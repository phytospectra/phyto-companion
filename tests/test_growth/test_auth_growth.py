"""Auth-service growth surfaces: free-tier allowance, plans, billing,
workspaces, cohort signup (ADR-0045/0047/0048).

Enforcement is on, everything is offline: mock Firebase, in-process
ledger, mock Stripe.  No network, no GCP credentials.
"""

from __future__ import annotations

import json
import time

import pytest
import stripe
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth.config import AuthConfig
from auth.entitlements import MockEntitlementsClient
from auth.middleware import AuthMiddleware
from auth.pubsub_publisher import LoggingPublisher
from auth.token_balance import MockTokenBalanceClient
from auth.workspaces import InMemoryWorkspaceStore

AUTH = {"Authorization": "Bearer mock:alice"}

# Test-only signing key. It is not a real Stripe secret and cannot verify a
# real delivery; it exists so the tests exercise the same HMAC path Stripe
# uses without touching an account.
WEBHOOK_SECRET = "whsec_test_0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    """Every app built in this module verifies against the test secret."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)


def _signed_post(client, event, *, secret: str = WEBHOOK_SECRET):
    """POST an event the way Stripe does: raw body plus an HMAC header."""
    payload = json.dumps(event, separators=(",", ":"))
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload}", secret
    )
    return client.post(
        "/billing/webhook",
        content=payload,
        headers={
            "Stripe-Signature": f"t={timestamp},v1={signature}",
            "Content-Type": "application/json",
        },
    )


def _config(**overrides) -> AuthConfig:
    values = dict(
        project_id="test-project",
        firebase_credentials_path=None,
        dry_run=True,
        default_free_tokens=50,
        enabled=True,
    )
    values.update(overrides)
    return AuthConfig(**values)


def _auth_client(
    *,
    tokens: MockTokenBalanceClient | None = None,
    entitlements: MockEntitlementsClient | None = None,
    workspaces: InMemoryWorkspaceStore | None = None,
    tmp_path=None,
):
    from auth import main as auth_main

    tokens = tokens or MockTokenBalanceClient({"alice": 1000})
    entitlements = entitlements or MockEntitlementsClient(ledger=tokens)
    workspaces = workspaces or InMemoryWorkspaceStore()
    app = auth_main.create_app(
        _config(),
        token_client=tokens,
        entitlements=entitlements,
        workspaces=workspaces,
        audit_path=str(tmp_path / "auth_audit.jsonl") if tmp_path else None,
    )
    client = TestClient(app)
    client.tokens = tokens
    client.entitlements = entitlements
    client.workspaces = workspaces
    return client


@pytest.fixture()
def client(tmp_path):
    return _auth_client(tmp_path=tmp_path)


def _metered_client(
    handler=None,
    *,
    tokens: MockTokenBalanceClient | None = None,
    entitlements: MockEntitlementsClient | None = None,
    workspaces: InMemoryWorkspaceStore | None = None,
):
    """A minimal app with one priced route, for allowance/pool behaviour.

    The auth service's own routes are all zero-cost account surfaces; the
    metered path is ``POST /elucidate`` (20 tokens), which is what a
    frees-tier user's first real analysis costs.
    """
    from auth.firebase_auth import MockFirebaseAuthClient

    tokens = tokens or MockTokenBalanceClient({"alice": 1000})
    entitlements = entitlements or MockEntitlementsClient(ledger=tokens)
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware,
        firebase_client=MockFirebaseAuthClient(),
        token_client=tokens,
        publisher=LoggingPublisher(),
        entitlements=entitlements,
        workspaces=workspaces,
    )
    app.post("/elucidate")(handler or (lambda: {"ok": True}))
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.tokens = tokens
    test_client.entitlements = entitlements
    return test_client


# ---------------------------------------------------------------------------
# Free-tier allowance (ADR-0045)
# ---------------------------------------------------------------------------


