"""Durable run-store tests: restart, concurrency, idempotency, recovery.

All tests exercise the actual SQLiteRunStore implementation (real file, WAL,
BEGIN IMMEDIATE transitions) — no mocks of the persistence layer. These are
software durability guarantees, not scientific validation, and they never
claim exactly-once external execution: interrupted work is reconciled to
``uncertain``, never re-executed.

Stale-work policy under test (docs/DURABLE-RUN-STORE.md): a run is stale only
when its lease has expired (or the operator confirmed the system quiesced)
AND its owning worker is not live. Waiting-for-review runs are never stale,
a live worker's rows are never stale, and applying reconciliation requires a
recorded operator and reason.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from orchestrator.run_store import (
    InMemoryRunStore,
    KeyConflictError,
    RUN_SCHEMA_VERSION,
    RunRecord,
    SQLiteRunStore,
    StaleStateError,
    backup_sqlite_store,
    new_run_id,
)

FUTURE = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
V1_DDL = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    owner_uid TEXT NOT NULL,
    workflow TEXT NOT NULL,
    status TEXT NOT NULL,
    outcome TEXT NOT NULL,
    manifest_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX runs_owner ON runs(owner_uid, updated_at DESC);
CREATE TABLE idempotency (
    user_uid TEXT NOT NULL,
    idem_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_uid, idem_key)
);
CREATE TABLE approvals (
    run_id TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    PRIMARY KEY (run_id, checkpoint)
);
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
"""


def make_record(run_id=None, owner="alice", status="awaiting_review", outcome="awaiting_review"):
    return RunRecord(
        run_id=run_id or new_run_id(),
        owner_uid=owner,
        workflow="reference_hsqc_identify",
        status=status,
        outcome=outcome,
        manifest_id="m" * 64,
        state={"graph": {"run_id": run_id or "x"}, "request": {"sample_id": "s"}},
        created_at="2026-11-09T00:00:00+00:00",
        updated_at="2026-11-09T00:00:00+00:00",
    )


def make_admitted_record(worker="worker-1", lease=FUTURE):
    record = make_record(status="running", outcome="admitted")
    record.worker_id = worker
    record.lease_expires_at = lease
    return record


def test_restart_recovery_hydrates_state(tmp_path):
    path = tmp_path / "runs.sqlite"
    store = SQLiteRunStore(path)
    record = make_record(outcome="completed", status="complete")
    store.create_run(record)
    store.admit(user_uid="alice", idem_key="k" * 16, request_hash="h" * 64, run_id=record.run_id)
    store.record_approval(record.run_id, "before_confirmed_claim", "alice")
    store.record_response(record.run_id, {"run_id": record.run_id, "status": "complete"})

    # Simulated restart: a brand-new process opens the same file.
    reopened = SQLiteRunStore(path)
    loaded = reopened.load_run(record.run_id)
    assert loaded is not None
    assert loaded.state == record.state
    assert loaded.owner_uid == "alice"
    assert loaded.outcome == "completed"
    assert loaded.response == {"run_id": record.run_id, "status": "complete"}
    # Idempotency binding survives the restart.
    assert reopened.admit(
        user_uid="alice", idem_key="k" * 16, request_hash="h" * 64, run_id="other"
    ) == record.run_id
    assert reopened.list_runs("alice")[0].run_id == record.run_id


def test_migrations_apply_to_fresh_and_versioned_databases(tmp_path):
    SQLiteRunStore(tmp_path / "a.sqlite")
    with sqlite3.connect(tmp_path / "a.sqlite") as conn:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    assert versions == set(range(1, RUN_SCHEMA_VERSION + 1))
    # Reopening an already-migrated database is a no-op and keeps data.
    again = SQLiteRunStore(tmp_path / "a.sqlite")
    record = make_record()
    again.create_run(record)
    assert SQLiteRunStore(tmp_path / "a.sqlite").load_run(record.run_id) is not None


def test_v1_database_migrates_response_out_and_relabels_review_pending(tmp_path):
    """Schema v2: the creation response leaves mutable state, and
    review-pending rows stop masquerading as executing."""
    path = tmp_path / "v1.sqlite"
    with sqlite3.connect(path) as conn:
        for statement in V1_DDL.split(";"):
            if statement.strip():
                conn.execute(statement)
        state = json.dumps(
            {
                "graph": {"run_id": "run-old"},
                "request": {"sample_id": "s"},
                "response": {"run_id": "run-old", "status": "awaiting_review"},
            },
            sort_keys=True,
        )
        conn.execute(
            "INSERT INTO runs(run_id, owner_uid, workflow, status, outcome,"
            " manifest_id, state_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "run-old", "alice", "single_compound", "awaiting_review",
                "executing", "m" * 64, state,
                "2026-11-09T00:00:00+00:00", "2026-11-09T00:00:00+00:00",
            ),
        )
        conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (1, 'x')")

    store = SQLiteRunStore(path)
    record = store.load_run("run-old")
    assert record.outcome == "awaiting_review"  # relabelled, never treated as interrupted
    assert "response" not in record.state
    assert record.response == {"run_id": "run-old", "status": "awaiting_review"}
    # A review-pending run is never selected for reconciliation.
    assert store.reconcile_stale(dry_run=True, require_quiesced=True) == []


