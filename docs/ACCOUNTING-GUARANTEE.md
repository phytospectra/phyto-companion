# Accounting guarantee statement and recovery matrix — 2026-11-12

## Concise guarantee (verified by tests)

Per (user, idempotency key), within one durable ledger instance, the request
path provides **at-most-once handler admission** and **at-most-once terminal
accounting settlement** (no guarantee that settlement eventually occurs after
a crash). Execution and money stay separate: the ledger never authorizes a
new execution, and a replay never costs money.

1. The first request claims the key in the shared ledger before budget
   selection; every later request with the same key is refused or replayed
   **before** any handler runs again (including parallel duplicates —
   admission is exactly one).
2. The monetary charge is reserved exactly once, and settlement (confirm or
   refund) is applied exactly once per reservation; the ledger state machine
   refuses double confirm/refund. Failed, cache-served and replayed
   responses refund exactly once.
3. Keys are scoped to (user, ledger). Another user may reuse the same key
   string; a key can never surface another user's response.

### The end-to-end replay contract for `POST /analysis`

The two layers agree on one contract. The accounting middleware sees a
consumed key; because `POST /analysis` implements durable replay, the
middleware dispatches **without a reservation** and the run layer resolves
the outcome from durable state:

| Durable state of the bound run | Response | Accounting effect |
|---|---|---|
| Terminal (`completed`, `awaiting_review`) with stored creation response | `200`, identical replay, `X-Idempotency-Replay: true` | none — never reserved, never charged |
| Terminal without a stored response | `409` conflict with recovery guidance | none |
| `admitted` / `executing` (in flight) | `409` with `run_id` + `outcome` | none |
| `uncertain` | `409` "operator reconciliation required" | none |
| `failed` | `409` "this key cannot be reused" | none |
| Same key, different canonical content | `409` content conflict | none |
| Ledger claim exists, no run-layer binding | `409` "never admitted a run; reconciliation required" | none (no uncharged execution) |

Every other endpoint keeps the strict behaviour: a consumed key is `409`
before the handler runs. If a replay is returned while a fresh reservation
was somehow made (the mock-ledger restart window below), the
`X-Idempotency-Replay` header makes the middleware refund it — a replay is
never charged twice.

What is **NOT** guaranteed:

- **Not exactly-once external effects.** If the process dies after a
  handler's external effect but before settlement, the effect happened once
  and the charge stays held: recovery is operator reconciliation, not replay.
- **No key expiry or clearing.** A consumed key stays consumed. After an
  uncertain outcome the client must not retry — with the same key or a new
  one — until the original operation has been reconciled and the operator
  decision recorded (`scripts/reconcile_runs.py --resolve`, or
  `POST /admin/runs/{run_id}/resolve`). Operators must never clear keys to
  "retry", which could repeat a non-idempotent effect.
- **Mock (dry-run) ledger is process-local.** A process restart forgets all
  claims/charges; this is a test/offline boundary only. The production
  Firestore path persists claims and balances and serializes concurrent
  claims via transactions (design-verified, not executed here without SDK).
  In the mock window a duplicate may briefly reserve and then refund (0 net
  tokens) as it replays; the durable run store still guarantees the replay
  is identical and the response is never re-executed.
- **Restore resurrection.** Restoring an older `runs.sqlite` can bring back
  rows whose keys the accounting ledger still considers consumed; reconcile
  run-vs-ledger state after any restore (see DURABLE-RUN-STORE.md).

## Failure/recovery matrix

| Failure point | Observed behavior | Client recovery | Operator action |
|---|---|---|---|
| Duplicate key, same content, terminal run | `200` replay, 0 tokens | none — the replay is authoritative | none |
| Duplicate key, different content | `409` before any handler | use a new key for genuinely new work | none |
| Parallel same key | exactly one admitted; others `409` in-flight/replay | await or reconcile the original outcome | none for known successful outcome |
| Handler exception / HTTP failure / cache hit | refund is idempotent; key stays consumed | reconcile any possible external effect before a new operation | refund does not prove absence of an external effect |
| Crash after claim, before run admission | key claimed, zero tokens held; no run row | do not reuse the key | `reconcile_runs.py` lists the orphan claim; resolve after determining nothing ran |
| Crash after run admission, before execution | run row `admitted`; duplicates `409` in-flight | do not resubmit | reconcile (`--confirm-quiesced` post-shutdown) → uncertain → resolve |
| Crash after external effect, before settle | effect may have occurred; charge may remain held (Firestore) | do not resubmit with any key until reconciled | establish external outcome before accounting settlement; a refund does not undo an effect |
| Crash during approve continuation | approval `admitted`, run `executing`; retry `409` with reconciliation guidance | do not re-approve | reconcile → uncertain → resolve; the continuation is never re-run |
| Crash during settle | commit outcome may be unknown to caller | inspect original outcome; no new-key replay | read reservation state and reconcile; storage atomicity does not ensure eventual settlement |
| Process restart with mock ledger | all claims/charges forgotten; replays re-claim and refund | replay returns the identical stored response at 0 net tokens | never use mock ledger across restarts for real accounting |
| Workspace pool exhausted mid-retry | duplicate refused before pool selection | inspect original request outcome | do not bypass the duplicate guard with a new key |

## Test evidence

- `tests/test_operational_integration.py`: middleware-level success, HTTP
  error, exception, cache-hit, parallel same-key, cross-operation, cross-UID,
  pool-exhaustion duplicates; balances/allowances/events/audit asserted
  unchanged on refusals.
- `tests/test_accounting_guarantees.py`: store-level duplicate, content-blind,
  user-scoped, parallel admission, exactly-once settlement, fresh-process
  replay window, never-expiring keys.
- `tests/test_idempotency_contract.py`: the end-to-end authenticated replay
  contract above, through the real middleware with offline collaborators.
- `tests/test_durability_fault_injection.py` and
  `tests/test_durable_run_store.py`: the durable execution/approval/recovery
  state behind the contract (see docs/DURABLE-RUN-STORE.md).

## Documentation corrections applied

Earlier wording describing "exactly-once idempotency" has been narrowed to
"at-most-once handler admission with at-most-once terminal accounting
settlement". Response replay is now implemented for `POST /analysis` per the
table above; all other routes keep the strict 409. Recovery instructions
never advise a new key while an outcome is uncertain.
