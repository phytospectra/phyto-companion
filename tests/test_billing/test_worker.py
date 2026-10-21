"""Billing worker: dedup, malformed handling, ledger writes."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from billing.main import BillingConfig, InMemoryLedgerWriter, create_app


def _event(event_id: str = "evt-1") -> dict:
    return {
        "event_id": event_id,
        "reservation_id": "res-1",
        "user_id": "uid-1",
        "operation": "POST /elucidate",
        "tokens_consumed": 20,
        "status": "confirmed",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": 12,
    }


def _envelope(payload: str | bytes) -> dict:
    raw = payload.encode() if isinstance(payload, str) else payload
    return {"message": {"data": base64.b64encode(raw).decode(), "message_id": "m-1"}}


@pytest.fixture()
def ledger() -> InMemoryLedgerWriter:
    return InMemoryLedgerWriter()


@pytest.fixture()
def client(ledger) -> TestClient:
    return TestClient(create_app(BillingConfig(dry_run=True), ledger=ledger))


def test_duplicate_event_is_deduped(client, ledger):
    body = _envelope(json.dumps(_event()))
    first = client.post("/pubsub/push", json=body)
    second = client.post("/pubsub/push", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == {"status": "ok", "duplicate": False}
    assert second.json() == {"status": "ok", "duplicate": True}
    assert len(ledger.all_events()) == 1


def test_malformed_returns_200(client):
    # Decodes as base64 but is not JSON.
    response = client.post("/pubsub/push", json=_envelope("not json"))
    assert response.status_code == 200
    assert response.json()["status"] == "malformed"


def test_bad_base64_returns_200(client):
    response = client.post("/pubsub/push", json={"message": {"data": "!!!not-base64!!!"}})
    assert response.status_code == 200
    assert response.json()["status"] == "malformed"


def test_schema_mismatch_returns_200(client):
    response = client.post("/pubsub/push", json=_envelope(json.dumps({"event_id": "x"})))
    assert response.status_code == 200
    assert response.json()["status"] == "malformed"


def test_valid_event_writes_ledger(client, ledger):
    response = client.post("/pubsub/push", json=_envelope(json.dumps(_event("evt-42"))))
    assert response.json() == {"status": "ok", "duplicate": False}
    assert [e.event_id for e in ledger.all_events()] == ["evt-42"]


def test_ledger_write_failure_returns_500():
    class BrokenLedger:
        def write(self, event):
            raise RuntimeError("firestore unavailable")

    app = create_app(BillingConfig(dry_run=True), ledger=BrokenLedger())
    response = TestClient(app, raise_server_exceptions=False).post(
        "/pubsub/push", json=_envelope(json.dumps(_event()))
    )
    assert response.status_code == 500


def test_health_reports_dry_run(client):
    body = client.get("/health").json()
    assert body["service"] == "billing"
    assert body["dry_run"] is True
