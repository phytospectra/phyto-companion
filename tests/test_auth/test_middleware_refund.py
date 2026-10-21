"""A failed priced handler must return the reservation; audit is advisory."""

from __future__ import annotations

from .helpers import AUTH, ExplodingPublisher, FakePublisher
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth.firebase_auth import MockFirebaseAuthClient
from auth.middleware import AuthMiddleware
from auth.token_balance import MockTokenBalanceClient


def _client(handler, tokens, publisher) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware,
        firebase_client=MockFirebaseAuthClient(),
        token_client=tokens,
        publisher=publisher,
    )
    app.post("/elucidate")(handler)
    return TestClient(app, raise_server_exceptions=False)


def test_refund_on_http_500():
    tokens, publisher = MockTokenBalanceClient({"alice": 30}), FakePublisher()

    def boom():
        raise HTTPException(status_code=500, detail="boom")

    response = _client(boom, tokens, publisher).post("/elucidate", headers=AUTH)
    assert response.status_code == 500
    assert tokens.get_balance("alice") == 30  # fully refunded
    assert publisher.events[0].status == "refunded"
    assert publisher.events[0].tokens_consumed == 0


def test_refund_on_unhandled_exception():
    tokens, publisher = MockTokenBalanceClient({"alice": 30}), FakePublisher()

    def crash():
        raise RuntimeError("unexpected")

    response = _client(crash, tokens, publisher).post("/elucidate", headers=AUTH)
    assert response.status_code == 500
    assert tokens.get_balance("alice") == 30
    assert publisher.events[0].status == "refunded"


def test_publisher_failure_does_not_fail_request():
    tokens = MockTokenBalanceClient({"alice": 30})
    response = _client(lambda: {"ok": True}, tokens, ExplodingPublisher()).post(
        "/elucidate", headers=AUTH
    )
    assert response.status_code == 200
    assert tokens.get_balance("alice") == 10  # charged, despite audit failure
