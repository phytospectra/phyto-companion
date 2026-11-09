"""End-to-end durable persistence through the orchestrator HTTP API.

Exercises the real SQLite run store via PHYTO_RUN_STORE=sqlite: restart
recovery (fresh runtime process state), content-bound idempotent replay,
conflicting-key rejection, durable approval refusal, owner-scoped history,
and cloud fail-closed behavior without durable storage.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator import main
from orchestrator.run_store import SQLiteRunStore

@pytest.fixture()
def sqlite_app(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "0")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_RUN_STORE", "sqlite")
    monkeypatch.setenv("PHYTO_RUN_STORE_PATH", str(tmp_path / "runs.sqlite"))
    main.get_runtime.cache_clear()
    app = main.create_app()

    @app.middleware("http")
    async def test_identity(request, call_next):
        # Test-only principal injection (auth enforcement is off); never
        # installed on the exported app.
        uid = request.headers.get("X-Test-UID")
        if uid:
            request.state.user = SimpleNamespace(uid=uid)
        return await call_next(request)

    with TestClient(app) as client:
        yield client, str(tmp_path / "runs.sqlite")
    main.get_runtime.cache_clear()


AUTH = {"X-Test-UID": "alice"}
BOB = {"X-Test-UID": "bob"}
KEY = {"Idempotency-Key": "durable-api-key-0001"}
WORKFLOW_REQUEST = {
    "sample_id": "durable-api-test",
    "workflow": "single_compound",
    "dry_run": True,
}


def test_run_survives_runtime_restart_and_stays_owner_bound(sqlite_app):
    client, path = sqlite_app
    created = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]

    # Simulated restart: cache-cleared runtime re-hydrates from the store.
    main.get_runtime.cache_clear()
    refreshed = client.get(f"/runs/{run_id}", headers=AUTH)
    assert refreshed.status_code == 200
    assert refreshed.json()["run_id"] == run_id
    assert refreshed.json()["scientific_results"] == created.json()["summary"]["scientific_results"]
    # Ownership boundary survives the restart.
    other = client.get(f"/runs/{run_id}", headers=BOB)
    assert other.status_code == 404
    # Owner-scoped durable history is served after restart.
    history = client.get("/runs", headers=AUTH)
    assert history.status_code == 200
    assert [row["run_id"] for row in history.json()] == [run_id]
    assert client.get("/runs", headers=BOB).json() == []


def test_idempotent_replay_returns_identical_response(sqlite_app):
    client, _ = sqlite_app
    first = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert first.status_code == 200
    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()  # exact stored-response replay


def test_same_key_different_content_is_rejected(sqlite_app):
    client, _ = sqlite_app
    first = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert first.status_code == 200
    conflicting = client.post(
        "/analysis", headers=AUTH | KEY,
        json={**WORKFLOW_REQUEST, "sample_id": "different-content"},
    )
    assert conflicting.status_code == 409
    assert "different request content" in conflicting.json()["detail"]


def test_short_idempotency_key_is_rejected(sqlite_app):
    client, _ = sqlite_app
    response = client.post(
        "/analysis", headers=AUTH | {"Idempotency-Key": "too-short"},
        json=WORKFLOW_REQUEST,
    )
    assert response.status_code == 400


def test_duplicate_key_replay_survives_restart(sqlite_app):
    client, path = sqlite_app
    first = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    main.get_runtime.cache_clear()
    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()


def test_uncertain_reconciliation_marks_stale_executing(sqlite_app):
    client, path = sqlite_app
    created = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST)
    run_id = created.json()["run_id"]
    # Simulate a crash before terminal persistence: force outcome=executing.
    store = SQLiteRunStore(path)
    record = store.load_run(run_id)
    record.outcome = "executing"
    store.save_run(record)

    store = SQLiteRunStore(path)
    assert store.reconcile_stale_executing(dry_run=True) == [run_id]
    assert store.load_run(run_id).outcome == "executing"
    assert store.reconcile_stale_executing(dry_run=False) == [run_id]
    reconciled = store.load_run(run_id)
    assert reconciled.outcome == "uncertain"
    # Reconciliation never mutates operational state or re-executes work.
    assert reconciled.status in {"complete", "failed", "awaiting_review"}


def test_cloud_fails_closed_without_durable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("PHYTO_ENV", "cloud")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")  # valid cloud-mock science config
    monkeypatch.delenv("PHYTO_RUN_STORE", raising=False)
    main.get_runtime.cache_clear()
    with pytest.raises(RuntimeError, match="durable run storage"):
        main.get_runtime()
    main.get_runtime.cache_clear()


def test_cloud_accepts_explicit_sqlite_store(monkeypatch, tmp_path):
    monkeypatch.setenv("PHYTO_ENV", "cloud")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_RUN_STORE", "sqlite")
    monkeypatch.setenv("PHYTO_RUN_STORE_PATH", str(tmp_path / "cloud.sqlite"))
    main.get_runtime.cache_clear()
    runtime = main.get_runtime()
    assert runtime.run_store.mode == "sqlite"
    main.get_runtime.cache_clear()
