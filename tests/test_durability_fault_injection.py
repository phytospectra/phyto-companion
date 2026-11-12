"""Interruption fault injection at the real API boundary.

These tests simulate process failures at every critical window of the
admission/execution and approval/continuation lifecycles and verify the
durable contract: state exists before effects, duplicates fail closed with
actionable guidance, reconciliation never re-executes, and the operator
resolution path records a decision instead of guessing.

Every interruption is injected through the actual HTTP stack (TestClient over
create_app with the real SQLite store); no persistence internals are mocked.
External-effect recovery is evidence-based operator work, not software
assertion: these tests establish the durable bookkeeping around that decision.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orchestrator import main
from orchestrator.run_store import SQLiteRunStore

AUTH = {"X-Test-UID": "alice"}
KEY = {"Idempotency-Key": "fault-injection-key-0001"}
WORKFLOW_REQUEST = {
    "sample_id": "fault-injection-test",
    "workflow": "single_compound",
    "dry_run": True,
}


@pytest.fixture()
def fault_app(monkeypatch, tmp_path):
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
        # installed on the exported app. Mirrors the real Firebase user
        # object shape the citation path reads (.uid and .email).
        uid = request.headers.get("X-Test-UID")
        if uid:
            request.state.user = __import__("types").SimpleNamespace(uid=uid, email=None)
        return await call_next(request)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, str(tmp_path / "runs.sqlite"), main.get_runtime()
    main.get_runtime.cache_clear()


def _reconcile_quiesced(path, run_id):
    """Operator procedure after stopping the service: confirm quiesced and
    record the uncertain outcome."""
    store = SQLiteRunStore(path)
    preview = store.reconcile_stale(
        dry_run=True, require_quiesced=True, run_ids=[run_id]
    )
    assert [e["run_id"] for e in preview] == [run_id]
    applied = store.reconcile_stale(
        dry_run=False, require_quiesced=True, run_ids=[run_id],
        operator="ops@example.org", reason="post-shutdown reconciliation",
    )
    assert [e["run_id"] for e in applied] == [run_id]
    assert store.load_run(run_id).outcome == "uncertain"


def test_crash_after_admission_before_execution(fault_app, monkeypatch):
    """A crash between admission and execution leaves a discoverable run row,
    never a bare idempotency claim."""
    client, path, runtime = fault_app
    real_engine = runtime.engine.run

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash before execution")

    monkeypatch.setattr(runtime.engine, "run", crash)
    response = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert response.status_code == 500

    store = SQLiteRunStore(path)
    record = store.load_run(_latest_run(store))
    assert record.outcome == "admitted"
    assert record.status == "running"
    assert record.worker_id
    assert record.lease_expires_at
    assert record.response is None

    # The API reports the admitted state truthfully.
    status = client.get(f"/runs/{record.run_id}", headers=AUTH)
    assert status.status_code == 200
    assert status.json()["outcome"] == "admitted"
    assert status.json()["status"] == "running"

    # A duplicate fails closed with the in-flight contract; nothing re-runs.
    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["run_id"] == record.run_id
    assert duplicate.json()["detail"]["outcome"] == "admitted"

    # Operator reconciliation (post-shutdown) → uncertain → resolution.
    _reconcile_quiesced(path, record.run_id)
    assert store.resolve_run(
        record.run_id, operator="ops@example.org",
        reason="execution never started", evidence="engine log empty for run",
    ) is True
    assert store.load_run(record.run_id).outcome == "failed"

    # The consumed key stays failed; only a new key may proceed.
    conflict = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["outcome"] == "failed"
    monkeypatch.setattr(runtime.engine, "run", real_engine)
    fresh = client.post(
        "/analysis",
        headers=AUTH | {"Idempotency-Key": "fault-injection-key-0002"},
        json=WORKFLOW_REQUEST,
    )
    assert fresh.status_code == 200


def test_crash_during_execution_after_effect(fault_app, monkeypatch):
    """Effects may have happened; reconciliation marks uncertain and the run
    is never re-executed."""
    client, path, runtime = fault_app
    calls = []
    real = runtime.engine.run

    def execute_then_crash(graph, context):
        calls.append(1)
        real(graph, context)
        raise RuntimeError("simulated crash after execution effects")

    monkeypatch.setattr(runtime.engine, "run", execute_then_crash)
    response = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert response.status_code == 500

    store = SQLiteRunStore(path)
    record = store.load_run(_latest_run(store))
    assert record.outcome == "admitted"  # final persistence never happened

    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 409  # in-flight/admitted, never replayed

    _reconcile_quiesced(path, record.run_id)
    assert store.load_run(record.run_id).outcome == "uncertain"
    uncertain = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert uncertain.status_code == 409
    assert uncertain.json()["detail"]["outcome"] == "uncertain"
    assert "reconciliation" in uncertain.json()["detail"]["message"]

    assert store.resolve_run(
        record.run_id, operator="ops@example.org", reason="abandoned"
    ) is True
    monkeypatch.setattr(runtime.engine, "run", real)
    fresh = client.post(
        "/analysis",
        headers=AUTH | {"Idempotency-Key": "fault-injection-key-0002"},
        json=WORKFLOW_REQUEST,
    )
    assert fresh.status_code == 200
    assert len(calls) == 1  # exactly one crashed execution; the fresh run is uncounted (real engine)


def test_crash_before_final_persistence(fault_app, monkeypatch):
    """A crash after the effect but before terminal persistence leaves the
    admitted record; the response is never replayed as a success."""
    client, path, runtime = fault_app
    original = runtime._persist_entry

    def persist_then_crash(run_id, entry, *, manifest_id, outcome, create=False):
        if not create:
            raise RuntimeError("simulated crash before final persistence")
        return original(run_id, entry, manifest_id=manifest_id, outcome=outcome, create=create)

    monkeypatch.setattr(runtime, "_persist_entry", persist_then_crash)
    response = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert response.status_code == 500

    store = SQLiteRunStore(path)
    record = store.load_run(_latest_run(store))
    assert record.outcome == "admitted"

    # Even a duplicate with identical content must not present the stored
    # creation response as a success while the run never reached a terminal
    # outcome: the outcome gate comes before the replay.
    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 409

    _reconcile_quiesced(path, record.run_id)
    assert store.resolve_run(
        record.run_id, operator="ops@example.org", reason="abandoned"
    ) is True


def test_approval_crash_before_admission_is_recoverable(fault_app, monkeypatch):
    """Failure before the approval is durably admitted must not wedge the run."""
    client, path, runtime = fault_app
    run_id = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST).json()["run_id"]
    approval = {"checkpoint": "before_confirmed_claim", "actor": "alice"}
    original_admit = runtime.run_store.admit_approval

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash before approval admission")

    monkeypatch.setattr(runtime.run_store, "admit_approval", crash)
    crashed = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert crashed.status_code == 500
    store = SQLiteRunStore(path)
    assert store.approval_state(run_id, "before_confirmed_claim") is None
    assert store.load_run(run_id).outcome == "awaiting_review"

    monkeypatch.setattr(runtime.run_store, "admit_approval", original_admit)
    retried = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert retried.status_code == 200
    assert retried.json()["status"] == "complete"


def test_approval_crash_before_continuation_never_blindly_reruns(fault_app, monkeypatch):
    """The approval is admitted durably first; a crash before the
    continuation leaves an explicit uncertain window that refuses retries."""
    client, path, runtime = fault_app
    calls = []
    real = runtime.engine.run

    def crash_on_continuation(graph, context):
        calls.append(1)
        if len(calls) == 1:  # the submission executes normally
            return real(graph, context)
        raise RuntimeError("simulated crash after approval admission, before continuation")

    monkeypatch.setattr(runtime.engine, "run", crash_on_continuation)
    run_id = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST).json()["run_id"]
    approval = {"checkpoint": "before_confirmed_claim", "actor": "alice"}
    crashed = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert crashed.status_code == 500

    store = SQLiteRunStore(path)
    assert store.approval_state(run_id, "before_confirmed_claim") == "admitted"
    record = store.load_run(run_id)
    assert record.outcome == "executing"
    assert record.status == "awaiting_review"  # pre-continuation operational state

    # The retry is refused with the uncertain-admission contract — the
    # continuation is never re-run automatically.
    retried = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert retried.status_code == 409
    assert "reconciliation" in retried.json()["detail"]["recovery"]

    _reconcile_quiesced(path, run_id)
    assert store.resolve_run(
        run_id, operator="ops@example.org", reason="continuation never started"
    ) is True
    assert client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval).status_code == 409
    assert len(calls) == 2  # submit + the one admitted continuation attempt

    # The safe forward path: a brand-new run with a new key.
    monkeypatch.setattr(runtime.engine, "run", real)
    fresh = client.post(
        "/analysis",
        headers=AUTH | {"Idempotency-Key": "fault-injection-key-0003"},
        json=WORKFLOW_REQUEST,
    )
    assert fresh.status_code == 200
    approved = client.post(
        f"/runs/{fresh.json()['run_id']}/approve", headers=AUTH, json=approval
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "complete"


def test_approval_crash_after_continuation_effect(fault_app, monkeypatch):
    """Effects happened but terminal persistence never did: reconciliation
    and resolution, never a second continuation."""
    client, path, runtime = fault_app
    calls = []
    real = runtime.engine.run

    def crash_after_continuation(graph, context):
        calls.append(1)
        result = real(graph, context)
        if len(calls) == 2:  # the continuation ran; the process dies after
            raise RuntimeError("simulated crash after continuation executed")
        return result

    monkeypatch.setattr(runtime.engine, "run", crash_after_continuation)
    run_id = client.post("/analysis", headers=AUTH, json=WORKFLOW_REQUEST).json()["run_id"]
    approval = {"checkpoint": "before_confirmed_claim", "actor": "alice"}
    original = runtime._persist_entry

    def persist_fails_terminal(run_id_arg, entry, *, manifest_id, outcome, create=False):
        if not create and outcome != "executing":
            raise RuntimeError("simulated crash before terminal approval persistence")
        return original(run_id_arg, entry, manifest_id=manifest_id, outcome=outcome, create=create)

    monkeypatch.setattr(runtime, "_persist_entry", persist_fails_terminal)
    crashed = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert crashed.status_code == 500

    store = SQLiteRunStore(path)
    assert store.approval_state(run_id, "before_confirmed_claim") == "admitted"
    assert store.load_run(run_id).outcome == "executing"  # pre-continuation record

    retried = client.post(f"/runs/{run_id}/approve", headers=AUTH, json=approval)
    assert retried.status_code == 409

    _reconcile_quiesced(path, run_id)
    assert store.resolve_run(
        run_id, operator="ops@example.org",
        reason="continuation effects unconfirmed", evidence="executor logs reviewed",
    ) is True
    assert len(calls) == 2  # the continuation ran exactly once, never again


def test_orphan_claim_fails_closed_and_is_discoverable(fault_app):
    """A key admitted with no run row (crash between admission and the
    pre-execution persist) is refused with reconciliation guidance and is
    listed by the reconciliation tooling."""
    client, path, runtime = fault_app
    store = SQLiteRunStore(path)

    from phyto_shared.provenance import sha256_dict
    from phyto_shared.schemas import AnalysisRequest

    # The same canonical content the handler hashes after model parsing.
    request_hash = sha256_dict(AnalysisRequest(**WORKFLOW_REQUEST).model_dump(mode="json"))
    store.admit(user_uid="alice", idem_key="orphan-bound-key-001", request_hash=request_hash, run_id="run-ghost")
    assert store.orphan_claims()[0]["run_id"] == "run-ghost"

    response = client.post(
        "/analysis",
        headers=AUTH | {"Idempotency-Key": "orphan-bound-key-001"},
        json=WORKFLOW_REQUEST,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["run_id"] == "run-ghost"
    assert "reconciliation" in response.json()["detail"]["message"]


def test_replay_survives_approval_share_and_revocation(fault_app):
    """The immutable creation response survives every later mutation."""
    client, path, runtime = fault_app
    first = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert first.status_code == 200
    run_id = first.json()["run_id"]

    client.post(
        f"/runs/{run_id}/approve", headers=AUTH,
        json={"checkpoint": "before_confirmed_claim", "actor": "alice"},
    )
    client.post(f"/runs/{run_id}/share", headers=AUTH, json={})
    client.delete(f"/runs/{run_id}/share", headers=AUTH)

    duplicate = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert duplicate.status_code == 200
    assert duplicate.json() == first.json()
    assert duplicate.headers.get("X-Idempotency-Replay") == "true"

    # And again across a simulated restart.
    main.get_runtime.cache_clear()
    replayed = client.post("/analysis", headers=AUTH | KEY, json=WORKFLOW_REQUEST)
    assert replayed.status_code == 200
    assert replayed.json() == first.json()


def _latest_run(store: SQLiteRunStore):
    rows = store.list_runs("alice", limit=1)
    assert rows, "expected a durable run row"
    return rows[0].run_id