class TestFreeTierAllowance:
    def test_balance_endpoint_reports_allowance_state(self, client):
        body = client.get("/billing/balance", headers=AUTH).json()
        assert body["plan"] == "free"
        assert body["monthly_allowance"] == 50
        assert body["used_this_month"] == 0
        assert body["balance"] == 1000
        assert body["resets_at"]

    def test_allowance_blocks_before_the_balance_runs_out(self):
        # /elucidate costs 20; the free allowance is 50.  With 1000 tokens
        # in the balance, the third call must still be refused: the
        # allowance is the binding monthly cap (ADR-0045).
        client = _metered_client()
        for _ in range(2):
            assert client.post("/elucidate", headers=AUTH).status_code == 200
        response = client.post("/elucidate", headers=AUTH)
        assert response.status_code == 402
        body = response.json()
        assert body["required"] == 20
        assert body["balance"] == 10  # remaining allowance, not ledger balance
        assert body["topup_url"] == "/billing/topup"
        assert client.tokens.get_balance("alice") == 960

    def test_allowance_is_consumed_on_success_only(self):
        client = _metered_client()
        client.post("/elucidate", headers=AUTH)
        assert client.entitlements.status("alice").used_this_month == 20

    def test_plan_change_raises_the_allowance(self, client):
        client.entitlements.set_plan("alice", "pro")
        body = client.get("/billing/balance", headers=AUTH).json()
        assert body["plan"] == "pro"
        assert body["monthly_allowance"] == 2000


class TestAllowanceRefund:
    """A refunded call must never erase real usage from earlier calls."""

    def test_failed_call_does_not_decrement_the_allowance(self):
        tokens = MockTokenBalanceClient({"alice": 1000})
        entitlements = MockEntitlementsClient(ledger=tokens)
        calls = {"n": 0}

        def handler():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True}
            raise HTTPException(status_code=500, detail="boom")

        client = _metered_client(handler, tokens=tokens, entitlements=entitlements)
        assert client.post("/elucidate", headers=AUTH).status_code == 200
        assert entitlements.status("alice").used_this_month == 20
        assert client.post("/elucidate", headers=AUTH).status_code == 500
        # The refund returns the hold; it must not touch the month's usage.
        assert tokens.get_balance("alice") == 980
        assert entitlements.status("alice").used_this_month == 20

    def test_cache_hit_is_free_and_does_not_touch_the_allowance(self):
        from fastapi.responses import JSONResponse

        tokens = MockTokenBalanceClient({"alice": 1000})
        entitlements = MockEntitlementsClient(ledger=tokens)

        def handler():
            return JSONResponse({"ok": True}, headers={"X-Cache": "HIT"})

        client = _metered_client(handler, tokens=tokens, entitlements=entitlements)
        assert client.post("/elucidate", headers=AUTH).status_code == 200
        # A cache hit charges neither the ledger nor the allowance.
        assert tokens.get_balance("alice") == 1000
        assert entitlements.status("alice").used_this_month == 0


# ---------------------------------------------------------------------------
# Plans / billing (ADR-0047)
# ---------------------------------------------------------------------------


