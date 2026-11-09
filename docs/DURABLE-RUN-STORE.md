# Durable run storage — design, operations, and limits (2026-11-09)

## What is durable now

`services/orchestrator/app/run_store.py` provides the persistence layer:

- **SQLite** single-file store (`PHYTO_RUN_STORE=sqlite`,
  `PHYTO_RUN_STORE_PATH=...`), WAL journaling, `synchronous=FULL`,
  `busy_timeout=30 s`, and `BEGIN IMMEDIATE` write transactions.
- Durable **runs** (graph, execution context, engine result, request,
  citation, share token, and the exact creation response for idempotent
  replay), **idempotency keys bound to canonical request-content hashes**,
  and **approvals** (exactly one per run/checkpoint, attributed to the
  verified principal).
- **Migrations**: `schema_migrations` table; each version applies inside one
  transaction. Current schema version: see `RUN_SCHEMA_VERSION`.
- **Local plane default** remains the in-memory store (offline tests, mock
  plane). **Cloud/production fails closed** at startup unless sqlite storage
  is configured — a production deployment must not silently run on
  process-local state.

`GET /capabilities` reports `run_storage: memory|sqlite` truthfully.

## What this does NOT provide

- No exactly-once execution of external effects. A crash between admission
  and persistence leaves the key durably claimed with no run record:
  duplicates fail closed (409) until an operator reconciles.
- Shares/revocation registries, the audit rate limiter, and cohort stores
  remain process-local; multi-replica routing is still unsupported. The
  audit chain remains an append-only JSONL file (durable, per-process).
- Single-instance SQLite: appropriate for one orchestrator process. A
  multi-replica deployment needs an external database — a deliberate
  non-goal until that need is real.

## Operator procedures

**Reconciliation (after a crash/restart):**
```sh
.venv/bin/python scripts/reconcile_runs.py --store /path/to/runs.sqlite          # dry run
.venv/bin/python scripts/reconcile_runs.py --store /path/to/runs.sqlite --apply  # mark uncertain
```
Stale `executing` runs become `uncertain`; operational status is untouched;
nothing is re-executed; no idempotency key is cleared. Clients retry with a
NEW key. A duplicate hitting an in-flight key gets 409 with the run id; a
duplicate hitting a terminal key replays the stored response byte-for-byte;
the same key with different content is always 409.

**Backup / restore / rollback:**
- Backup (consistent, safe while writers run): SQLite's backup API via
  `run_store.backup_sqlite_store(store, dest)` — or `sqlite3 runs.sqlite
  ".backup dest.sqlite"`; copy the WAL alongside for raw file copies taken
  without the backup API.
- Restore: stop the orchestrator, replace `runs.sqlite` (+ `-wal`/`-shm`)
  with the backup, restart. Runs created after the backup are lost — that
  is the rollback.
- Rollback of schema: not supported (migrations are forward-only). Restore
  an older file instead; the migration table keeps older data readable.

## Test evidence

- `tests/test_durable_run_store.py` (12): restart hydration, migration
  idempotence, concurrent BEGIN IMMEDIATE transitions (exactly one winner),
  stale expected-status rejection, content-conflict/user-scoped/never-cleared
  keys, parallel same-key admission, interrupted→uncertain reconciliation,
  durable exactly-once approvals, backup/restore round-trip, tenant
  separation at row level, canonical-JSON persistence, in-memory parity.
- `tests/test_durable_api.py` (8): end-to-end through HTTP with the real
  sqlite store — restart recovery with ownership intact, owner-scoped
  `GET /runs` history, identical-response replay (including across
  restart), conflicting-content 409, short-key 400, uncertain
  reconciliation via the real script's store API, and cloud fail-closed
  without/with explicit sqlite configuration.

Accounting guarantee boundaries are unchanged: see `ACCOUNTING-GUARANTEE.md`.
