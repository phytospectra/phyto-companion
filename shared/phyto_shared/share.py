"""Shareable read-only run URLs (ADR-0044).

A colleague can open a run's evidence panel without an account.  This
spreads usage within teams at zero acquisition cost, and the shared view
carries a CTA back into the app.

Token design:

* HMAC-SHA256 signed, time-limited, ``scope="read_only"`` by construction.
* Signed with ``SHARE_HMAC_KEY`` (Secret Manager -> env, ADR-0032) --
  a separate, lower-privilege credential from the Firebase token.  A
  share token grants READ on ONE run; it can never approve a checkpoint,
  mutate anything, or read another run.
* Default expiry 30 days, configurable up to 365.
* Revocation by token id (``jti``); revocation entries live no longer
  than the token they kill (TTL = expiry), so the revocation list cannot
  grow without bound.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

__all__ = [
    "ShareClaims",
    "ShareError",
    "ExpiredShareTokenError",
    "RevokedShareTokenError",
    "InvalidShareTokenError",
    "DEFAULT_SHARE_EXPIRY_DAYS",
    "MAX_SHARE_EXPIRY_DAYS",
    "SHARE_SCOPE",
    "ShareTokenManager",
    "ShareRegistry",
    "InMemoryShareRegistry",
    "share_url_for",
    "app_base_url",
]

SHARE_SCOPE = "read_only"
DEFAULT_SHARE_EXPIRY_DAYS = 30
MAX_SHARE_EXPIRY_DAYS = 365

_DEV_HMAC_KEY = "phyto-dev-share-key-not-for-production"
"""Dry-run default so the local plane can mint and verify share tokens
without Secret Manager.  Production requires SHARE_HMAC_KEY; using the
dev key there would let anyone forge tokens, so ``from_env`` refuses."""


class ShareError(Exception):
    """Base class for share-token failures."""


class InvalidShareTokenError(ShareError):
    """Malformed signature or payload."""


class ExpiredShareTokenError(ShareError):
    """The token's expiry has passed."""


class RevokedShareTokenError(ShareError):
    """The token was revoked before its expiry."""


@dataclass(frozen=True)
class ShareClaims:
    """What a share token authorizes: read one run, nothing else."""

    run_id: str
    scope: str
    expires_at: datetime
    issued_at: datetime
    token_id: str


def app_base_url() -> str:
    return os.environ.get("PHYTO_APP_BASE_URL", "https://app.phytospectra.com")


def share_url_for(token: str) -> str:
    return f"{app_base_url()}/share/{token}"


class ShareRegistry(Protocol):
    """Tracks issued share tokens and their revocations."""

    def register(self, run_id: str, token_id: str, expires_at: datetime) -> None: ...
    def revoke_all(self, run_id: str) -> int:
        """Revoke every still-active token for ``run_id``; return the count."""
    def is_revoked(self, token_id: str, *, now: datetime | None = None) -> bool: ...


