"""Offline operational acceptance: real HTTP/auth middleware, mock collaborators.

The Firebase SDK verifier is a mock; these are NOT live Firebase, IAM, billing,
provider or scientific-validation results. No external target is contacted.
"""
from __future__ import annotations

import copy
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from auth.config import AuthConfig
from auth.entitlements import MockEntitlementsClient
from auth.firebase_auth import RealFirebaseAuthClient
from auth.install import TokenLayer, install_auth
from auth.token_balance import MockTokenBalanceClient
from auth.workspaces import InMemoryWorkspaceStore
from phyto_shared.share import share_url_for
from phyto_shared.token_pricing import cost_for_route

ORIGIN = "https://staging-ui.invalid"
KEY = "operational-idempotency-0001"
APPROVAL = {"checkpoint": "before_confirmed_claim", "actor": "offline-owner"}


def headers(uid="alice", **extra):
    return {"Authorization": f"Bearer valid-{uid}", **extra}


@pytest.fixture()
def harness(monkeypatch, tmp_path):
    """Use the real SDK adapter but never construct Firebase or a cloud client."""
    def forbidden(*args, **kwargs):
        raise AssertionError("External network/cloud collaborator forbidden in offline acceptance")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "1")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_CORS_ORIGINS", ORIGIN)
    monkeypatch.setenv("SHARE_HMAC_KEY", "offline-operational-key-not-a-secret")
    from auth import install
    from orchestrator import main

    for name in ("get_firebase_client", "get_token_balance_client", "get_publisher",
                 "FirestoreEntitlementsClient", "FirestoreWorkspaceStore"):
        monkeypatch.setattr(install, name, forbidden)

    def verify(token, *, check_revoked):
        assert check_revoked is True
        if token not in {"valid-alice", "valid-bob"}:
            # SDK failures are simulated, NOT an independent audience/revocation check.
            raise ValueError(f"simulated verifier rejection: {token}")
        return {"uid": token.removeprefix("valid-"), "aud": "offline-staging-project"}

    sdk = SimpleNamespace(verify_id_token=Mock(side_effect=verify))
    verifier = RealFirebaseAuthClient.__new__(RealFirebaseAuthClient)
    verifier._auth = sdk
    tokens = MockTokenBalanceClient({"alice": 500, "bob": 500})
    entitlements = MockEntitlementsClient()
    for uid in ("alice", "bob"):
        entitlements.status(uid)
    publisher = SimpleNamespace(events=[])
    publisher.publish = publisher.events.append
    workspaces = InMemoryWorkspaceStore()
    layer = TokenLayer(firebase=verifier, tokens=tokens, publisher=publisher,
                       entitlements=entitlements, workspaces=workspaces)
    config = AuthConfig(project_id="offline-staging-project", firebase_credentials_path=None,
                        dry_run=True, default_free_tokens=500, enabled=True)
    monkeypatch.setattr(install, "build_token_layer", lambda config=None: layer)
    main.get_runtime.cache_clear()
    app = main.create_app(config)
    result = SimpleNamespace(app=app, layer=layer, tokens=tokens, publisher=publisher,
                             entitlements=entitlements, workspaces=workspaces, sdk=sdk,
                             config=config, main=main)
    with TestClient(app, raise_server_exceptions=False) as client:
        result.client = client
        result.runtime = main.get_runtime()
        yield result
    main.get_runtime.cache_clear()


