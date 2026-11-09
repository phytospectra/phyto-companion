"""Durable run-store tests: restart, concurrency, idempotency, recovery.

All tests exercise the actual SQLiteRunStore implementation (real file, WAL,
BEGIN IMMEDIATE transitions) — no mocks of the persistence layer. These are
software durability guarantees, not scientific validation, and they never
claim exactly-once external execution: interrupted work is reconciled to
``uncertain``, never re-executed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestrator.run_store import (
    InMemoryRunStore,
    KeyConflictError,
    RUN_SCHEMA_VERSION,
    RunRecord,
    SQLiteRunStore,
    backup_sqlite_store,
    new_run_id,
)


def make_record(run_id=None, owner="alice", status="awaiting_review", outcome="executing"):
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


def test_restart_recovery_hydrates_state(tmp_path):
    path = tmp_path / "runs.sqlite"
    store = SQLiteRunStore(path)
    record = make_record(outcome="completed", status="complete")
    store.create_run(record)
    store.admit(user_uid="alice", idem_key="k" * 16, request_hash="h" * 64, run_id=record.run_id)
    store.record_approval(record.run_id, "before_confirmed_claim", "alice")

    # Simulated restart: a brand-new process opens the same file.
    reopened = SQLiteRunStore(path)
    loaded = reopened.load_run(record.run_id)
    assert loaded is not None
    assert loaded.state == record.state
    assert loaded.owner_uid == "alice"
    assert loaded.outcome == "completed"
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


def test_transactional_transition_serializes_concurrent_workers(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record(status="awaiting_review", outcome="executing")
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
    store.create_run(executing)
    stale = store.reconcile_stale_executing(dry_run=True)
    assert stale == [executing.run_id]
    assert store.load_run(executing.run_id).outcome == "executing"  # dry run
    applied = store.reconcile_stale_executing(dry_run=False)
    assert applied == [executing.run_id]
    record = store.load_run(executing.run_id)
    assert record.outcome == "uncertain"
    assert record.status == "executing"  # operational state untouched
    assert store.reconcile_stale_executing(dry_run=False) == []


def test_durable_approval_exactly_once(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record()
    store.create_run(record)
    assert store.record_approval(record.run_id, "before_confirmed_claim", "alice") is True
    reopened = SQLiteRunStore(tmp_path / "runs.sqlite")
    assert reopened.record_approval(record.run_id, "before_confirmed_claim", "bob") is False


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


def test_in_memory_store_matches_protocol_semantics():
    store = InMemoryRunStore()
    record = make_record()
    store.create_run(record)
    assert store.admit(user_uid="a", idem_key="k" * 16, request_hash="h", run_id=record.run_id) == record.run_id
    with pytest.raises(KeyConflictError):
        store.admit(user_uid="a", idem_key="k" * 16, request_hash="different", run_id="x")
    assert store.load_run(record.run_id) is not None
    assert store.reconcile_stale_executing(dry_run=False) == [record.run_id]
    assert store.load_run(record.run_id).outcome == "uncertain"


def test_persisted_state_is_canonical_json(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    record = make_record()
    store.create_run(record)
    with sqlite3.connect(tmp_path / "runs.sqlite") as conn:
        raw = conn.execute("SELECT state_json FROM runs").fetchone()[0]
    assert json.loads(raw) == record.state  # round-trips through canonical JSON