class InMemoryShareRegistry:
    """Process-local registry (tests, dry-run, single-instance local plane)."""

    def __init__(self) -> None:
        self._issued: dict[str, dict] = {}  # token_id -> {run_id, expires_at}
        self._revoked: dict[str, datetime] = {}  # token_id -> revoked-until (expiry)

    def register(self, run_id: str, token_id: str, expires_at: datetime) -> None:
        self._issued[token_id] = {"run_id": run_id, "expires_at": expires_at}

    def revoke_all(self, run_id: str) -> int:
        count = 0
        for token_id, entry in self._issued.items():
            if entry["run_id"] != run_id:
                continue
            if entry["expires_at"] <= datetime.now(timezone.utc):
                continue  # already expired; nothing to revoke
            self._revoked[token_id] = entry["expires_at"]
            count += 1
        return count

    def is_revoked(self, token_id: str, *, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        until = self._revoked.get(token_id)
        # TTL = expiry: an entry for an already-expired token is garbage.
        return until is not None and moment < until

    def active_tokens(self, run_id: str) -> list[str]:
        moment = datetime.now(timezone.utc)
        return [
            token_id
            for token_id, entry in self._issued.items()
            if entry["run_id"] == run_id and entry["expires_at"] > moment
        ]


class FirestoreShareRegistry:
    """Production registry.

    Collections (both carry ``expires_at`` so TTL policies can garbage-
    collect them; a revocation never outlives the token it kills):

      ``share_tokens/{token_id}``  -- {run_id, expires_at}
      ``share_revocations/{token_id}`` -- {run_id, expires_at}
    """

    def __init__(self, client=None, project_id: str | None = None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._db = client if client is not None else firestore.Client(project=project_id)

    def register(self, run_id: str, token_id: str, expires_at: datetime) -> None:
        self._db.collection("share_tokens").document(token_id).set(
            {"run_id": run_id, "expires_at": expires_at}
        )

    def revoke_all(self, run_id: str) -> int:
        now = datetime.now(timezone.utc)
        tokens = (
            self._db.collection("share_tokens")
            .where("run_id", "==", run_id)
            .where("expires_at", ">", now)
            .stream()
        )
        count = 0
        for snap in tokens:
            self._db.collection("share_revocations").document(snap.id).set(
                snap.to_dict()
            )
            count += 1
        return count

    def is_revoked(self, token_id: str, *, now: datetime | None = None) -> bool:
        snap = self._db.collection("share_revocations").document(token_id).get()
        if not snap.exists:
            return False
        expires_at = snap.to_dict().get("expires_at")
        moment = now or datetime.now(timezone.utc)
        return bool(expires_at) and moment < expires_at


class ShareTokenManager:
    """Mints and verifies HMAC-signed, read-only, time-limited tokens.

    The token is ``base64url(payload).base64url(hmac)`` where the payload
    is canonical JSON: {run_id, scope, iat, exp, jti}.  The signature
    covers the payload byte-for-byte, so any edit invalidates it.
    """

    def __init__(self, key: str, registry: ShareRegistry | None = None):
        if not key or len(key) < 16:
            raise ValueError("SHARE_HMAC_KEY must be at least 16 characters")
        self._key = key.encode("utf-8")
        self.registry = registry

    @classmethod
    def from_env(cls, registry: ShareRegistry | None = None) -> "ShareTokenManager":
        """Build from the environment; dry-run falls back to the dev key."""
        key = os.environ.get("SHARE_HMAC_KEY")
        if not key and os.environ.get("PHYTO_DRY_RUN", "1") == "1":
            key = _DEV_HMAC_KEY
        if not key:
            raise RuntimeError(
                "SHARE_HMAC_KEY is not configured; share URLs cannot be "
                "minted safely (fetch it from Secret Manager, ADR-0032)"
            )
        return cls(key, registry)

    # -- minting ------------------------------------------------------------
    def create_token(
        self,
        run_id: str,
        *,
        expires_in_days: int = DEFAULT_SHARE_EXPIRY_DAYS,
        now: datetime | None = None,
    ) -> tuple[str, ShareClaims]:
        """Mint a read-only token for ``run_id``.

        Returns ``(token, claims)``.  ``expires_in_days`` is clamped to
        [1, 365]: the ceiling exists so a leaked token cannot grant read
        access forever.
        """
        if not 1 <= expires_in_days <= MAX_SHARE_EXPIRY_DAYS:
            raise ValueError(
                f"expires_in_days must be between 1 and {MAX_SHARE_EXPIRY_DAYS}"
            )
        issued = now or datetime.now(timezone.utc)
        expires = issued + timedelta(days=expires_in_days)
        payload = {
            "run_id": run_id,
            "scope": SHARE_SCOPE,
            "iat": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": secrets.token_urlsafe(12),
        }
        body = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = _b64encode(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        token = f"{body}.{signature}"
        if self.registry is not None:
            self.registry.register(run_id, payload["jti"], expires)
        claims = ShareClaims(
            run_id=run_id,
            scope=SHARE_SCOPE,
            issued_at=issued,
            expires_at=expires,
            token_id=payload["jti"],
        )
        return token, claims

    # -- verification ---------------------------------------------------------
    def verify(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> ShareClaims:
        """Verify signature, expiry and revocation; raise on any failure."""
        body, separator, signature = token.partition(".")
        if not separator or not body or not signature:
            raise InvalidShareTokenError("malformed share token")
        expected = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        try:
            provided = _b64decode(signature)
        except ValueError as exc:
            raise InvalidShareTokenError("malformed share token signature") from exc
        if not hmac.compare_digest(expected, provided):
            raise InvalidShareTokenError("share token signature mismatch")
        try:
            payload = json.loads(_b64decode(body))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidShareTokenError("share token payload is not valid JSON") from exc

        moment = now or datetime.now(timezone.utc)
        if int(payload.get("exp", 0)) <= moment.timestamp():
            raise ExpiredShareTokenError("share token has expired")
        if payload.get("scope") != SHARE_SCOPE:
            raise InvalidShareTokenError(
                f"share token scope must be {SHARE_SCOPE!r}"
            )
        claims = ShareClaims(
            run_id=str(payload["run_id"]),
            scope=SHARE_SCOPE,
            issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=timezone.utc),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc),
            token_id=str(payload["jti"]),
        )
        if self.registry is not None and self.registry.is_revoked(
            claims.token_id, now=moment
        ):
            raise RevokedShareTokenError("share token was revoked")
        return claims


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)