def test_transactional_transition_serializes_concurrent_workers(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="awaiting_review", outcome="awaiting_review")
    store.create_run(record)
    winners = []

    def attempt(worker):
        reopened = SQLiteRunStore(tmp_path / "runs.sqlite")  # own connection
        result = reopened.transition(
            record.run_id,
            expected_status="awaiting_review",
            mutator=lambda r: (
                setattr(r, "status", "complete"),
                setattr(r, "outcome", "completed"),
            ),
        )
        if result is not None:
            winners.append(worker)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(attempt, range(4)))
    assert len(winners) == 1  # exactly one worker performed the transition
    final = store.load_run(record.run_id)
    assert final.status == "complete" and final.outcome == "completed"


def test_transition_rejects_stale_expected_status(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="complete", outcome="completed")
    store.create_run(record)
    assert store.transition(record.run_id, "awaiting_review", lambda r: None) is None


def test_save_run_cas_refuses_stale_writes(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="running", outcome="admitted")
    store.create_run(record)
    first = store.load_run(record.run_id)
    store.save_run(first, expected_updated_at=first.updated_at)  # fresh: ok
    with pytest.raises(StaleStateError):
        store.save_run(first, expected_updated_at=first.updated_at)  # stale: refused


def test_idempotency_replay_and_content_conflict(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    first = make_record(outcome="completed")
    store.create_run(first)
    store.admit(user_uid="alice", idem_key="key-0000000000001", request_hash="hash-a", run_id=first.run_id)
    # Same content: duplicate admission returns the original run.
    assert store.admit(
        user_uid="alice", idem_key="key-0000000000001", request_hash="hash-a", run_id="new"
    ) == first.run_id
    # Same key, different canonical content: rejected, original binding intact.
    with pytest.raises(KeyConflictError):
        store.admit(user_uid="alice", idem_key="key-0000000000001", request_hash="hash-b", run_id="new")
    # Keys are user-scoped: bob may use the same key string.
    bob = make_record(owner="bob")
    store.create_run(bob)
    assert store.admit(
        user_uid="bob", idem_key="key-0000000000001", request_hash="hash-b", run_id=bob.run_id
    ) == bob.run_id
    # Keys are never cleared: still bound after terminal outcome.
    assert store.admit(
        user_uid="alice", idem_key="key-0000000000001", request_hash="hash-a", run_id="x"
    ) == first.run_id
    # Read-only lookup matches the binding.
    assert store.lookup_admission("alice", "key-0000000000001") == ("hash-a", first.run_id)
    assert store.lookup_admission("alice", "never-admitted-01") is None


def test_parallel_same_key_admits_exactly_one_run(tmp_path):
    SQLiteRunStore(tmp_path / "runs.sqlite")
    barrier = threading.Barrier(4)
    admitted = []

    def attempt(index):
        reopened = SQLiteRunStore(tmp_path / "runs.sqlite")
        barrier.wait()
        record = make_record(owner=f"u{index}")
        try:
            bound = reopened.admit(
                user_uid="shared-user", idem_key="parallel-key-0001",
                request_hash="same-hash", run_id=record.run_id,
            )
        except Exception:
            return
        admitted.append(bound)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(attempt, range(4)))
    assert len(admitted) == 4
    assert len(set(admitted)) == 1  # every racer got the single winning run


