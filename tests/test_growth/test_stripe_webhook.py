"""Stripe webhook authentication (ADR-0047 follow-up).

The webhook is the only public endpoint that can grant a paid plan, so the
signature is its entire credential.  These tests drive the HMAC path Stripe
itself uses -- ``t=<ts>,v1=<hmac>`` over ``"{ts}.{payload}"`` -- with a local
test secret.  No Stripe account, no network, no real webhook secret.
"""

from __future__ import annotations

import json
import time

import pytest
import stripe
from fastapi.testclient import TestClient

from auth import main as auth_main
from auth.config import AuthConfig
from auth.entitlements import MockEntitlementsClient
from auth.token_balance import MockTokenBalanceClient

# Test-only key: 32 hex chars after the whsec_ prefix, as Stripe's are.
SECRET = "whsec_test_0123456789abcdef0123456789abcdef"

EVENT = {
    "id": "evt_sig_1",
    "type": "checkout.session.completed",
    "data": {
        "object": {
            "id": "cs_test_sig_1",
            "amount_total": 1500,
            "metadata": {"user_id": "alice", "plan": "pro"},
        }
    },
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    tokens = MockTokenBalanceClient({"alice": 1000})
    entitlements = MockEntitlementsClient(ledger=tokens)
    app = auth_main.create_app(
        AuthConfig(
            project_id="test-project",
            firebase_credentials_path=None,
            dry_run=True,
            default_free_tokens=50,
            enabled=True,
        ),
        token_client=tokens,
        entitlements=entitlements,
        audit_path=str(tmp_path / "audit.jsonl"),
    )
    app.state.entitlements = entitlements
    return TestClient(app)


def _header(payload: str, *, secret: str = SECRET, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    signature = stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", secret)
    return f"t={ts},v1={signature}"


def _body(event=EVENT) -> str:
    # One canonical serialization so header and body cover identical bytes.
    return json.dumps(event, separators=(",", ":"))


def _post(client, payload: str, signature: str | None):
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["Stripe-Signature"] = signature
    return client.post("/billing/webhook", content=payload, headers=headers)


class TestSignatureVerification:
    def test_missing_signature_header_is_rejected(self, client):
        response = _post(client, _body(), None)
        assert response.status_code == 400
        assert "Stripe-Signature" in response.json()["detail"]

    def test_invalid_signature_is_rejected(self, client):
        payload = _body()
        # Signed with a different key: structurally valid, cryptographically wrong.
        response = _post(
            client, payload, _header(payload, secret="whsec_test_ffffffffffffffff")
        )
        assert response.status_code == 400
        assert "signature" in response.json()["detail"].lower()

    def test_valid_signature_is_accepted_and_processed(self, client):
        payload = _body()
        response = _post(client, payload, _header(payload))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        assert client.app.state.entitlements.status("alice").plan == "pro"

    def test_valid_signature_over_malformed_payload_is_rejected(self, client):
        payload = "{not json at all"
        # Sign the malformed bytes, so this isolates payload parsing from
        # signature checking rather than accidentally testing both.
        response = _post(client, payload, _header(payload))
        assert response.status_code == 400
