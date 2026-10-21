"""Auth service configuration."""

import os
from dataclasses import dataclass


def _is_cloud() -> bool:
    return os.getenv("PHYTO_ENV") == "cloud"


@dataclass(frozen=True)
class AuthConfig:
    project_id: str
    firebase_credentials_path: str | None
    dry_run: bool
    default_free_tokens: int
    enabled: bool

    @classmethod
    def from_env(cls) -> "AuthConfig":
        cloud = _is_cloud()

        # Enforcement is on wherever real users exist (cloud) and off on the
        # local plane, which has no Firebase credentials and where the test
        # suite drives endpoints directly. PHYTO_AUTH_ENABLED overrides both.
        explicit_enabled = os.getenv("PHYTO_AUTH_ENABLED")
        enabled = cloud if explicit_enabled is None else explicit_enabled == "1"

        # Local is dry-run by default so no GCP client is ever constructed.
        explicit_dry_run = os.getenv("PHYTO_DRY_RUN")
        dry_run = (not cloud) if explicit_dry_run is None else explicit_dry_run == "1"

        return cls(
            project_id=os.getenv(
                "GOOGLE_CLOUD_PROJECT", "phyto-companion-prod"
            ),
            firebase_credentials_path=(
                os.getenv("FIREBASE_CREDENTIALS_PATH") or None
            ),
            dry_run=dry_run,
            default_free_tokens=int(os.getenv("PHYTO_FREE_TOKENS", "50")),
            enabled=enabled,
        )
