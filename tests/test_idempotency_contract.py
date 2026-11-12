"""End-to-end idempotency contract through the real authenticated stack.

The accounting middleware and the durable run layer must agree on one
contract: a consumed request key either replays the stored creation response
(never reserving or charging again) or returns a stable documented conflict
from durable state. These tests run the actual AuthMiddleware over the real
orchestrator app with the SQLite run store and offline auth collaborators —
the middleware is never bypassed.

The two layers keep distinct guarantees: the ledger provides at-most-once
handler admission and at-most-once terminal accounting settlement; the run
store provides content-bound replay and the execution lifecycle. Monetary
settlement never authorizes a new execution, and a replay never costs money.
"""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth.config import AuthConfig
from auth.entitlements import MockEntitlementsClient
from auth.firebase_auth import RealFirebaseAuthClient
from auth.install import TokenLayer
from auth.token_balance import MockTokenBalanceClient
from auth.workspaces import InMemoryWorkspaceStore
from orchestrator.run_store import SQLiteRunStore
from phyto_shared.token_pricing import cost_for_route

KEY = "contract-idempotency-0001"
WORKFLOW_REQUEST = {
    "sample_id": "idempotency-contract",
    "workflow": "single_compound",
    "dry_run": True,
}
PRICE = cost_for_route("POST", "/analysis").tokens


def headers(uid="alice", **extra):
    return {"Authorization": f"Bearer valid-{uid}", **extra}


