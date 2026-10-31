"""Networking service runtime configuration (extends ADR-0078's rule).

``get_gnps_client`` used to return ``MockGNPSClient`` whenever no API
key was set and public GNPS was not explicitly allowed -- on any plane.
A cloud deployment without credentials therefore returned fabricated
job handles and fake network summaries while looking identical to a
real submission. The fail-closed rule instead: real mode (cloud, no
dry-run) requires an authorized access route -- ``GNPS_API_KEY`` or an
explicit ``PHYTO_ALLOW_PUBLIC_GNPS=1`` -- and refuses startup without
one; the mock stays a dry-run-only tool.

Plane semantics (matching ``auth.config.AuthConfig``):
- ``PHYTO_ENV=local`` (default): dry-run, mocks allowed.
- ``PHYTO_ENV=cloud``: real mode unless ``PHYTO_DRY_RUN=1`` is set
  explicitly (bring-up only, reported as such).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["NetworkingConfig", "NetworkingConfigError"]


class NetworkingConfigError(RuntimeError):
    """Raised when a real deployment lacks an authorized GNPS route."""


@dataclass(frozen=True)
class NetworkingConfig:
    env: str
    dry_run: bool
    gnps_api_key: str | None
    allow_public_gnps: bool

    @property
    def real_submission(self) -> bool:
        return not self.dry_run

    @property
    def mode(self) -> str:
        """The human-facing mode label reported by ``/health``."""
        return "local-mock" if self.dry_run else "cloud-real"

    @classmethod
    def from_env(cls) -> "NetworkingConfig":
        env = os.getenv("PHYTO_ENV", "local")
        dry_run_default = env != "cloud"
        explicit = os.getenv("PHYTO_DRY_RUN")
        dry_run = dry_run_default if explicit is None else explicit == "1"
        return cls(
            env=env,
            dry_run=dry_run,
            gnps_api_key=os.getenv("GNPS_API_KEY") or None,
            allow_public_gnps=os.getenv("PHYTO_ALLOW_PUBLIC_GNPS") == "1",
        )

    def validate(self) -> "NetworkingConfig":
        """Fail loudly when real mode lacks an authorized GNPS route.

        Called by the runtime at construction so a misconfigured cloud
        deployment stops at startup.  Dry-run deliberately skips the
        check: that is the local plane's contract.
        """
        if self.dry_run:
            return self
        if not (self.gnps_api_key or self.allow_public_gnps):
            raise NetworkingConfigError(
                "networking service is in real mode (PHYTO_ENV="
                f"{self.env!r}, PHYTO_DRY_RUN=0) but has no authorized "
                "GNPS access route: set GNPS_API_KEY, or explicitly opt "
                "in to the public GNPS instance with "
                "PHYTO_ALLOW_PUBLIC_GNPS=1 (public submissions carry "
                "data outside the tenancy boundary). Refusing to start "
                "rather than serve fabricated job handles. Run with "
                "PHYTO_DRY_RUN=1 for the mock plane.",
            )
        return self
