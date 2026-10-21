"""AuthMiddleware: 401/402/503, reserve-confirm, idempotency."""

from __future__ import annotations

from .helpers import AUTH, build_app
from fastapi.testclient import TestClient


def test_health_requires_no_auth(client):
    assert client.get("/health").status_code == 200


def test_missing_auth_returns_401(client):
    response = client.post("/elucidate")
    assert (response.status_code, response.headers["WWW-Authenticate"]) == (401, "Bearer")


def test_reserve_and_confirm_on_success(client, publisher, tokens):
    assert client.post("/elucidate", headers=AUTH).status_code == 200
    assert tokens.get_balance("alice") == 10  # 30 - 20
    assert len(publisher.events) == 1
    event = publisher.events[0]
    assert (event.status, event.user_id, event.tokens_consumed) == ("confirmed", "alice", 20)


def test_insufficient_returns_402(client):
    client.post("/elucidate", headers=AUTH)  # 30 -> 10
    response = client.post("/llm/hypothesize", headers=AUTH)
    assert response.status_code == 402
    body = response.json()
    assert (body["balance"], body["required"]) == (10, 25)
    assert body["topup_url"]


def test_idempotency_header(client, tokens):
    headers = {**AUTH, "Idempotency-Key": "same-key-00000001"}
    client.post("/elucidate", headers=headers)
    client.post("/elucidate", headers=headers)
    assert tokens.get_balance("alice") == 10  # charged exactly once


def test_unpriced_route_returns_503(client):
    assert client.post("/unpriced", headers=AUTH).status_code == 503


def test_bearer_prefix_case_sensitivity(client):
    response = client.post("/elucidate", headers={"Authorization": "bearer mock:alice"})
    assert response.status_code == 401


def test_short_idempotency_key_rejected(client):
    headers = {**AUTH, "Idempotency-Key": "tiny"}
    assert client.post("/elucidate", headers=headers).status_code == 400


def test_zero_cost_route_authenticates_without_reserving(publisher, tokens):
    # /runs/ is a zero-cost prefix route: user attached, nothing held.
    body = TestClient(build_app(publisher, tokens)).get("/runs/abc", headers=AUTH).json()
    assert body == {"reservation_id": None, "user": "alice"}
