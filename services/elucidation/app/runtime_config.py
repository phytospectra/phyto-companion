"""Elucidation service runtime configuration (ADR-0078).

The service must never answer a production request with mock science.
:class:`ElucidationConfig` makes the plane and the mock/real decision
explicit, and :meth:`ElucidationConfig.validate` turns a missing real
dependency into a startup failure instead of a silent fallback.

Plane semantics (matching ``auth.config.AuthConfig``):

- ``PHYTO_ENV=local`` (default): dry-run, mocks allowed.
- ``PHYTO_ENV=cloud``: real mode unless ``PHYTO_DRY_RUN=1`` is set
  explicitly (bring-up only, reported as such).
- Real mode requires ``VERTEX_ENDPOINT_ID`` and ``GOOGLE_CLOUD_PROJECT``;
  a missing value refuses startup rather than degrading to the mock.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["ElucidationConfig", "ElucidationConfigError"]


class ElucidationConfigError(RuntimeError):
    """Raised when a real deployment is missing required configuration."""


@dataclass(frozen=True)
class ElucidationConfig:
    env: str
    dry_run: bool
    vertex_endpoint_id: str | None
    gcp_project: str | None
    vertex_location: str

    @property
    def real_prediction(self) -> bool:
        return not self.dry_run

    @property
    def mode(self) -> str:
        """The human-facing mode label reported by ``/health``."""
        return "local-mock" if self.dry_run else "cloud-real"

    @classmethod
    def from_env(cls) -> "ElucidationConfig":
        env = os.getenv("PHYTO_ENV", "local")
        dry_run_default = env != "cloud"
        explicit = os.getenv("PHYTO_DRY_RUN")
        dry_run = dry_run_default if explicit is None else explicit == "1"
        return cls(
            env=env,
            dry_run=dry_run,
            vertex_endpoint_id=os.getenv("VERTEX_ENDPOINT_ID") or None,
            gcp_project=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            vertex_location=os.getenv("VERTEX_LOCATION", "us-central1"),
        )

    def validate(self) -> "ElucidationConfig":
        """Fail loudly when real mode lacks a real dependency.

        Called by the runtime at construction so a misconfigured cloud
        deployment stops at startup.  Dry-run deliberately skips the check:
        that is the local plane's contract.
        """
        if self.dry_run:
            return self
        missing = [
            name
            for name, value in (
                ("VERTEX_ENDPOINT_ID", self.vertex_endpoint_id),
                ("GOOGLE_CLOUD_PROJECT", self.gcp_project),
            )
            if not value
        ]
        if missing:
            raise ElucidationConfigError(
                "elucidation service is in real mode (PHYTO_ENV="
                f"{self.env!r}, PHYTO_DRY_RUN=0) but is missing required "
                f"configuration: {', '.join(missing)}. Refusing to start "
                "rather than serve mock shift predictions. Set the variables "
                "or run with PHYTO_DRY_RUN=1 on the local plane.",
            )
        return self