def test_interrupted_execution_reconciles_to_uncertain_never_reruns(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    executing = make_record(status="executing", outcome="executing")
    executing.worker_id = None
    executing.lease_expires_at = None
    store.create_run(executing)
    stale = store.reconcile_stale(dry_run=True)
    assert stale == [{"run_id": executing.run_id, "reason": "no lease recorded and worker not live"}]
    assert store.load_run(executing.run_id).outcome == "executing"  # dry run
    with pytest.raises(ValueError):  # apply must record the operator + reason
        store.reconcile_stale(dry_run=False)
    applied = store.reconcile_stale(
        dry_run=False, operator="ops@example.org", reason="crash window; no lease"
    )
    assert applied == [{"run_id": executing.run_id, "reason": "no lease recorded and worker not live"}]
    record = store.load_run(executing.run_id)
    assert record.outcome == "uncertain"
    assert record.status == "executing"  # operational state untouched
    assert store.reconcile_stale(dry_run=False, operator="x", reason="y") == []
    # The transition was recorded with the operator, reason and evidence path.
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        rows = list(conn.execute(
            "SELECT operator, reason, from_outcome, to_outcome FROM reconciliations"
        ))
    assert rows == [("ops@example.org", "crash window; no lease", "executing", "uncertain")]


def test_reconciliation_selectivity(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    pending_review = make_record(status="awaiting_review", outcome="awaiting_review")
    store.create_run(pending_review)
    active = make_admitted_record(worker="worker-1", lease=FUTURE)
    store.create_run(active)
    store.register_worker("worker-1")
    store.heartbeat("worker-1")
    expired = make_admitted_record(worker="worker-2", lease="2020-01-01T00:00:00+00:00")
    store.create_run(expired)
    later = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()

    # Waiting-for-review is never selected — even with the strongest
    # quiesced-operator override.
    assert {e["run_id"] for e in store.reconcile_stale(dry_run=True, require_quiesced=True)} == {
        active.run_id, expired.run_id
    }
    # A live worker's row is never selected, and an unexpired lease alone
    # protects a dead worker's row too (conservative: elapsed time is not a
    # staleness verdict).
    stale = store.reconcile_stale(dry_run=True, now=FUTURE)
    assert [e["run_id"] for e in stale] == [expired.run_id]
    assert stale[0]["reason"] == "lease expired and worker not live"
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        conn.execute("DELETE FROM workers WHERE worker_id='worker-1'")
    assert store.reconcile_stale(dry_run=True, now=FUTURE) == stale  # lease still active
    # Only once the lease itself expires is the dead worker's run stale.
    stale = store.reconcile_stale(dry_run=True, now=later)
    assert {e["run_id"] for e in stale} == {active.run_id, expired.run_id}


def test_explicit_run_selection_and_quiesced_override(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    a = make_admitted_record(worker="worker-a", lease="2020-01-01T00:00:00+00:00")
    b = make_admitted_record(worker="worker-b", lease="2020-01-01T00:00:00+00:00")
    store.create_run(a)
    store.create_run(b)
    # Explicit selection: only the named run is affected.
    assert [e["run_id"] for e in store.reconcile_stale(dry_run=True, run_ids=[a.run_id])] == [a.run_id]
    # Quiesced-operator override: any admitted/executing run is stale,
    # scoped to the named run here.
    active_lease = make_admitted_record(worker="worker-c", lease=FUTURE)
    store.create_run(active_lease)
    store.register_worker("worker-c")
    store.heartbeat("worker-c")
    assert [e["run_id"] for e in store.reconcile_stale(
        dry_run=True, require_quiesced=True, run_ids=[active_lease.run_id]
    )] == [active_lease.run_id]
    # Nothing was applied by dry runs.
    assert store.load_run(a.run_id).outcome == "admitted"


def test_concurrent_reconciliation_applies_once(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="running", outcome="admitted")
    record.worker_id = None
    record.lease_expires_at = None
    store.create_run(record)
    results = []

    def reconcile(index):
        reopened = SQLiteRunStore(tmp_path / "runs.sqlite")
        results.append(
            reopened.reconcile_stale(
                dry_run=False, operator=f"op-{index}", reason="concurrent"
            )
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(reconcile, range(4)))
    assert store.load_run(record.run_id).outcome == "uncertain"
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        rows = list(conn.execute("SELECT COUNT(*) FROM reconciliations"))
    assert rows[0][0] == 1  # exactly one reconciliation row survives the race


def test_resolve_run_records_decision_and_never_reruns(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="running", outcome="uncertain")
    store.create_run(record)
    store.record_response(record.run_id, {"run_id": record.run_id, "status": "complete"})
    resolved = store.resolve_run(
        record.run_id, operator="ops@example.org",
        reason="no executor evidence of completion", evidence="log tail 2026-11-12",
    )
    assert resolved is True
    final = store.load_run(record.run_id)
    assert final.outcome == "failed" and final.status == "failed"
    # The creation response is preserved even though the run was abandoned.
    assert final.response == {"run_id": record.run_id, "status": "complete"}
    # Terminal runs cannot be resolved again.
    assert store.resolve_run(record.run_id, operator="ops", reason="twice") is False
    # The idempotency binding is untouched (keys are never cleared).
    store.admit(user_uid="alice", idem_key="k" * 16, request_hash="h" * 64, run_id=record.run_id)
    assert store.lookup_admission("alice", "k" * 16) == ("h" * 64, record.run_id)


def test_durable_approval_lifecycle(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record()
    store.create_run(record)
    assert store.admit_approval(record.run_id, "before_confirmed_claim", "alice") is True
    assert store.approval_state(record.run_id, "before_confirmed_claim") == "admitted"
    reopened = SQLiteRunStore(tmp_path / "runs.sqlite")
    assert reopened.admit_approval(record.run_id, "before_confirmed_claim", "bob") is False
    assert [a["run_id"] for a in reopened.admitted_approvals()] == [record.run_id]
    assert reopened.complete_approval(record.run_id, "before_confirmed_claim") is True
    assert reopened.complete_approval(record.run_id, "before_confirmed_claim") is False
    assert reopened.approval_state(record.run_id, "before_confirmed_claim") == "executed"
    assert reopened.admitted_approvals() == []
    # record_approval keeps its executed-once semantics.
    assert store.record_approval(record.run_id, "other_checkpoint", "alice") is True
    assert store.record_approval(record.run_id, "other_checkpoint", "bob") is False


def test_creation_response_is_immutable(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(outcome="completed", status="complete")
    store.create_run(record)
    first = {"run_id": record.run_id, "status": "complete"}
    assert store.record_response(record.run_id, first) is True
    # Later saves of mutable state must not touch the response.
    mutated = store.load_run(record.run_id)
    mutated.status = "complete"
    mutated.state = {**mutated.state, "share_token": "tok-1"}
    store.save_run(mutated, expected_updated_at=mutated.updated_at)
    assert store.load_run(record.run_id).response == first
    # A second write to the response is refused: the first creation wins.
    assert store.record_response(record.run_id, {"run_id": record.run_id, "status": "failed"}) is False
    assert store.load_run(record.run_id).response == first


def test_orphan_claims_are_discoverable(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    store.admit(user_uid="alice", idem_key="orphan-key-0000001", request_hash="h" * 64, run_id="run-ghost")
    assert store.orphan_claims() == [
        {
            "user_uid": "alice",
            "idem_key": "orphan-key-0000001",
            "request_hash": "h" * 64,
            "run_id": "run-ghost",
        }
    ]
    record = make_record(run_id="run-ghost")
    store.create_run(record)
    assert store.orphan_claims() == []


def test_backup_and_restore_round_trip(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(outcome="completed")
    store.create_run(record)
    backup = backup_sqlite_store(store, tmp_path / "backup.sqlite")
    # Restore = open the backup copy as the live store.
    restored = SQLiteRunStore(backup)
    assert restored.load_run(record.run_id).state == record.state


def test_ownership_is_enforced_by_the_store_data(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(owner="alice")
    store.create_run(record)
    assert store.load_run(record.run_id).owner_uid == "alice"
    assert [r.run_id for r in store.list_runs("bob")] == []
    # Raw row-level check: no cross-tenant leakage in persisted state.
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        owners = {row[0] for row in conn.execute("SELECT owner_uid FROM runs")}
    assert owners == {"alice"}


def test_list_runs_is_bounded_and_offset(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    for index in range(5):
        record = make_record(owner="alice")
        record.updated_at = f"2026-11-09T00:0{index}:00+00:00"
        store.create_run(record)
    newest_first = store.list_runs("alice", limit=2)
    assert [r.updated_at for r in newest_first] == [
        "2026-11-09T00:04:00+00:00", "2026-11-09T00:03:00+00:00"
    ]
    page_two = store.list_runs("alice", limit=2, offset=2)
    assert [r.updated_at for r in page_two] == [
        "2026-11-09T00:02:00+00:00", "2026-11-09T00:01:00+00:00"
    ]


def test_in_memory_store_matches_protocol_semantics():
    store = InMemoryRunStore()
    record = make_record(outcome="completed")
    store.create_run(record)
    assert store.admit(user_uid="a", idem_key="k" * 16, request_hash="h", run_id=record.run_id) == record.run_id
    with pytest.raises(KeyConflictError):
        store.admit(user_uid="a", idem_key="k" * 16, request_hash="different", run_id="x")
    assert store.load_run(record.run_id) is not None
    assert store.record_response(record.run_id, {"ok": True}) is True
    assert store.record_response(record.run_id, {"ok": False}) is False
    stale = make_record(status="running", outcome="executing")
    stale.worker_id = None
    stale.lease_expires_at = None
    store.create_run(stale)
    assert store.reconcile_stale(dry_run=True) == [{"run_id": stale.run_id, "reason": "no lease recorded and worker not live"}]
    with pytest.raises(ValueError):
        store.reconcile_stale(dry_run=False)
    store.reconcile_stale(dry_run=False, operator="ops", reason="test")
    assert store.load_run(stale.run_id).outcome == "uncertain"
    assert store.resolve_run(stale.run_id, operator="ops", reason="abandon") is True
    assert store.load_run(stale.run_id).outcome == "failed"


def test_persisted_state_is_canonical_json(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record()
    store.create_run(record)
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        raw = conn.execute("SELECT state_json FROM runs").fetchone()[0]
    assert json.loads(raw) == record.state  # round-trips through canonical JSON
