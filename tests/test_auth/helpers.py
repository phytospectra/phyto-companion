"""Test doubles shared by the auth middleware tests."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth.firebase_auth import MockFirebaseAuthClient
from auth.middleware import AuthMiddleware
from phyto_shared.usage_events import UsageEvent

AUTH = {"Authorization": "Bearer mock:alice"}


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def publish(self, event: UsageEvent) -> None:
        self.events.append(event)


class ExplodingPublisher:
    def publish(self, event: UsageEvent) -> None:
        raise RuntimeError("pubsub down")


def build_app(publisher, tokens) -> FastAPI:
    """A minimal metered app: priced, unpriced and zero-cost routes."""
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware,
        firebase_client=MockFirebaseAuthClient(),
        token_client=tokens,
        publisher=publisher,
    )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/elucidate")
    def elucidate():
        return {"ok": True}

    @app.post("/unpriced")
    def unpriced():
        return {"ok": True}

    @app.get("/runs/{run_id}")
    def read_run(run_id: str, request: Request):
        return {"reservation_id": request.state.reservation_id, "user": request.state.user.uid}

    return app