def start(harness, workflow="single_compound", uid="alice", **extra_headers):
    response = harness.client.post("/analysis", headers=headers(uid, **extra_headers), json={
        "sample_id": "offline-operational", "workflow": workflow, "dry_run": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def domain_snapshot(h):
    """Exclude expected access audit, verifier calls and share rate-limit hits."""
    return copy.deepcopy({"runs": h.runtime.runs, "manifests": h.runtime.manifests,
                          "shares": h.runtime.share_registry._revoked,
                          "balances": h.tokens._balances,
                          "reservations": h.tokens._reservations,
                          "usage": h.entitlements._users,
                          "events": h.publisher.events})


@pytest.mark.parametrize("token", [None, "expired", "revoked", "wrong-project", "malformed"])
def test_invalid_identity_rejected_before_run_or_reservation(harness, token):
    h = harness
    before = domain_snapshot(h)
    response = h.client.post("/analysis", headers={"Origin": ORIGIN, **(
        {"Authorization": f"Bearer {token}"} if token else {})}, json={
        "sample_id": "must-not-run", "workflow": "single_compound", "dry_run": True,
    })
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert domain_snapshot(h) == before
    assert h.sdk.verify_id_token.call_count == (0 if token is None else 1)


def test_owner_citation_approval_and_share_lifecycle(harness):
    h = harness
    run_id = start(h)
    run = h.client.get(f"/runs/{run_id}", headers=headers())
    assert run.status_code == 200
    assert run.json()["status"] == "awaiting_review"
    assert h.runtime.runs[run_id]["owner_uid"] == "alice"
    assert h.client.get(f"/runs/{run_id}/citation", headers=headers()).status_code == 409
    approved = h.client.post(f"/runs/{run_id}/approve", headers=headers(), json=APPROVAL)
    assert approved.status_code == 200
    assert approved.json()["status"] == "complete"
    citation = h.client.get(f"/runs/{run_id}/citation", headers=headers())
    assert citation.status_code == 200
    assert citation.json()["canonical_id"].startswith("phytospectra.run:")
    assert len(citation.json()["provenance_manifest_hash"]) == 64
    minted = h.client.post(f"/runs/{run_id}/share", headers=headers(), json={})
    assert minted.status_code == 200
    token = minted.json()["share_url"].rsplit("/", 1)[1]
    before = domain_snapshot(h)
    calls = h.sdk.verify_id_token.call_count
    public = h.client.get(f"/share/{token}")
    assert public.status_code == 200
    assert public.json()["run_id"] == run_id
    assert "owner_uid" not in public.json()
    assert "context" not in public.json()
    assert h.sdk.verify_id_token.call_count == calls  # public capability, no Firebase
    assert domain_snapshot(h) == before
    # Revocation is per-run: DELETE /runs/{run_id}/share revokes every token.
    assert h.client.delete(f"/runs/{run_id}/share", headers=headers()).status_code == 200
    after = domain_snapshot(h)
    assert h.client.get(f"/share/{token}").status_code == 410
    assert domain_snapshot(h) == after


@pytest.mark.parametrize("uid,expected", [("bob", 404), (None, 401)])
def test_nonowner_denials_leave_run_citation_share_and_balance_unchanged(harness, uid, expected):
    h = harness
    run_id = start(h)
    other = start(h, uid="bob")
    token = h.client.post(f"/runs/{run_id}/share", headers=headers(), json={}).json()[
        "share_url"].rsplit("/", 1)[1]
    before = domain_snapshot(h)
    for method, path, body in [
        ("GET", f"/runs/{run_id}", None), ("GET", f"/runs/{run_id}/citation", None),
        ("POST", f"/runs/{run_id}/approve", APPROVAL),
        ("POST", f"/runs/{run_id}/share", {}),
        ("DELETE", f"/runs/{run_id}/share", None),
    ]:
        response = h.client.request(method, path, headers=headers(uid) if uid else {}, json=body)
        assert response.status_code == expected, (path, response.text)
        assert domain_snapshot(h) == before
    # There is no per-token DELETE route; unknown paths never mutate state.
    assert h.client.delete(f"/runs/{other}/share/{token}", headers=headers("bob")).status_code == 404
    assert domain_snapshot(h) == before
    assert h.client.get(f"/share/{token}").status_code == 200


def test_public_capability_never_authenticates_private_or_mutating_paths(harness):
    h = harness
    run_id = start(h)
    token = h.client.post(f"/runs/{run_id}/share", headers=headers(), json={}).json()[
        "share_url"].rsplit("/", 1)[1]
    before = domain_snapshot(h)
    for method, path, body in [
        ("GET", f"/runs/{run_id}", None), ("GET", f"/runs/{run_id}/citation", None),
        ("POST", f"/runs/{run_id}/approve", APPROVAL),
        ("POST", f"/runs/{run_id}/share", {}),
        ("DELETE", f"/runs/{run_id}/share", None),
        ("POST", "/analysis", {"sample_id": "x", "workflow": "single_compound", "dry_run": True}),
    ]:
        assert h.client.request(method, path, headers={"Authorization": f"Bearer {token}"},
                                json=body).status_code == 401
        assert domain_snapshot(h) == before
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert h.client.request(method, f"/share/{token}").status_code == 405
        assert domain_snapshot(h) == before
    assert h.client.get(f"/share/{token}tampered").status_code == 404
    assert domain_snapshot(h) == before


def metered_app(h, handler):
    app = FastAPI()
    install_auth(app, h.config, layer=h.layer)
    app.post("/elucidate")(handler)
    app.post("/llm/hypothesize")(handler)
    return TestClient(app, raise_server_exceptions=False)


def use_pool(h, enabled):
    if not enabled:
        return None
    workspace = h.workspaces.create(name="Offline only", type="lab", owner_uid="alice")
    return workspace


@pytest.mark.parametrize("pool", [False, True])
@pytest.mark.parametrize("outcome", ["success", "http_error", "exception", "cache_hit"])
def test_reservation_refund_and_duplicate_never_reexecutes(harness, pool, outcome):
    h = harness
    workspace = use_pool(h, pool)
    called = []

    def endpoint(request: Request):
        called.append(request.state.user.uid)
        assert request.state.reservation_id
        if outcome == "http_error":
            raise HTTPException(status_code=409, detail="synthetic failure")
        if outcome == "exception":
            raise RuntimeError("synthetic failure")
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": True}, headers={"X-Cache": "HIT"} if outcome == "cache_hit" else {})

    balance = h.tokens.get_balance("alice")
    with metered_app(h, endpoint) as client:
        first = client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
        assert first.status_code == {"success": 200, "http_error": 409,
                                     "exception": 500, "cache_hit": 200}[outcome]
        cost = cost_for_route("POST", "/elucidate").tokens
        spent = cost if outcome == "success" else 0
        assert h.tokens.get_balance("alice") == balance - (spent if not pool else 0)
        assert h.entitlements.status("alice").used_this_month == (spent if not pool else 0)
        if workspace:
            assert workspace.token_pool_used == spent
            assert workspace.members["alice"].tokens_used == spent
        settled = domain_snapshot(h)
        # Same key is consumed even after refund: response replay is not implemented.
        duplicate = client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
        assert duplicate.status_code == 409
        assert called == ["alice"]
        assert domain_snapshot(h) == settled
    assert len(h.publisher.events) == 1
    assert h.publisher.events[0].tokens_consumed == spent


@pytest.mark.parametrize("pool", [False, True])
def test_parallel_same_key_and_cross_operation_binding(harness, pool):
    h = harness
    workspace = use_pool(h, pool)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def endpoint():
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"ok": True}

    with metered_app(h, endpoint) as client, ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.post, "/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
        try:
            assert entered.wait(5)
            duplicate = client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
            assert duplicate.status_code == 409
            conflict = client.post("/llm/hypothesize", headers=headers(**{"Idempotency-Key": KEY}))
            assert conflict.status_code == 409
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        assert len(calls) == 1
        if workspace:
            # Exhausting the pool must not let a duplicate fall through to personal funds.
            workspace.token_pool_used = workspace.token_pool
        before = domain_snapshot(h)
        assert client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY})).status_code == 409
        assert domain_snapshot(h) == before
        # A key is scoped to a UID, not shared globally across users.
        assert client.post("/elucidate", headers=headers("bob", **{"Idempotency-Key": KEY})).status_code == 200
        assert len(calls) == 2


