"""Build a real service app with the token layer enforced."""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

from auth.config import AuthConfig
from auth.firebase_auth import MockFirebaseAuthClient
from auth.middleware import AuthMiddleware
from auth.token_balance import MockTokenBalanceClient


class RecordingPublisher:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def enforced_client(service: str, *, balance: int = 1000):
    """Return a TestClient for ``service`` with auth enforced and mocks wired."""
    module = importlib.import_module(f"{service}.main")
    cfg = AuthConfig(
        project_id="test-project",
        firebase_credentials_path=None,
        dry_run=True,
        default_free_tokens=50,
        enabled=True,
    )
    app = module.create_app(cfg)
    tokens = MockTokenBalanceClient({"alice": balance})
    publisher = RecordingPublisher()
    for middleware in app.user_middleware:
        if middleware.cls is AuthMiddleware:
            middleware.kwargs["firebase_client"] = MockFirebaseAuthClient()
            middleware.kwargs["token_client"] = tokens
            middleware.kwargs["publisher"] = publisher
    client = TestClient(app)
    client.tokens = tokens
    client.publisher = publisher
    return client