@pytest.fixture()
def contract(monkeypatch, tmp_path):
    """Real middleware, offline collaborators, durable SQLite run store."""
    def forbidden(*args, **kwargs):
        raise AssertionError("External network/cloud collaborator forbidden in offline acceptance")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "1")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_RUN_STORE", "sqlite")
    monkeypatch.setenv("PHYTO_RUN_STORE_PATH", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("SHARE_HMAC_KEY", "offline-contract-key-not-a-secret")
    from auth import install
    from orchestrator import main

    for name in ("get_firebase_client", "get_token_balance_client", "get_publisher",
                 "FirestoreEntitlementsClient", "FirestoreWorkspaceStore"):
        monkeypatch.setattr(install, name, forbidden)

    def verify(token, *, check_revoked):
        assert check_revoked is True
        if token not in {"valid-alice", "valid-bob"}:
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
    result = SimpleNamespace(
        app=app, layer=layer, tokens=tokens, publisher=publisher,
        entitlements=entitlements, workspaces=workspaces, config=config,
        main=main, verifier=verifier,
        path=str(tmp_path / "runs.sqlite"),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        result.client = client
        result.runtime = main.get_runtime()
        yield result
    main.get_runtime.cache_clear()


def submit(h, key=KEY, uid="alice", **payload):
    body = {**WORKFLOW_REQUEST, **payload}
    response = h.client.post(
        "/analysis", headers=headers(uid, **{"Idempotency-Key": key}), json=body
    )
    return response


def test_duplicate_same_key_replays_without_second_reservation(contract):
    h = contract
    first = submit(h)
    assert first.status_code == 200, first.text
    balance_after_first = h.tokens.get_balance("alice")
    assert balance_after_first == 500 - PRICE

    duplicate = submit(h)
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert duplicate.headers.get("X-Idempotency-Replay") == "true"
    # No second reservation, no second charge, no second usage event.
    assert h.tokens.get_balance("alice") == balance_after_first
    assert len(h.publisher.events) == 1
    assert h.publisher.events[0].tokens_consumed == PRICE


def test_duplicate_different_content_is_conflict_without_charge(contract):
    h = contract
    first = submit(h)
    assert first.status_code == 200
    balance = h.tokens.get_balance("alice")
    conflicting = submit(h, sample_id="different-content")
    assert conflicting.status_code == 409
    assert "different request content" in conflicting.json()["detail"]
    assert h.tokens.get_balance("alice") == balance
    assert len(h.publisher.events) == 1


def test_duplicate_while_in_flight_conflicts_with_actionable_state(contract, monkeypatch):
    h = contract
    entered, release = threading.Event(), threading.Event()
    real = h.runtime.engine.run

    def block(graph, context):
        entered.set()
        assert release.wait(10)
        return real(graph, context)

    monkeypatch.setattr(h.runtime.engine, "run", block)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, h)
        try:
            assert entered.wait(10)
            duplicate = submit(h)
            assert duplicate.status_code == 409
            detail = duplicate.json()["detail"]
            assert detail["outcome"] in {"admitted", "executing"}
            assert detail["run_id"]
            assert "in flight" in detail["message"]
            assert h.tokens.get_balance("alice") == 500 - PRICE  # one charge only
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 200


def test_duplicate_after_uncertain_requires_reconciliation(contract, monkeypatch):
    h = contract
    # Crash the first submission before terminal persistence so the durable
    # record stays admitted; the operator then reconciles it to uncertain.
    real = h.runtime.engine.run

    def crash(graph, context):
        raise RuntimeError("simulated crash during execution")

    monkeypatch.setattr(h.runtime.engine, "run", crash)
    crashed = submit(h)
    assert crashed.status_code == 500
    monkeypatch.setattr(h.runtime.engine, "run", real)

    store = SQLiteRunStore(h.path)
    run_id = store.list_runs("alice", limit=1)[0].run_id
    assert store.load_run(run_id).outcome == "admitted"
    # Operator procedure: service stopped, confirmed quiesced, run recorded
    # uncertain with the operator and reason.
    store.reconcile_stale(
        dry_run=False, require_quiesced=True, run_ids=[run_id],
        operator="ops@example.org", reason="simulated shutdown",
    )
    assert store.load_run(run_id).outcome == "uncertain"

    balance = h.tokens.get_balance("alice")
    duplicate = submit(h)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["outcome"] == "uncertain"
    assert "reconciliation" in duplicate.json()["detail"]["message"]
    assert "new key" in duplicate.json()["detail"]["recovery"]
    assert h.tokens.get_balance("alice") == balance

    # Resolution is the only path forward; afterwards the key is failed.
    assert store.resolve_run(run_id, operator="ops@example.org", reason="abandoned")
    assert submit(h).status_code == 409
    assert submit(h).json()["detail"]["outcome"] == "failed"


def test_replay_after_ledger_restart_never_charges_twice(contract, monkeypatch):
    """Even when the (mock) accounting ledger forgets its claims — the
    documented dry-run restart window — a replayed response is refunded:
    exactly one charge survives."""
    h = contract
    first = submit(h)
    assert first.status_code == 200

    from auth import install

    tokens2 = MockTokenBalanceClient({"alice": 500, "bob": 500})
    entitlements2 = MockEntitlementsClient()
    for uid in ("alice", "bob"):
        entitlements2.status(uid)
    publisher2 = SimpleNamespace(events=[])
    publisher2.publish = publisher2.events.append
    layer2 = TokenLayer(
        firebase=h.layer.firebase, tokens=tokens2, publisher=publisher2,
        entitlements=entitlements2, workspaces=InMemoryWorkspaceStore(),
    )
    monkeypatch.setattr(install, "build_token_layer", lambda config=None: layer2)
    h.main.get_runtime.cache_clear()
    app2 = h.main.create_app(h.config)
    with TestClient(app2, raise_server_exceptions=False) as client2:
        replayed = client2.post(
            "/analysis", headers=headers(**{"Idempotency-Key": KEY}), json=WORKFLOW_REQUEST
        )
        assert replayed.status_code == 200
        assert replayed.json() == first.json()
        assert replayed.headers.get("X-Idempotency-Replay") == "true"
    # The fresh ledger reserved the charge and then refunded it on replay:
    # no money moved on the replay, and the event records zero consumption.
    assert tokens2.get_balance("alice") == 500
    assert len(publisher2.events) == 1
    assert publisher2.events[0].tokens_consumed == 0
    assert publisher2.events[0].status == "refunded"
    h.main.get_runtime.cache_clear()


def test_non_replay_routes_keep_the_strict_conflict(contract):
    """Only the routes that implement durable replay opt in; every other
    endpoint keeps the strict 409 on a consumed key."""
    from auth.install import install_auth

    called = []

    def endpoint(request: Request):
        called.append(1)
        return {"ok": True}

    app = FastAPI()
    install_auth(app, contract.config, layer=contract.layer)
    app.post("/elucidate")(endpoint)
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
        assert first.status_code == 200
        duplicate = client.post("/elucidate", headers=headers(**{"Idempotency-Key": KEY}))
        assert duplicate.status_code == 409
        assert "replay is unavailable" in duplicate.json()["detail"]
        assert called == [1]


def test_keys_are_user_scoped_and_never_leak(contract):
    h = contract
    alice_run = submit(h, key="shared-key-string-0001", uid="alice")
    assert alice_run.status_code == 200
    # Bob reuses the same key string: it is his own fresh operation.
    bob_run = submit(h, key="shared-key-string-0001", uid="bob")
    assert bob_run.status_code == 200
    assert bob_run.json()["run_id"] != alice_run.json()["run_id"]
    # Alice's replay still resolves to her own run, never Bob's.
    alice_replay = submit(h, key="shared-key-string-0001", uid="alice")
    assert alice_replay.status_code == 200
    assert alice_replay.json() == alice_run.json()
    # Each principal was charged once.
    assert h.tokens.get_balance("alice") == 500 - PRICE
    assert h.tokens.get_balance("bob") == 500 - PRICE
