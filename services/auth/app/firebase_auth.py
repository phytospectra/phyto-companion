"""Firebase ID token verification with a deterministic mock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, status

from .config import AuthConfig


@dataclass(frozen=True)
class AuthenticatedUser:
    uid: str
    email: str | None
    claims: dict
    tenant_id: str | None = None


class FirebaseAuthClient(Protocol):
    def verify_token(self, token: str) -> AuthenticatedUser: ...


class RealFirebaseAuthClient:
    """Wraps Firebase Admin SDK. Lazy import so local tests skip it."""

    def __init__(self, credentials_path: str | None = None):
        try:
            import firebase_admin
            from firebase_admin import auth, credentials
        except ImportError as e:
            raise RuntimeError(
                "firebase-admin not installed. "
                "Install with: pip install firebase-admin"
            ) from e

        if not firebase_admin._apps:
            cred = (
                credentials.Certificate(credentials_path)
                if credentials_path
                else credentials.ApplicationDefault()
            )
            firebase_admin.initialize_app(cred)
        self._auth = auth

    def verify_token(self, token: str) -> AuthenticatedUser:
        try:
            decoded = self._auth.verify_id_token(token, check_revoked=True)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired token: {type(e).__name__}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from e
        return AuthenticatedUser(
            uid=decoded["uid"],
            email=decoded.get("email"),
            claims=decoded,
            tenant_id=decoded.get("firebase", {}).get("tenant"),
        )


class MockFirebaseAuthClient:
    """
    Accepts tokens of form 'mock:<uid>'. Rejects everything else.
    Deterministic; used for tests and dry-run mode.
    """

    def verify_token(self, token: str) -> AuthenticatedUser:
        if not token.startswith("mock:"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Mock auth requires 'mock:<uid>' token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        uid = token[len("mock:"):]
        if not uid or len(uid) > 128:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid mock uid",
            )
        return AuthenticatedUser(
            uid=uid,
            email=f"{uid}@mock.local",
            claims={"uid": uid, "mock": True},
        )


def get_firebase_client(config: AuthConfig) -> FirebaseAuthClient:
    if config.dry_run:
        return MockFirebaseAuthClient()
    return RealFirebaseAuthClient(config.firebase_credentials_path)
