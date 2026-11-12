"""Durable run/task/checkpoint persistence for the orchestrator.

Two implementations behind one protocol:

* :class:`InMemoryRunStore` — deterministic, process-local. Offline tests and
  the local mock plane only; nothing here survives a restart.
* :class:`SQLiteRunStore` — the durable store. One file, WAL journaling,
  ``BEGIN IMMEDIATE`` write transactions for cross-process transition
  serialization, and a versioned migration table. stdlib-only by design:
  this repository has no concrete need for a database server, and adding
  one would add an unmanaged operational dependency.

Run outcomes (the durable lifecycle, distinct from the engine's operational
``status``): ``admitted`` (durably recorded before execution starts),
``executing`` (a continuation is running), ``awaiting_review`` (waiting for a
human checkpoint decision — never mistaken for interrupted work),
``completed``, ``failed`` and ``uncertain`` (reconciliation concluded the
outcome is unknown).

Stale ownership: every created run records the owning ``worker_id`` (a
process-generation identifier) and a ``lease_expires_at`` deadline, and the
worker renews a liveness heartbeat while it serves requests. Reconciliation
marks a run ``uncertain`` only when its lease has expired (or the operator
explicitly confirms the system is quiesced) AND its owner is not live; a
healthy in-flight run and every ``awaiting_review`` run are never selected.
Elapsed time alone never authorizes replay: reconciliation records the
operator, reason and evidence and never re-executes work.

Guarantees actually provided (see docs/ACCOUNTING-GUARANTEE.md for the
request-key semantics): durable run/task/checkpoint/approval state that
survives restarts, transactional single-writer state transitions, and
idempotency keys bound to a canonical request-content hash. NOT provided:
exactly-once execution of external effects. A run interrupted mid-execution
is persisted as ``admitted``/``executing`` and must be reconciled by an
operator to ``uncertain``; reconciliation never re-executes work.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from phyto_shared.provenance import sha256_dict

__all__ = [
    "RunRecord",
    "RunStore",
    "InMemoryRunStore",
    "SQLiteRunStore",
    "SQLiteShareRegistry",
    "KeyConflictError",
    "StaleStateError",
    "open_run_store",
    "RUN_SCHEMA_VERSION",
    "backup_sqlite_store",
    "new_run_id",
    "request_content_hash",
]

RUN_SCHEMA_VERSION = 2

Outcome = Literal[
    "admitted", "executing", "awaiting_review", "completed", "failed", "uncertain"
]


def _migrate_v2_data(conn: sqlite3.Connection) -> None:
    """Split the immutable creation response out of mutable run state.

    Schema v1 stored the creation response inside ``state_json`` so that any
    later save could (and did) overwrite it with ``None``. v2 moves it to a
    dedicated ``response_json`` column that no update path ever touches, and
    re-labels review-pending rows (previously persisted as ``executing``) so
    reconciliation can distinguish waiting-for-review from active execution.
    """
    rows = conn.execute("SELECT run_id, state_json FROM runs").fetchall()
    for row in rows:
        state = json.loads(row["state_json"])
        response = state.pop("response", None)
        conn.execute(
            "UPDATE runs SET response_json=?, state_json=? WHERE run_id=?",
            (
                json.dumps(response, sort_keys=True) if response is not None else None,
                json.dumps(state, sort_keys=True),
                row["run_id"],
            ),
        )
    conn.execute(
        "UPDATE runs SET outcome='awaiting_review' "
        "WHERE outcome='executing' AND status='awaiting_review'"
    )


_MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None] | None]] = [
    (
        1,
        """
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
        """,
        None,
    ),
    (
        2,
        """
        ALTER TABLE runs ADD COLUMN response_json TEXT;
        ALTER TABLE runs ADD COLUMN worker_id TEXT;
        ALTER TABLE runs ADD COLUMN lease_expires_at TEXT;
        ALTER TABLE approvals ADD COLUMN status TEXT NOT NULL DEFAULT 'executed';
        ALTER TABLE approvals ADD COLUMN execution_started_at TEXT;
        ALTER TABLE approvals ADD COLUMN execution_completed_at TEXT;
        CREATE TABLE IF NOT EXISTS reconciliations (
            run_id TEXT NOT NULL,
            operator TEXT NOT NULL,
            reason TEXT NOT NULL,
            from_outcome TEXT NOT NULL,
            to_outcome TEXT NOT NULL,
            evidence TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (run_id, recorded_at)
        );
        CREATE TABLE IF NOT EXISTS workers (
            worker_id TEXT PRIMARY KEY,
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS share_tokens (
            token_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS share_revocations (
            run_id TEXT PRIMARY KEY,
            revoked_at TEXT NOT NULL
        );
        """,
        _migrate_v2_data,
    ),
]


class KeyConflictError(Exception):
    """An idempotency key was reused with different request content."""


class StaleStateError(Exception):
    """A save was refused because the durable row moved on since it was read."""


class RunRecord:
    """One run's durable state (graph, context, result, request, extras)."""

    __slots__ = (
        "run_id", "owner_uid", "workflow", "status", "outcome",
        "manifest_id", "state", "response", "worker_id", "lease_expires_at",
        "created_at", "updated_at",
    )

    def __init__(
        self,
        *,
        run_id: str,
        owner_uid: str,
        workflow: str,
        status: str,
        outcome: str,
        manifest_id: str,
        state: dict[str, Any],
        created_at: str,
        updated_at: str,
        response: dict[str, Any] | None = None,
        worker_id: str | None = None,
        lease_expires_at: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.owner_uid = owner_uid
        self.workflow = workflow
        self.status = status
        self.outcome = outcome
        self.manifest_id = manifest_id
        self.state = state
        self.response = response
        self.worker_id = worker_id
        self.lease_expires_at = lease_expires_at
        self.created_at = created_at
        self.updated_at = updated_at


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore(Protocol):
    mode: Literal["memory", "sqlite"]

    def create_run(self, record: RunRecord) -> None: ...
    def load_run(self, run_id: str) -> RunRecord | None: ...
    def save_run(self, record: RunRecord, *, expected_updated_at: str | None = None) -> None: ...
    def transition(
        self, run_id: str, expected_status: str, mutator
    ) -> RunRecord | None:
        ...

    def record_approval(
        self, run_id: str, checkpoint: str, approved_by: str
    ) -> bool: ...
    def admit_approval(
        self, run_id: str, checkpoint: str, approved_by: str
    ) -> bool: ...
    def approval_state(self, run_id: str, checkpoint: str) -> str | None: ...
    def complete_approval(self, run_id: str, checkpoint: str) -> bool: ...
    def admitted_approvals(self) -> list[dict[str, str]]: ...
    def record_response(self, run_id: str, response: dict[str, Any]) -> bool:
        """Store the immutable creation response; the first write wins."""
    ...

    def admit(
        self, *, user_uid: str, idem_key: str, request_hash: str, run_id: str
    ) -> str:
        """Return the run_id bound to (user_uid, idem_key).

        Same request hash returns the existing run (duplicate admission).
        A different hash raises :class:`KeyConflictError`. Keys are never
        cleared automatically: recovery is a new key, never key deletion.
        """
    ...
    def lookup_admission(
        self, user_uid: str, idem_key: str
    ) -> tuple[str, str] | None:
        """Read-only (request_hash, run_id) bound to (user_uid, idem_key)."""
    ...
    def orphan_claims(self) -> list[dict[str, str]]: ...
    def list_runs(self, owner_uid: str, limit: int = 50, offset: int = 0) -> list[RunRecord]: ...

    def register_worker(self, worker_id: str) -> None: ...
    def heartbeat(self, worker_id: str) -> None: ...
    def worker_live(
        self, worker_id: str | None, *, now: str, heartbeat_ttl_seconds: float
    ) -> bool: ...

    def reconcile_stale(
        self,
        *,
        dry_run: bool = True,
        run_ids: list[str] | None = None,
        require_quiesced: bool = False,
        operator: str | None = None,
        reason: str | None = None,
        now: str | None = None,
        heartbeat_ttl_seconds: float = 60.0,
    ) -> list[dict[str, str]]:
        """Mark stale ``admitted``/``executing`` runs ``uncertain``.

        Never selects ``awaiting_review`` rows and never re-executes work.
        """
    ...
    def resolve_run(
        self,
        run_id: str,
        *,
        operator: str,
        reason: str,
        evidence: str | None = None,
    ) -> bool:
        """Abandon a non-terminal run: terminal ``failed`` with a recorded
        decision. Never re-executes and never clears the idempotency key."""
    ...


def _apply_staleness(
    record: RunRecord,
    *,
    require_quiesced: bool,
    now: str,
    worker_live: bool,
) -> tuple[bool, str]:
    """Decide whether one admitted/executing row is stale, with the reason.

    A live worker's rows are never stale: the heartbeat protects healthy
    long-running work. Otherwise the lease must have expired (or the operator
    must have confirmed the system quiesced) before anything is touched.
    """
    if require_quiesced:
        return True, "operator confirmed quiesced"
    if worker_live:
        return False, "worker live"
    lease = record.lease_expires_at
    if lease is None:
        return True, "no lease recorded and worker not live"
    if lease < now:
        return True, "lease expired and worker not live"
    return False, "lease active"


class InMemoryRunStore:
    """Process-local store; offline tests and the local mock plane only."""

    mode: Literal["memory", "sqlite"] = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._approvals: dict[tuple[str, str], str] = {}
        self._workers: dict[str, str] = {}
        self._reconciliations: list[dict[str, str]] = []

    def create_run(self, record: RunRecord) -> None:
        with self._lock:
            if record.run_id in self._runs:
                raise ValueError(f"run {record.run_id} already exists")
            self._runs[record.run_id] = record

    def load_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._runs.get(run_id)

    def save_run(self, record: RunRecord, *, expected_updated_at: str | None = None) -> None:
        with self._lock:
            if record.run_id not in self._runs:
                raise ValueError(f"unknown run {record.run_id}")
            current = self._runs[record.run_id]
            if expected_updated_at is not None and current.updated_at != expected_updated_at:
                raise StaleStateError(
                    f"run {record.run_id} changed since it was read; reload and retry"
                )
            # Mutable columns only; the creation response, worker and lease
            # are immutable once created.
            current.status = record.status
            current.outcome = record.outcome
            current.state = record.state
            current.updated_at = _utcnow()

    def transition(self, run_id: str, expected_status: str, mutator):
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.status != expected_status:
                return None
            mutator(record)
            record.updated_at = _utcnow()
            return record

    def record_approval(self, run_id: str, checkpoint: str, approved_by: str) -> bool:
        with self._lock:
            key = (run_id, checkpoint)
            if key in self._approvals:
                return False
            self._approvals[key] = "executed"
            return True

    def admit_approval(self, run_id: str, checkpoint: str, approved_by: str) -> bool:
        with self._lock:
            key = (run_id, checkpoint)
            if key in self._approvals:
                return False
            self._approvals[key] = "admitted"
            return True

    def approval_state(self, run_id: str, checkpoint: str) -> str | None:
        with self._lock:
            return self._approvals.get((run_id, checkpoint))

    def complete_approval(self, run_id: str, checkpoint: str) -> bool:
        with self._lock:
            key = (run_id, checkpoint)
            if self._approvals.get(key) != "admitted":
                return False
            self._approvals[key] = "executed"
            return True

    def admitted_approvals(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {"run_id": run_id, "checkpoint": checkpoint}
                for (run_id, checkpoint), status in self._approvals.items()
                if status == "admitted"
            ]

    def record_response(self, run_id: str, response: dict[str, Any]) -> bool:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.response is not None:
                return False
            record.response = response
            return True

    def admit(self, *, user_uid: str, idem_key: str, request_hash: str, run_id: str) -> str:
        with self._lock:
            key = (user_uid, idem_key)
            existing = self._keys.get(key)
            if existing is not None:
                bound_hash, bound_run = existing
                if bound_hash != request_hash:
                    raise KeyConflictError(
                        "Idempotency-Key already bound to different request content"
                    )
                return bound_run
            self._keys[key] = (request_hash, run_id)
            return run_id

    def lookup_admission(self, user_uid: str, idem_key: str) -> tuple[str, str] | None:
        with self._lock:
            return self._keys.get((user_uid, idem_key))

    def orphan_claims(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    "user_uid": user_uid,
                    "idem_key": idem_key,
                    "request_hash": request_hash,
                    "run_id": bound_run,
                }
                for (user_uid, idem_key), (request_hash, bound_run) in self._keys.items()
                if bound_run not in self._runs
            ]

    def list_runs(self, owner_uid: str, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        with self._lock:
            runs = [r for r in self._runs.values() if r.owner_uid == owner_uid]
            runs.sort(key=lambda r: r.updated_at, reverse=True)
            return runs[offset : offset + limit]

    def register_worker(self, worker_id: str) -> None:
        with self._lock:
            self._workers[worker_id] = _utcnow()

    def heartbeat(self, worker_id: str) -> None:
        with self._lock:
            self._workers[worker_id] = _utcnow()

    def worker_live(self, worker_id: str | None, *, now: str, heartbeat_ttl_seconds: float) -> bool:
        with self._lock:
            if worker_id is None:
                return False
            seen = self._workers.get(worker_id)
            if seen is None:
                return False
            return (datetime.fromisoformat(now) - datetime.fromisoformat(seen)).total_seconds() <= heartbeat_ttl_seconds

    def reconcile_stale(
        self,
        *,
        dry_run: bool = True,
        run_ids: list[str] | None = None,
        require_quiesced: bool = False,
        operator: str | None = None,
        reason: str | None = None,
        now: str | None = None,
        heartbeat_ttl_seconds: float = 60.0,
    ) -> list[dict[str, str]]:
        with self._lock:
            if not dry_run and (not operator or not reason):
                raise ValueError("apply requires an operator and reason")
            moment = now or _utcnow()
            result: list[dict[str, str]] = []
            for record in self._runs.values():
                if record.outcome not in ("admitted", "executing"):
                    continue
                if run_ids is not None and record.run_id not in run_ids:
                    continue
                stale, why = _apply_staleness(
                    record,
                    require_quiesced=require_quiesced,
                    now=moment,
                    worker_live=self.worker_live(
                        record.worker_id,
                        now=moment,
                        heartbeat_ttl_seconds=heartbeat_ttl_seconds,
                    ),
                )
                if not stale:
                    continue
                result.append({"run_id": record.run_id, "reason": why})
                if not dry_run:
                    from_outcome = record.outcome
                    record.outcome = "uncertain"
                    record.updated_at = moment
                    self._reconciliations.append(
                        {
                            "run_id": record.run_id,
                            "operator": operator,
                            "reason": reason,
                            "from_outcome": from_outcome,
                            "to_outcome": "uncertain",
                            "evidence": None,
                            "recorded_at": moment,
                        }
                    )
            return result

    def resolve_run(
        self,
        run_id: str,
        *,
        operator: str,
        reason: str,
        evidence: str | None = None,
    ) -> bool:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None or record.outcome in ("completed", "failed"):
                return False
            self._reconciliations.append(
                {
                    "run_id": run_id,
                    "operator": operator,
                    "reason": reason,
                    "from_outcome": record.outcome,
                    "to_outcome": "failed",
                    "evidence": evidence,
                    "recorded_at": _utcnow(),
                }
            )
            record.outcome = "failed"
            record.status = "failed"
            record.updated_at = _utcnow()
            return True


class SQLiteRunStore:
    """Durable single-file store with transactional transitions."""

    mode: Literal["memory", "sqlite"] = "sqlite"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._migrate()

    # -- connection ------------------------------------------------------
    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.path, timeout=30.0, isolation_level=None  # autocommit
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _migrate(self) -> None:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations")
            }
            for version, script, data_migration in _MIGRATIONS:
                if version in applied:
                    continue
                # executescript() would auto-commit the open transaction;
                # run each DDL statement individually instead.
                for statement in script.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                if data_migration is not None:
                    data_migration(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, _utcnow()),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -- records ---------------------------------------------------------
    @staticmethod
    def _to_record(row: sqlite3.Row) -> RunRecord:
        response = row["response_json"]
        return RunRecord(
            run_id=row["run_id"],
            owner_uid=row["owner_uid"],
            workflow=row["workflow"],
            status=row["status"],
            outcome=row["outcome"],
            manifest_id=row["manifest_id"],
            state=json.loads(row["state_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            response=json.loads(response) if response else None,
            worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"],
        )

    def create_run(self, record: RunRecord) -> None:
        self._conn.execute(
            "INSERT INTO runs(run_id, owner_uid, workflow, status, outcome,"
            " manifest_id, state_json, response_json, worker_id,"
            " lease_expires_at, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.run_id, record.owner_uid, record.workflow, record.status,
                record.outcome, record.manifest_id,
                json.dumps(record.state, sort_keys=True),
                json.dumps(record.response, sort_keys=True)
                if record.response is not None
                else None,
                record.worker_id,
                record.lease_expires_at,
                record.created_at, record.updated_at,
            ),
        )

    def load_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else self._to_record(row)

    def save_run(self, record: RunRecord, *, expected_updated_at: str | None = None) -> None:
        # Only mutable columns. The creation response (response_json), worker
        # and lease are immutable once created, so later saves can never
        # erase replay evidence.
        cursor = self._conn.execute(
            "UPDATE runs SET status=?, outcome=?, state_json=?, updated_at=?"
            " WHERE run_id=?"
            + (" AND updated_at=?" if expected_updated_at is not None else ""),
            (
                record.status, record.outcome,
                json.dumps(record.state, sort_keys=True), _utcnow(),
                record.run_id,
            )
            + ((expected_updated_at,) if expected_updated_at is not None else ()),
        )
        if cursor.rowcount != 1:
            if expected_updated_at is not None and self.load_run(record.run_id) is not None:
                raise StaleStateError(
                    f"run {record.run_id} changed since it was read; reload and retry"
                )
            raise ValueError(f"unknown run {record.run_id}")

    def transition(self, run_id: str, expected_status: str, mutator):
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != expected_status:
                conn.execute("ROLLBACK")
                return None
            record = self._to_record(row)
            mutator(record)
            conn.execute(
                "UPDATE runs SET status=?, outcome=?, state_json=?, updated_at=?"
                " WHERE run_id=? AND status=?",
                (
                    record.status, record.outcome,
                    json.dumps(record.state, sort_keys=True), _utcnow(),
                    run_id, expected_status,
                ),
            )
            conn.execute("COMMIT")
            return record
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # -- approvals -------------------------------------------------------
    def _insert_approval(self, run_id: str, checkpoint: str, approved_by: str, status: str) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO approvals(run_id, checkpoint, approved_by,"
                " approved_at, status)"
                " VALUES (?,?,?,?,?)",
                (run_id, checkpoint, approved_by, _utcnow(), status),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def record_approval(self, run_id: str, checkpoint: str, approved_by: str) -> bool:
        """Record an approval whose continuation is already completed."""
        return self._insert_approval(run_id, checkpoint, approved_by, "executed")

    def admit_approval(self, run_id: str, checkpoint: str, approved_by: str) -> bool:
        """Durably admit an approval BEFORE its continuation executes."""
        return self._insert_approval(run_id, checkpoint, approved_by, "admitted")

    def approval_state(self, run_id: str, checkpoint: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM approvals WHERE run_id=? AND checkpoint=?",
            (run_id, checkpoint),
        ).fetchone()
        return None if row is None else row["status"]

    def complete_approval(self, run_id: str, checkpoint: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE approvals SET status='executed', execution_completed_at=?"
            " WHERE run_id=? AND checkpoint=? AND status='admitted'",
            (_utcnow(), run_id, checkpoint),
        )
        return cursor.rowcount == 1

    def admitted_approvals(self) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT run_id, checkpoint, approved_by, approved_at FROM approvals"
            " WHERE status='admitted'"
        ).fetchall()
        return [dict(row) for row in rows]

    def record_response(self, run_id: str, response: dict[str, Any]) -> bool:
        """Store the immutable creation response; the first write wins."""
        cursor = self._conn.execute(
            "UPDATE runs SET response_json=? WHERE run_id=? AND response_json IS NULL",
            (json.dumps(response, sort_keys=True), run_id),
        )
        return cursor.rowcount == 1

    # -- idempotency -----------------------------------------------------
    def admit(self, *, user_uid: str, idem_key: str, request_hash: str, run_id: str) -> str:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT request_hash, run_id FROM idempotency"
                " WHERE user_uid=? AND idem_key=?",
                (user_uid, idem_key),
            ).fetchone()
            if row is not None:
                conn.execute("ROLLBACK")
                if row["request_hash"] != request_hash:
                    raise KeyConflictError(
                        "Idempotency-Key already bound to different request content"
                    )
                return row["run_id"]
            conn.execute(
                "INSERT INTO idempotency(user_uid, idem_key, request_hash, run_id,"
                " created_at) VALUES (?,?,?,?,?)",
                (user_uid, idem_key, request_hash, run_id, _utcnow()),
            )
            conn.execute("COMMIT")
            return run_id
        except KeyConflictError:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def lookup_admission(self, user_uid: str, idem_key: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT request_hash, run_id FROM idempotency"
            " WHERE user_uid=? AND idem_key=?",
            (user_uid, idem_key),
        ).fetchone()
        return None if row is None else (row["request_hash"], row["run_id"])

    def orphan_claims(self) -> list[dict[str, str]]:
        rows = self._conn.execute(
            "SELECT i.user_uid, i.idem_key, i.request_hash, i.run_id"
            " FROM idempotency i LEFT JOIN runs r ON r.run_id = i.run_id"
            " WHERE r.run_id IS NULL"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- listing ---------------------------------------------------------
    def list_runs(self, owner_uid: str, limit: int = 50, offset: int = 0) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE owner_uid=? ORDER BY updated_at DESC"
            " LIMIT ? OFFSET ?",
            (owner_uid, limit, offset),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    # -- workers / leases --------------------------------------------------
    def register_worker(self, worker_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO workers(worker_id, last_seen) VALUES (?,?)",
            (worker_id, _utcnow()),
        )

    def heartbeat(self, worker_id: str) -> None:
        self._conn.execute(
            "UPDATE workers SET last_seen=? WHERE worker_id=?", (_utcnow(), worker_id)
        )

    def worker_live(self, worker_id: str | None, *, now: str, heartbeat_ttl_seconds: float) -> bool:
        if worker_id is None:
            return False
        row = self._conn.execute(
            "SELECT last_seen FROM workers WHERE worker_id=?", (worker_id,)
        ).fetchone()
        if row is None:
            return False
        seen = datetime.fromisoformat(row["last_seen"])
        moment = datetime.fromisoformat(now)
        return (moment - seen).total_seconds() <= heartbeat_ttl_seconds

    # -- reconciliation ----------------------------------------------------
    def _reconcile_row(self, run_id: str, from_outcome: str, *, operator: str | None, reason: str | None, now: str) -> None:
        self._conn.execute(
            "UPDATE runs SET outcome='uncertain', updated_at=? WHERE run_id=?",
            (now, run_id),
        )
        self._conn.execute(
            "INSERT INTO reconciliations(run_id, operator, reason,"
            " from_outcome, to_outcome, evidence, recorded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (run_id, operator, reason, from_outcome, "uncertain", None, now),
        )

    def reconcile_stale(
        self,
        *,
        dry_run: bool = True,
        run_ids: list[str] | None = None,
        require_quiesced: bool = False,
        operator: str | None = None,
        reason: str | None = None,
        now: str | None = None,
        heartbeat_ttl_seconds: float = 60.0,
    ) -> list[dict[str, str]]:
        """Mark stale admitted/executing runs uncertain (never re-execute).

        Waiting-for-review rows (``outcome='awaiting_review'``) and rows owned
        by a live worker are never selected. ``require_quiesced`` overrides
        the lease check when the operator has stopped every worker: with no
        worker possible, any admitted/executing row is stale.
        """
        if not dry_run and (not operator or not reason):
            raise ValueError("apply requires an operator and reason")
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            moment = now or _utcnow()
            result: list[dict[str, str]] = []
            candidates = [
                self._to_record(row)
                for row in conn.execute(
                    "SELECT * FROM runs WHERE outcome IN ('admitted','executing')"
                ).fetchall()
                if run_ids is None or row["run_id"] in run_ids
            ]
            for record in candidates:
                stale, why = _apply_staleness(
                    record,
                    require_quiesced=require_quiesced,
                    now=moment,
                    worker_live=self.worker_live(
                        record.worker_id,
                        now=moment,
                        heartbeat_ttl_seconds=heartbeat_ttl_seconds,
                    ),
                )
                if not stale:
                    continue
                result.append({"run_id": record.run_id, "reason": why})
                if not dry_run:
                    self._reconcile_row(
                        record.run_id, record.outcome,
                        operator=operator, reason=reason, now=moment,
                    )
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def resolve_run(
        self,
        run_id: str,
        *,
        operator: str,
        reason: str,
        evidence: str | None = None,
    ) -> bool:
        """Abandon a non-terminal run and record the operator decision.

        The only safe resolution is ``failed``: nothing is re-executed, no
        idempotency key is cleared, and the decision is durably recorded so
        a later new-key submission is grounded in evidence, not guesswork.
        """
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row["outcome"] in ("completed", "failed"):
                conn.execute("ROLLBACK")
                return False
            now = _utcnow()
            conn.execute(
                "UPDATE runs SET outcome='failed', status='failed', updated_at=?"
                " WHERE run_id=? AND outcome NOT IN ('completed','failed')",
                (now, run_id),
            )
            conn.execute(
                "INSERT INTO reconciliations(run_id, operator, reason,"
                " from_outcome, to_outcome, evidence, recorded_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (run_id, operator, reason, row["outcome"], "failed", evidence, now),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise


class SQLiteShareRegistry:
    """Share-token registry persisted in the durable run store.

    Restores revocation across restarts: a token revoked for a run stays
    revoked because the revocation row survives the process. Replaces the
    process-local registry when ``PHYTO_RUN_STORE=sqlite``.
    """

    def __init__(self, store: SQLiteRunStore):
        self._store = store
        self._conn = store._conn

    def register(self, run_id: str, token_id: str, expires_at: datetime) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO share_tokens(token_id, run_id, expires_at)"
            " VALUES (?,?,?)",
            (token_id, run_id, expires_at.isoformat()),
        )

    def revoke_all(self, run_id: str) -> int:
        now = _utcnow()
        rows = self._conn.execute(
            "SELECT token_id FROM share_tokens WHERE run_id=? AND expires_at>?",
            (run_id, now),
        ).fetchall()
        if rows:
            self._conn.execute(
                "INSERT OR REPLACE INTO share_revocations(run_id, revoked_at)"
                " VALUES (?,?)",
                (run_id, now),
            )
        return len(rows)

    def is_revoked(self, token_id: str, *, now: datetime | None = None) -> bool:
        token = self._conn.execute(
            "SELECT run_id, expires_at FROM share_tokens WHERE token_id=?",
            (token_id,),
        ).fetchone()
        if token is None:
            return False
        moment = now or datetime.now(timezone.utc)
        revocation = self._conn.execute(
            "SELECT revoked_at FROM share_revocations WHERE run_id=?",
            (token["run_id"],),
        ).fetchone()
        if revocation is None:
            return False
        return moment.isoformat() < token["expires_at"]

    def active_tokens(self, run_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT token_id FROM share_tokens WHERE run_id=? AND expires_at>?",
            (run_id, _utcnow()),
        ).fetchall()
        return [row["token_id"] for row in rows]


def open_run_store() -> RunStore:
    """Select the run store from the environment.

    Local plane defaults to the in-memory store (offline tests, mock plane).
    ``PHYTO_RUN_STORE=sqlite PHYTO_RUN_STORE_PATH=...`` selects the durable
    store on any plane. The cloud plane fails closed without it: a production
    deployment must not silently run on process-local state.
    """
    environment = "cloud" if os.environ.get("PHYTO_ENV") == "cloud" else "local"
    backend = os.environ.get("PHYTO_RUN_STORE", "memory")
    if backend == "sqlite":
        path = os.environ.get("PHYTO_RUN_STORE_PATH")
        if not path:
            raise RuntimeError(
                "PHYTO_RUN_STORE=sqlite requires PHYTO_RUN_STORE_PATH"
            )
        return SQLiteRunStore(path)
    if environment == "cloud":
        raise RuntimeError(
            "Cloud/production mode requires durable run storage: set "
            "PHYTO_RUN_STORE=sqlite and PHYTO_RUN_STORE_PATH"
        )
    return InMemoryRunStore()


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def request_content_hash(payload: dict[str, Any]) -> str:
    return sha256_dict(payload)


def backup_sqlite_store(store: SQLiteRunStore, destination: str | Path) -> Path:
    """Consistent hot backup via SQLite's backup API (safe under writers)."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    try:
        with target:
            store._conn.backup(target)
    finally:
        target.close()
    return destination
