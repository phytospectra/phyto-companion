"""Metabolomics service runtime configuration (extends ADR-0078's rule).

The service must never answer a production request with mock feature
detection. :class:`MetabolomicsConfig` makes the plane and the
mock/real decision explicit, and :meth:`MetabolomicsConfig.validate`
turns a missing real dependency into a startup failure instead of a
silent fallback.

Plane semantics (matching ``auth.config.AuthConfig`` and
``elucidation.runtime_config``):

- ``PHYTO_ENV=local`` (default): dry-run, mocks allowed.
- ``PHYTO_ENV=cloud``: real mode unless ``PHYTO_DRY_RUN=1`` is set
  explicitly (bring-up only, reported as such).
- Real mode requires ``MZMINE_HOME`` (a licensed local MZmine
  installation); a missing value refuses startup rather than degrading
  to the mock. NOTE: the MZmine batch contract itself is still an
  unverified integration (see ``feature_detection.MZmineDetector``) --
  until that contract is validated, real mode is fail-closed at startup
  but must not be advertised as a validated LC-MS processing path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["MetabolomicsConfig", "MetabolomicsConfigError"]


class MetabolomicsConfigError(RuntimeError):
    """Raised when a real deployment is missing required configuration."""


@dataclass(frozen=True)
class MetabolomicsConfig:
    env: str
    dry_run: bool
    mzmine_home: str | None

    @property
    def real_detection(self) -> bool:
        return not self.dry_run

    @property
    def mode(self) -> str:
        """The human-facing mode label reported by ``/health``."""
        return "local-mock" if self.dry_run else "cloud-real"

    @classmethod
    def from_env(cls) -> "MetabolomicsConfig":
        env = os.getenv("PHYTO_ENV", "local")
        dry_run_default = env != "cloud"
        explicit = os.getenv("PHYTO_DRY_RUN")
        dry_run = dry_run_default if explicit is None else explicit == "1"
        return cls(
            env=env,
            dry_run=dry_run,
            mzmine_home=os.getenv("MZMINE_HOME") or None,
        )

    def validate(self) -> "MetabolomicsConfig":
        """Fail loudly when real mode lacks a real dependency.

        Called by the runtime at construction so a misconfigured cloud
        deployment stops at startup.  Dry-run deliberately skips the check:
        that is the local plane's contract.
        """
        if self.dry_run:
            return self
        if not self.mzmine_home:
            raise MetabolomicsConfigError(
                "metabolomics service is in real mode (PHYTO_ENV="
                f"{self.env!r}, PHYTO_DRY_RUN=0) but MZMINE_HOME is not "
                "set. Refusing to start rather than serve mock feature "
                "detection. Point MZMINE_HOME at a licensed local MZmine "
                "installation or run with PHYTO_DRY_RUN=1 on the local "
                "plane.",
            )
        return self
