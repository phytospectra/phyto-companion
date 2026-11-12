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
    # Simulate a legacy (pre-lease) crash window: outcome=executing with no
    # worker or lease columns, written at the raw row level because the store
    # keeps those columns immutable once created.
    store = SQLiteRunStore(path)
    record = store.load_run(run_id)
    record.outcome = "executing"
    store.save_run(record)
    import sqlite3

    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE runs SET worker_id=NULL, lease_expires_at=NULL WHERE run_id=?",
            (run_id,),
        )

    store = SQLiteRunStore(path)
    preview = store.reconcile_stale(dry_run=True)
    assert [e["run_id"] for e in preview] == [run_id]
    assert store.load_run(run_id).outcome == "executing"
    applied = store.reconcile_stale(
        dry_run=False, operator="ops@example.org", reason="legacy crash window"
    )
    assert [e["run_id"] for e in applied] == [run_id]
    reconciled = store.load_run(run_id)
    assert reconciled.outcome == "uncertain"
    # Reconciliation never mutates operational state or re-executes work.
    assert reconciled.status in {"complete", "failed", "awaiting_review"}


def test_review_pending_runs_are_never_reconciled(sqlite_app):
    """A healthy review-pending run must survive even a quiesced-shutdown
    reconciliation pass (audit finding 2 counterexample)."""
    client, path = sqlite_app
    created = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST)
    run_id = created.json()["run_id"]
    store = SQLiteRunStore(path)
    record = store.load_run(run_id)
    assert record.outcome == "awaiting_review"  # durable state says review-pending
    assert store.reconcile_stale(dry_run=True, require_quiesced=True) == []
    assert store.load_run(run_id).outcome == "awaiting_review"


def test_share_revocation_survives_restart(sqlite_app):
    """Revocation is durable: a token revoked after a restart stays revoked
    across further restarts."""
    client, path = sqlite_app
    created = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST)
    run_id = created.json()["run_id"]
    share = client.post(f"/runs/{run_id}/share", headers=AUTH, json={})
    assert share.status_code == 200
    token = share.json()["share_url"].rsplit("/", 1)[1]

    # Simulated restart, then revoke: the revocation must live in the store.
    main.get_runtime.cache_clear()
    assert client.get(f"/share/{token}").status_code == 200
    revoked = client.delete(f"/runs/{run_id}/share", headers=AUTH)
    assert revoked.status_code == 200
    # Another restart: the revocation is still enforced.
    main.get_runtime.cache_clear()
    assert client.get(f"/share/{token}").status_code == 410


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


def test_correlation_id_echo_and_mint(sqlite_app):
    client, _ = sqlite_app
    echoed = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["X-Request-ID"] == "trace-abc-123"
    minted = client.get("/health")
    assert len(minted.headers["X-Request-ID"]) == 32  # minted uuid hex
    # Unusable client values (too long / wrong charset) are replaced, not trusted.
    replaced = client.get("/health", headers={"X-Request-ID": "x" * 65})
    assert replaced.headers["X-Request-ID"] != "x" * 65


def test_health_reports_actual_run_storage_mode(sqlite_app):
    client, _ = sqlite_app
    assert client.get("/health").json()["run_storage"] == "sqlite"


def test_bounded_concurrent_submissions_all_persist_distinctly(sqlite_app):
    """Representative concurrency smoke (12 writers, unique keys), not a load
    certification: it documents that the single SQLite writer serializes
    concurrent admissions without loss; capacity limits remain unmeasured."""
    from concurrent.futures import ThreadPoolExecutor

    client, path = sqlite_app
    def submit(index: int):
        response = client.post(
            "/analysis",
            headers={"X-Test-UID": "alice", "Idempotency-Key": f"durable-load-key-{index:04d}"},
            json={**WORKFLOW_REQUEST, "sample_id": f"load-{index}"},
        )
        return response.status_code, response.json().get("run_id")

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(submit, range(12)))
    assert all(status == 200 for status, _ in results)
    run_ids = {run_id for _, run_id in results}
    assert len(run_ids) == 12  # no admission lost, no aliasing
    history = client.get("/runs", headers=AUTH).json()
    assert {row["run_id"] for row in history} == run_ids