class TestBilling:
    def test_plan_catalogue_is_public(self, client):
        response = client.get("/billing/plans")
        assert response.status_code == 200
        names = [plan["name"] for plan in response.json()["plans"]]
        assert names == ["free", "pro", "lab", "department", "enterprise"]
        free = response.json()["plans"][0]["features"]
        assert free["monthly_tokens"] == 50
        assert free["distilled_llm"] is True

    def test_subscribe_returns_a_checkout_session(self, client):
        response = client.post(
            "/billing/subscribe", headers=AUTH, json={"plan": "pro"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["checkout_url"].startswith("https://checkout.stripe.com/")
        assert body["session_id"].startswith("cs_mock_")
        assert body["plan"] == "pro"

    def test_subscribe_rejects_unsubscribable_plans(self, client):
        assert (
            client.post(
                "/billing/subscribe", headers=AUTH, json={"plan": "free"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/billing/subscribe", headers=AUTH, json={"plan": "enterprise"}
            ).status_code
            == 422
        )

    def test_webhook_grants_the_plan_and_emits_plan_changed(self, client):
        event = {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "amount_total": 1500,
                    "metadata": {"user_id": "alice", "plan": "pro"},
                }
            },
        }
        response = _signed_post(client, event)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert client.entitlements.status("alice").plan == "pro"

        records = client.app.state.audit_log.records()
        changes = [r for r in records if r.get("event") == "plan_changed"]
        assert len(changes) == 1
        assert changes[0]["detail"]["old_plan"] == "free"
        assert changes[0]["detail"]["new_plan"] == "pro"
        assert changes[0]["detail"]["mrr_usd"] == 15.0

    def test_webhook_is_idempotent_per_session(self, client):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_2",
                    "amount_total": 1500,
                    "metadata": {"user_id": "alice", "plan": "pro"},
                }
            },
        }
        first = _signed_post(client, event).json()
        second = _signed_post(client, event).json()
        assert first["duplicate"] is False
        assert second["duplicate"] is True
        changes = [
            r
            for r in client.app.state.audit_log.records()
            if r.get("event") == "plan_changed"
        ]
        assert len(changes) == 1

    def test_webhook_ignores_other_event_types(self, client):
        response = _signed_post(
            client, {"type": "invoice.paid", "data": {"object": {}}}
        )
        assert response.json()["status"] == "ignored"

    def test_webhook_malformed_session_is_acknowledged(self, client):
        response = _signed_post(
            client, {"type": "checkout.session.completed", "data": {"object": {}}}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "malformed"

    def test_api_keys_require_pro(self, client):
        assert client.post("/billing/api-keys", headers=AUTH).status_code == 403
        client.entitlements.set_plan("alice", "pro")
        created = client.post(
            "/billing/api-keys", headers=AUTH, params={"label": "ci"}
        )
        assert created.status_code == 200
        body = created.json()
        assert body["api_key"].startswith("pk_")
        assert body["label"] == "ci"
        listed = client.get("/billing/api-keys", headers=AUTH)
        assert listed.status_code == 200
        assert listed.json()["keys"] == [{"label": "ci"}]

    def test_sso_requires_department(self, client):
        assert client.get("/sso/metadata", headers=AUTH).status_code == 403
        client.entitlements.set_plan("alice", "lab")
        assert client.get("/sso/metadata", headers=AUTH).status_code == 403
        client.entitlements.set_plan("alice", "department")
        body = client.get("/sso/metadata", headers=AUTH).json()
        assert body["sso_enabled"] is True
        assert body["protocol"] == "oidc"


# ---------------------------------------------------------------------------
# Workspaces (ADR-0047)
# ---------------------------------------------------------------------------


def _make_lab(client):
    client.entitlements.set_plan("alice", "lab")
    response = client.post(
        "/workspaces", headers=AUTH, json={"name": "Lab A", "type": "lab"}
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestWorkspaces:
    def test_free_plan_cannot_create_a_workspace(self, client):
        response = client.post(
            "/workspaces", headers=AUTH, json={"name": "Class", "type": "classroom"}
        )
        assert response.status_code == 403
        assert "lab" in response.json()["detail"]

    def test_workspace_creation_opens_the_pool_and_seats(self, client):
        body = _make_lab(client)
        assert body["owner_uid"] == "alice"
        assert body["seat_limit"] == 10
        assert body["monthly_pool_tokens"] == 10_000
        assert body["token_pool"] == 10_000
        assert body["members"][0]["role"] == "owner"

    def test_unknown_workspace_type_is_422(self, client):
        client.entitlements.set_plan("alice", "lab")
        assert (
            client.post(
                "/workspaces", headers=AUTH, json={"name": "X", "type": "zoo"}
            ).status_code
            == 422
        )

    def test_members_are_added_once_and_deduplicated(self, client):
        workspace = _make_lab(client)
        path = f"/workspaces/{workspace['workspace_id']}/members"
        first = client.post(path, headers=AUTH, json={"email": "bob@uni.edu"})
        second = client.post(path, headers=AUTH, json={"email": "BOB@uni.edu"})
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["uid"] == second.json()["uid"]
        state = client.get(
            f"/workspaces/{workspace['workspace_id']}", headers=AUTH
        ).json()
        assert len(state["members"]) == 2

    def test_seat_limit_is_enforced(self, client):
        client.entitlements.set_plan("alice", "lab")
        workspace = client.post(
            "/workspaces", headers=AUTH, json={"name": "Class", "type": "classroom"}
        ).json()
        path = f"/workspaces/{workspace['workspace_id']}/members"
        assert workspace["seat_limit"] == 30
        for index in range(29):  # owner already holds seat 1
            assert (
                client.post(
                    path, headers=AUTH, json={"email": f"s{index}@uni.edu"}
                ).status_code
                == 200
            )
        over_capacity = client.post(path, headers=AUTH, json={"email": "late@uni.edu"})
        assert over_capacity.status_code == 409
        assert "seats used" in over_capacity.json()["detail"]

    def test_non_owner_cannot_add_members(self, client):
        workspace = _make_lab(client)
        client.entitlements.set_plan("bob", "free")
        response = client.post(
            f"/workspaces/{workspace['workspace_id']}/members",
            headers={"Authorization": "Bearer mock:bob"},
            json={"email": "eve@uni.edu"},
        )
        assert response.status_code == 403

    def test_owner_cannot_be_removed(self, client):
        workspace = _make_lab(client)
        response = client.delete(
            f"/workspaces/{workspace['workspace_id']}/members/alice", headers=AUTH
        )
        assert response.status_code == 409

    def test_owner_usage_dashboard_lists_members(self, client):
        workspace = _make_lab(client)
        usage = client.get(
            f"/workspaces/{workspace['workspace_id']}/usage", headers=AUTH
        )
        assert usage.status_code == 200
        assert usage.json()["workspace_id"] == workspace["workspace_id"]
        assert usage.json()["seats_used"] == 1
        assert [member["uid"] for member in usage.json()["per_member"]] == ["alice"]

    def test_pool_topup_returns_a_checkout_session(self, client):
        workspace = _make_lab(client)
        response = client.post(
            f"/workspaces/{workspace['workspace_id']}/pool/topup",
            headers=AUTH,
            json={"tokens": 500},
        )
        assert response.status_code == 200
        assert response.json()["tokens"] == 500
        assert response.json()["checkout_url"].startswith("https://checkout.stripe.com/")


class TestWorkspacePoolDraw:
    """Pool draws settle against the pool and bypass the personal allowance."""

    def _app(self, tokens, entitlements, workspaces):
        from auth.firebase_auth import MockFirebaseAuthClient

        app = FastAPI()
        app.add_middleware(
            AuthMiddleware,
            firebase_client=MockFirebaseAuthClient(),
            token_client=tokens,
            publisher=LoggingPublisher(),
            entitlements=entitlements,
            workspaces=workspaces,
        )
        app.post("/analysis")(lambda: {"ok": True})
        return TestClient(app)

    def test_member_draws_from_the_pool_not_their_balance(self):
        # Alice has no personal tokens of her own; only the classroom pool.
        tokens = MockTokenBalanceClient({"alice": 0})
        entitlements = MockEntitlementsClient(ledger=tokens)
        workspaces = InMemoryWorkspaceStore()
        workspace = workspaces.create(name="Class", type="classroom", owner_uid="alice")
        client = self._app(tokens, entitlements, workspaces)

        response = client.post("/analysis", headers=AUTH)
        assert response.status_code == 200
        state = workspaces.get(workspace.workspace_id)
        assert state.token_pool_used == 20
        # The personal balance is untouched (the 50 is the monthly grant,
        # which the middleware's plan lookup applied; nothing was charged).
        assert tokens.get_balance("alice") == 50
        # The personal monthly allowance is untouched: the pool is the
        # workspace's budget (ADR-0047).
        assert entitlements.status("alice").used_this_month == 0

    def test_exhausted_pool_falls_back_to_the_personal_balance(self):
        tokens = MockTokenBalanceClient({"alice": 1000})
        entitlements = MockEntitlementsClient(ledger=tokens)
        workspaces = InMemoryWorkspaceStore()
        workspace = workspaces.create(name="Class", type="classroom", owner_uid="alice")
        workspaces.draw(workspace.workspace_id, "alice", 5_000, "seed")
        client = self._app(tokens, entitlements, workspaces)

        assert client.post("/analysis", headers=AUTH).status_code == 200
        assert tokens.get_balance("alice") == 980

    def test_pool_too_small_and_balance_too_small_is_402(self):
        # No entitlements layer in this minimal deployment, so the monthly
        # grant does not intervene: both the pool and the ledger are short.
        tokens = MockTokenBalanceClient({"alice": 5})
        workspaces = InMemoryWorkspaceStore()
        workspace = workspaces.create(name="Class", type="classroom", owner_uid="alice")
        workspaces.draw(workspace.workspace_id, "alice", 4_995, "seed")
        client = self._app(tokens, None, workspaces)

        response = client.post("/analysis", headers=AUTH)
        assert response.status_code == 402
        # The refusal reports the combined remaining buying power.
        assert response.json()["balance"] == 10
        assert response.json()["required"] == 20


# ---------------------------------------------------------------------------
# Signup cohort event (ADR-0048)
# ---------------------------------------------------------------------------


class TestSignupEvent:
    def test_signup_is_recorded_once(self, client):
        first = client.post("/auth/signup", headers=AUTH).json()
        second = client.post("/auth/signup", headers=AUTH).json()
        assert first == {"recorded": True, "duplicate": False}
        assert second == {"recorded": False, "duplicate": True}
        signups = [
            record
            for record in client.app.state.audit_log.records()
            if record.get("event") == "user_signup"
        ]
        assert len(signups) == 1
        assert signups[0]["kind"] == "analytics_event"
        assert signups[0]["user_id"] == "alice"


# ---------------------------------------------------------------------------
# Token-layer wiring (ADR-0045)
# ---------------------------------------------------------------------------


class TestTokenLayerWiring:
    """The enforced offline plane must grant the monthly allowance.

    Regression: ``build_token_layer`` once built ``MockEntitlementsClient()``
    without a ledger.  The rollover grant is applied *through* the ledger, so
    with no ledger every user started at a zero balance and every priced
    request was refused 402 -- the local plane could not run an analysis at
    all, which the endpoint tests above could not see because they wire
    their own clients.
    """

    def test_dry_run_layer_shares_one_ledger_between_tokens_and_entitlements(self):
        from auth.config import AuthConfig
        from auth.install import build_token_layer

        layer = build_token_layer(
            AuthConfig(
                project_id="test",
                firebase_credentials_path=None,
                dry_run=True,
                default_free_tokens=50,
                enabled=True,
            )
        )
        assert layer.entitlements is not None
        assert layer.entitlements._ledger is layer.tokens

    def test_first_authenticated_request_gets_the_free_allowance(self):
        from auth.config import AuthConfig
        from auth.install import build_token_layer, install_auth

        cfg = AuthConfig(
            project_id="test",
            firebase_credentials_path=None,
            dry_run=True,
            default_free_tokens=50,
            enabled=True,
        )
        layer = build_token_layer(cfg)
        app = FastAPI()
        install_auth(app, cfg, layer=layer)
        tokens = layer.tokens

        @app.post("/analysis")
        def analysis() -> dict:
            return {"ok": True}

        client = TestClient(app)
        # A priced route (20 tokens): the grant must have happened for this
        # to be anything other than a 402 on a brand-new account.
        response = client.post("/analysis", headers=AUTH)
        assert response.status_code == 200, response.text
        assert tokens.get_balance("alice") == 30
