"""Smoke tests for shareable read-only run URLs (ADR-0044).

The observable contract: a minted token verifies while fresh, is rejected when
expired or tampered with, and can never carry anything but read-only scope.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest

from phyto_shared.share import (
    SHARE_SCOPE,
    ExpiredShareTokenError,
    InMemoryShareRegistry,
    InvalidShareTokenError,
    ShareTokenManager,
    share_url_for,
)

_KEY = "test-share-key-at-least-16-chars"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _forge(key: str, *, scope: str, expires_in_days: int = 1) -> str:
    """Build a correctly signed token with an arbitrary scope."""
    now = datetime.now(timezone.utc)
    payload = {
        "run_id": "run-1",
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=expires_in_days)).timestamp()),
        "jti": "forged-jti",
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    signature = _b64(hmac.new(key.encode(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


class TestTokenLifecycle:
    def test_create_share_token_returns_a_token(self):
        manager = ShareTokenManager(_KEY)
        token, claims = manager.create_token("run-1")
        assert token
        assert "." in token
        assert claims.run_id == "run-1"
        assert claims.scope == SHARE_SCOPE

    def test_valid_unexpired_token_is_accepted(self):
        manager = ShareTokenManager(_KEY)
        token, claims = manager.create_token("run-1")
        verified = manager.verify(token)
        assert verified.run_id == "run-1"
        assert verified.token_id == claims.token_id
        assert verified.expires_at > datetime.now(timezone.utc)

    def test_expired_token_is_rejected(self):
        manager = ShareTokenManager(_KEY)
        issued = datetime.now(timezone.utc) - timedelta(days=5)
        token, _ = manager.create_token("run-1", expires_in_days=1, now=issued)
        with pytest.raises(ExpiredShareTokenError):
            manager.verify(token)

    def test_tampered_payload_is_rejected(self):
        manager = ShareTokenManager(_KEY)
        token, _ = manager.create_token("run-1")
        body, _sep, signature = token.partition(".")
        tampered = _b64(json.dumps(
            {"run_id": "run-2", "scope": "read_only", "iat": 0, "exp": 9999999999,
             "jti": "x"},
            sort_keys=True, separators=(",", ":"),
        ).encode())
        with pytest.raises(InvalidShareTokenError):
            manager.verify(f"{tampered}.{signature}")

    def test_token_signed_with_another_key_is_rejected(self):
        token = _forge("a-completely-different-key-1234", scope=SHARE_SCOPE)
        with pytest.raises(InvalidShareTokenError):
            ShareTokenManager(_KEY).verify(token)


class TestReadOnlyEnforcement:
    def test_scope_is_read_only_by_construction(self):
        _token, claims = ShareTokenManager(_KEY).create_token("run-1")
        assert claims.scope == "read_only"

    def test_a_correctly_signed_token_cannot_widen_scope(self):
        """Even a token signed with the real key is refused if scope is not
        read_only: the token can never grant approval or mutation rights."""
        widened = _forge(_KEY, scope="approve")
        with pytest.raises(InvalidShareTokenError):
            ShareTokenManager(_KEY).verify(widened)

    def test_token_binds_to_one_run_only(self):
        token, _ = ShareTokenManager(_KEY).create_token("run-1")
        claims = ShareTokenManager(_KEY).verify(token)
        assert claims.run_id == "run-1"


class TestRevocation:
    def test_revoked_token_is_rejected(self):
        registry = InMemoryShareRegistry()
        manager = ShareTokenManager(_KEY, registry)
        token, claims = manager.create_token("run-1", expires_in_days=30)
        assert registry.revoke_all("run-1") == 1
        from phyto_shared.share import RevokedShareTokenError

        with pytest.raises(RevokedShareTokenError):
            manager.verify(token)
        assert claims.token_id

    def test_share_url_shape(self):
        assert share_url_for("abc").endswith("/share/abc")
