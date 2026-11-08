# Accounting guarantee statement and recovery matrix — 2026-11-09

## Concise guarantee (verified by tests)

Per (user, idempotency key), within one durable ledger instance, the request
path provides **at-most-once handler admission** and **at-most-once terminal accounting
settlement** (no guarantee that settlement eventually occurs after a crash):

1. The first request claims the key in the shared ledger before budget
   selection; every later request with the same key is refused HTTP 409
   **before** any handler, charge, allowance check, or workspace-pool draw
   (including parallel duplicates — admission is exactly one).
2. The monetary charge is reserved exactly once, and settlement (confirm or
   refund) is applied exactly once per reservation; the ledger state machine
   refuses double confirm/refund. Failed or cache-served handlers refund
   exactly once. Duplicate keys are refused even after a refund.
3. Keys are scoped to (user, operation-ledger); another user may reuse the
   same key string. Content is **not** bound to keys (response replay is not
   implemented; a reused key is refused regardless of payload).

What is **NOT** guaranteed:

- **Not exactly-once external effects.** If the process dies after a handler's
  external effect but before settlement, the effect happened once and the
  charge stays held: recovery is operator reconciliation, not replay.
- **No key expiry or clearing.** A consumed key stays consumed. After an uncertain outcome, the client must not use a new key or retry
  until the original operation has been reconciled. Operators must never clear
  keys to "retry", which could repeat a non-idempotent effect.
- **Mock (dry-run) ledger is process-local.** A process restart forgets all
  claims/charges; this is a test/offline boundary only. The production
  Firestore path persists claims and balances and serializes concurrent
  claims via transactions (design-verified, not executed here without SDK).
- A crash between claim and charge leaves a zero-token claim record; retrying
  the same key is refused. Client recovery is a new key; no money is at risk
  because no charge occurred.
- Duplicate responses are 409 conflict, not replayed original responses.

## Failure/recovery matrix

| Failure point | Observed behavior | Client recovery | Operator action |
|---|---|---|---|
| Duplicate key, same or different content | 409 before handler; no state change | inspect/reconcile original outcome; no automatic new-key retry | investigate uncertain original outcomes |
| Parallel same key | exactly one admitted; others 409 | await/reconcile original outcome | none for known successful outcome |
| Handler exception / HTTP failure / cache hit | refund is idempotent; key stays consumed | reconcile any possible external effect before a new operation | refund does not prove absence of an external effect |
| Crash after claim, before charge | key claimed, zero tokens held | new key | none |
| Crash after external effect, before settle | effect may have occurred; charge may remain held (Firestore) | do not resubmit with any key until reconciled | establish external outcome before accounting settlement; a refund does not undo an effect |
| Crash during settle | commit outcome may be unknown to caller | inspect original outcome; no new-key replay | read reservation state and reconcile; storage atomicity does not ensure eventual settlement |
| Process restart with mock ledger | all claims/charges forgotten; replays admitted | n/a (test plane) | never use mock ledger across restarts for real accounting |
| Workspace pool exhausted mid-retry | duplicate refused before pool selection | inspect original request outcome | do not bypass the duplicate guard with a new key |

## Test evidence

- `tests/test_operational_integration.py`: middleware-level success, HTTP
  error, exception, cache-hit, parallel same-key, cross-operation, cross-UID,
  pool-exhaustion duplicates; balances/allowances/events/audit asserted
  unchanged on refusals.
- `tests/test_accounting_guarantees.py`: store-level duplicate, content-blind,
  user-scoped, parallel admission, exactly-once settlement, fresh-process
  replay window, never-expiring keys.

## Documentation corrections applied

Earlier wording describing "exactly-once idempotency" has been narrowed to
"at-most-once handler admission with at-most-once terminal accounting settlement".
See `docs/FRONTEND-AUTH-ACCOUNTING-2026-11-09.md`,
`docs/STAGING-ACCEPTANCE-2026-11-09.md`, and the middleware docstring.
