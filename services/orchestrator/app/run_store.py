"""Durable run/task/checkpoint persistence for the orchestrator.

Two implementations behind one protocol:

* :class:`InMemoryRunStore` — deterministic, process-local. Offline tests and
  the local mock plane only; nothing here survives a restart.
* :class:`SQLiteRunStore` — the durable store. One file, WAL journaling,
  ``BEGIN IMMEDIATE`` write transactions for cross-process transition
  serialization, and a versioned migration table. stdlib-only by design:
  this repository has no concrete need for a database server, and adding
  one would add an unmanaged operational dependency.

Guarantees actually provided (see docs/ACCOUNTING-GUARANTEE.md for the
request-key semantics): durable run/task/checkpoint/approval state that
survives restarts, transactional single-writer state transitions, and
idempotency keys bound to a canonical request-content hash. NOT provided:
exactly-once execution of external effects. A run interrupted mid-execution
is persisted as ``executing`` and must be reconciled by an operator to
``uncertain``; reconciliation never re-executes work.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from phyto_shared.provenance import sha256_dict

__all__ = [
    "RunRecord",
    "RunStore",
    "InMemoryRunStore",
    "SQLiteRunStore",
    "open_run_store",
    "RUN_SCHEMA_VERSION",
    "backup_sqlite_store",
]

RUN_SCHEMA_VERSION = 1

_MIGRATIONS: list[tuple[int, str]] = [
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
    ),
]


class KeyConflictError(Exception):
    """An idempotency key was reused with different request content."""


class RunRecord:
    """One run's durable state (graph, context, result, request, extras)."""

    __slots__ = (
        "run_id", "owner_uid", "workflow", "status", "outcome",
        "manifest_id", "state", "created_at", "updated_at",
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
    ) -> None:
        self.run_id = run_id
        self.owner_uid = owner_uid
        self.workflow = workflow
        self.status = status
        self.outcome = outcome
        self.manifest_id = manifest_id
        self.state = state
        self.created_at = created_at
        self.updated_at = updated_at


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore(Protocol):
    mode: Literal["memory", "sqlite"]

    def create_run(self, record: RunRecord) -> None: ...
    def load_run(self, run_id: str) -> RunRecord | None: ...
    def save_run(self, record: RunRecord) -> None: ...
    def transition(
        self, run_id: str, expected_status: str, mutator
    ) -> RunRecord | None:
        ...

    def record_approval(
        self, run_id: str, checkpoint: str, approved_by: str
    ) -> bool: ...
    def admit(
        self, *, user_uid: str, idem_key: str, request_hash: str, run_id: str
    ) -> str:
        """Return the run_id bound to (user_uid, idem_key).

        Same request hash returns the existing run (duplicate admission).
        A different hash raises :class:`KeyConflictError`. Keys are never
        cleared automatically: recovery is a new key, never key deletion.
        """
    ...
    def list_runs(self, owner_uid: str, limit: int = 50) -> list[RunRecord]: ...
    def reconcile_stale_executing(self, *, dry_run: bool = True) -> list[str]:
        """Mark runs still ``executing`` as ``uncertain`` (never re-execute)."""
    ...


class InMemoryRunStore:
    """Process-local store; offline tests and the local mock plane only."""

    mode: Literal["memory", "sqlite"] = "memory"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, RunRecord] = {}
        self._keys: dict[tuple[str, str], tuple[str, str]] = {}
        self._approvals: set[tuple[str, str]] = set()

    def create_run(self, record: RunRecord) -> None:
        with self._lock:
            if record.run_id in self._runs:
                raise ValueError(f"run {record.run_id} already exists")
            self._runs[record.run_id] = record

    def load_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            return run

    def save_run(self, record: RunRecord) -> None:
        with self._lock:
            if record.run_id not in self._runs:
                raise ValueError(f"unknown run {record.run_id}")
            record.updated_at = _utcnow()
            self._runs[record.run_id] = record

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
            self._approvals.add(key)
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

    def list_runs(self, owner_uid: str, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            runs = [r for r in self._runs.values() if r.owner_uid == owner_uid]
            runs.sort(key=lambda r: r.updated_at, reverse=True)
            return runs[:limit]

    def reconcile_stale_executing(self, *, dry_run: bool = True) -> list[str]:
        with self._lock:
            stale = [
                r.run_id for r in self._runs.values() if r.outcome == "executing"
            ]
            if not dry_run:
                for run_id in stale:
                    record = self._runs[run_id]
                    record.outcome = "uncertain"
                    record.updated_at = _utcnow()
            return stale


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
            for version, script in _MIGRATIONS:
                if version in applied:
                    continue
                # executescript() would auto-commit the open transaction;
                # run each DDL statement individually instead.
                for statement in script.split(";"):
                    if statement.strip():
                        conn.execute(statement)
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
        )

    def create_run(self, record: RunRecord) -> None:
        self._conn.execute(
            "INSERT INTO runs(run_id, owner_uid, workflow, status, outcome,"
            " manifest_id, state_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                record.run_id, record.owner_uid, record.workflow, record.status,
                record.outcome, record.manifest_id,
                json.dumps(record.state, sort_keys=True),
                record.created_at, record.updated_at,
            ),
        )

    def load_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else self._to_record(row)

    def save_run(self, record: RunRecord) -> None:
        cursor = self._conn.execute(
            "UPDATE runs SET status=?, outcome=?, state_json=?, updated_at=?"
            " WHERE run_id=?",
            (
                record.status, record.outcome,
                json.dumps(record.state, sort_keys=True), _utcnow(),
                record.run_id,
            ),
        )
        if cursor.rowcount != 1:
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

    def record_approval(self, run_id: str, checkpoint: str, approved_by: str) -> bool:
        try:
            self._conn.execute(
                "INSERT INTO approvals(run_id, checkpoint, approved_by, approved_at)"
                " VALUES (?,?,?,?)",
                (run_id, checkpoint, approved_by, _utcnow()),
            )
        except sqlite3.IntegrityError:
            return False
        return True

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

    def list_runs(self, owner_uid: str, limit: int = 50) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT * FROM runs WHERE owner_uid=? ORDER BY updated_at DESC LIMIT ?",
            (owner_uid, limit),
        ).fetchall()
        return [self._to_record(row) for row in rows]

    def reconcile_stale_executing(self, *, dry_run: bool = True) -> list[str]:
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE outcome='executing'"
            ).fetchall()
            stale = [row["run_id"] for row in rows]
            if not dry_run and stale:
                conn.execute(
                    "UPDATE runs SET outcome='uncertain', updated_at=?"
                    " WHERE outcome='executing'",
                    (_utcnow(),),
                )
            conn.execute("COMMIT")
            return stale
        except Exception:
            conn.execute("ROLLBACK")
            raise


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
