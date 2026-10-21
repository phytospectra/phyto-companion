"""End-to-end metering behaviour against real service factories."""

from __future__ import annotations

import base64


from .service_runner import enforced_client

AUTH = {"Authorization": "Bearer mock:alice"}


def test_zero_balance_returns_402():
    client = enforced_client("elucidation", balance=10)
    response = client.post("/elucidate/novel", headers=AUTH, json={})
    assert response.status_code == 402
    body = response.json()
    assert set(body) >= {"balance", "required", "topup_url"}
    assert body["required"] == 50
    assert body["balance"] == 10


def test_successful_call_decrements_exactly_once():
    client = enforced_client("ingestion")
    before = client.tokens.get_balance("alice")
    response = client.post(
        "/ingest/peak_csv",
        headers=AUTH,
        json={"content_b64": _peak_csv(), "kind": "13C"},
    )
    assert response.status_code == 200
    assert client.tokens.get_balance("alice") == before - 1


def test_failed_handler_refunds():
    client = enforced_client("ingestion")
    before = client.tokens.get_balance("alice")
    response = client.post(
        "/ingest/peak_csv",
        headers=AUTH,
        json={"content_b64": base64.b64encode(b"not,a,valid\npeak,list").decode()},
    )
    assert response.status_code >= 400
    assert client.tokens.get_balance("alice") == before
    assert client.publisher.events[-1].status == "refunded"


def test_usage_event_published_on_confirm():
    client = enforced_client("ingestion")
    client.post(
        "/ingest/peak_csv",
        headers=AUTH,
        json={"content_b64": _peak_csv(), "kind": "13C"},
    )
    assert client.publisher.events
    event = client.publisher.events[-1]
    assert event.status == "confirmed"
    assert event.user_id == "alice"
    assert event.tokens_consumed == 1


def _peak_csv() -> str:
    return base64.b64encode(b"shift,intensity\n123.4,1000\n").decode()
