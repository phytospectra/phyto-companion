# Durable run storage — design, operations, and limits (2026-11-12)

## What is durable now

`services/orchestrator/app/run_store.py` provides the persistence layer:

- **SQLite** single-file store (`PHYTO_RUN_STORE=sqlite`,
  `PHYTO_RUN_STORE_PATH=...`), WAL journaling, `synchronous=FULL`,
  `busy_timeout=30 s`, and `BEGIN IMMEDIATE` write transactions. Schema
  version 2 (forward-only migrations; see `RUN_SCHEMA_VERSION`).
- **Durable run lifecycle**: a run is admitted (row persisted with the
  planned graph, context and request) BEFORE the engine executes. Outcomes
  are explicit: `admitted` → terminal (`completed` / `failed` /
  `awaiting_review`), with `executing` for an in-flight continuation and
  `uncertain` after operator reconciliation of an interruption.
- **Idempotency keys bound to canonical request-content hashes**, scoped to
  the authenticated principal. The immutable creation response is stored in
  a dedicated `response_json` column that no later mutation (approval, share
  creation, revocation) can overwrite; replays return it byte-for-byte.
- **Approval lifecycle**: an approval is durably `admitted` before its
  continuation executes and becomes `executed` only after the continuation
  result is persisted. A crash in between is an explicit uncertain window.
- **Stale-work ownership**: every run records its owning `worker_id`
  (process generation) and `lease_expires_at`; workers renew a liveness
  heartbeat while serving requests. Reconciliation requires lease expiry (or
  an operator attestation that the system is quiesced) AND a dead worker.
- **Reconciliation records**: every applied reconciliation or resolution
  writes the operator, reason, evidence and from→to transition to a durable
  `reconciliations` table.
- **Durable share revocation**: issued tokens and revocations live in the
  same SQLite file, so revocations survive restarts.
- **Local plane default** remains the in-memory store (offline tests, mock
  plane). **Cloud/production fails closed** at startup unless sqlite storage
  is configured — a production deployment must not silently run on
  process-local state.

`GET /capabilities` reports `run_storage: memory|sqlite` truthfully; `GET
/runs/{id}` reports the durable `outcome` alongside the operational `status`.

## What this does NOT provide

- **No exactly-once execution of external effects.** A run interrupted after
  an effect is `uncertain` until an operator determines what happened;
  nothing is ever re-executed automatically, and a refund or a 409 is never
  evidence that no effect occurred.
- **Single-instance SQLite.** One orchestrator process; multi-replica
  routing and cross-replica state are unsupported. The audit chain remains
  an append-only JSONL file (durable, per-process). Deploying multiple
  workers against one store is possible at the SQLite level (WAL,
  `BEGIN IMMEDIATE`) but is not a supported topology; do not claim
  distributed readiness.
- **No key expiry or clearing.** A consumed idempotency key stays consumed.
  Recovery is never "delete the key"; see the resolution procedure below.

## Operator procedures

### Reconciliation (after a crash or shutdown)

1. Stop the orchestrator if it is still running (or be prepared to attest
   that no worker can own the rows — see `--confirm-quiesced`).
2. Dry-run preview — this shows exactly which runs would be affected and
   why, and lists orphan idempotency claims and admitted-but-unfinished
   approvals:

   ```sh
   .venv/bin/python scripts/reconcile_runs.py --store /path/to/runs.sqlite
   ```

3. Apply the uncertain marking, recording who and why (both are required;
   the transition is recorded durably):

   ```sh
   .venv/bin/python scripts/reconcile_runs.py --store /path/to/runs.sqlite \
     --apply --operator you@example.org --reason "post-crash reconciliation"
   ```

   Use `--confirm-quiesced` only when every worker process is verifiably
   stopped: it overrides the lease check with the operator's attestation.
   Waiting-for-review runs are never selected, and elapsed time alone never
   marks anything stale.

4. **Resolve each uncertain run before any resubmission.** Determine whether
   the operation's effects occurred (executor logs, downstream state,
   provider usage). Record the decision:

   ```sh
   .venv/bin/python scripts/reconcile_runs.py --store /path/to/runs.sqlite \
     --resolve run-abc123=abandoned --operator you@example.org \
     --reason "no evidence the continuation executed" --evidence "log tail ..."
   ```

   The only supported decision is `abandoned` (run → `failed`, decision
   recorded). Completion is never asserted without stored execution
   evidence. Only after this recorded decision is a **new** operation with a
   **new** key appropriate. The same path exists as an authenticated admin
   endpoint: `POST /admin/runs/{run_id}/resolve`.
5. `--complete-approval RUN_ID:CHECKPOINT` closes an approval admitted for a
   run that later reached a terminal outcome without recording its
   completion (crash between the two persists).

**Never** tell a client to retry with a new key merely because an outcome is
uncertain. A new key can repeat an external effect.

### Backup / restore / rollback

- Backup (consistent, safe while writers run): SQLite's backup API via
  `run_store.backup_sqlite_store(store, dest)` — or `sqlite3 runs.sqlite
  ".backup dest.sqlite"`; copy the WAL alongside for raw file copies taken
  without the backup API.
- Restore: stop the orchestrator, replace `runs.sqlite` (+ `-wal`/`-shm`)
  with the backup, restart. Runs created after the backup are lost — that
  is the rollback.
- **Accounting interaction**: the durable token ledger (Firestore in
  production, the process-local mock in dry-run) is NOT inside this file.
  Restoring an older store can resurrect run rows whose keys the accounting
  ledger still considers consumed — those keys replay or conflict from the
  restored state. Restore is a deliberate operator action; reconcile
  run-vs-ledger state after any restore before telling users what happened.
- Rollback of schema: not supported (migrations are forward-only). Restore
  an older file instead; the migration table keeps older data readable.

## Test evidence

- `tests/test_durable_run_store.py`: restart hydration, v1→v2 data
  migration (response column extraction, review-pending relabel), migration
  idempotence, concurrent `BEGIN IMMEDIATE` transitions, CAS stale-write
  refusal, content-conflict/user-scoped/never-cleared keys, parallel
  same-key admission, reconciliation selectivity (live worker / pending
  review / expired lease / quiesced override / explicit selection),
  operator-recorded apply, concurrent reconciliation, resolution with
  recorded decision, approval admit→execute lifecycle, immutable response
  column, orphan-claim discovery, bounded+offset listing, backup/restore,
  tenant separation, canonical-JSON persistence, in-memory parity.
- `tests/test_durability_fault_injection.py`: interruption at the real API
  boundary — after admission/before execution, during execution after
  effects, before final persistence; approval crashes before admission,
  before continuation, and after continuation effects; orphan-claim
  fail-closed; replay surviving approval/share/revocation and restart.
- `tests/test_idempotency_contract.py`: the end-to-end key contract through
  the real auth middleware — replay without a second reservation or charge,
  content conflict, in-flight conflict, uncertain→reconciliation,
  failed-key conflict, ledger-restart replay refund, strict 409 for
  non-replay routes, cross-user scoping.

Accounting guarantee boundaries are unchanged: see `ACCOUNTING-GUARANTEE.md`.