def test_cors_preflight_and_controlled_402_503(harness):
    h = harness
    before = domain_snapshot(h)
    preflight = h.client.options("/analysis", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert h.sdk.verify_id_token.call_count == 0
    assert domain_snapshot(h) == before
    denied = h.client.options("/analysis", headers={
        "Origin": "https://not-allowed.invalid", "Access-Control-Request-Method": "POST",
    })
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
    h.tokens._balances["alice"] = 0
    h.app.post("/unpriced-offline")(lambda: {"must_not_run": True})
    for path, expected in (("/analysis", 402), ("/unpriced-offline", 503)):
        response = h.client.post(path, headers=headers(Origin=ORIGIN), json={})
        assert response.status_code == expected
        assert response.headers["access-control-allow-origin"] == ORIGIN
    assert not h.runtime.runs
    assert not h.publisher.events


def test_audit_records_are_owner_scoped_or_admin(harness):
    """Audit records carry identities: with auth enforced, a caller may read
    a run they own (scoped) or the whole chain as an admin — never another
    user's records."""
    h = harness
    run_id = start(h)
    owned = h.client.get("/audit/records", headers=headers(), params={"run_id": run_id})
    assert owned.status_code == 200
    assert all(record.get("run_id") == run_id for record in owned.json())
    other = h.client.get("/audit/records", headers=headers("bob"), params={"run_id": run_id})
    assert other.status_code == 404  # unknown-run semantics, no existence leak
    # The unfiltered chain requires an admin; neither user here is one.
    assert h.client.get("/audit/records", headers=headers()).status_code == 403
    assert h.client.get("/audit/records", headers=headers("bob")).status_code == 403


def test_audit_log_never_carries_credentials(harness):
    """The durable audit chain must not capture bearer tokens, the share HMAC
    key, or the share URL credential itself."""
    import json

    h = harness
    run_id = start(h)
    share = h.client.post(f"/runs/{run_id}/share", headers=headers(), json={}).json()
    share_token = share["share_url"].rsplit("/", 1)[1]
    h.client.get(f"/share/{share_token}")  # emits run_shared audit events
    secret = "offline-operational-key-not-a-secret"
    raw = h.runtime.audit.path.read_text(encoding="utf-8")
    assert "Bearer" not in raw
    assert "valid-alice" not in raw  # verifier tokens must never be logged
    assert secret not in raw  # the share HMAC key must never be logged
    for record in h.runtime.audit.records():
        encoded = json.dumps(record)
        assert "Bearer" not in encoded
        assert secret not in encoded
        # The full signed share URL is a credential; only the token id may
        # appear in the audit tail.
        assert share_url_for(share_token) not in encoded
