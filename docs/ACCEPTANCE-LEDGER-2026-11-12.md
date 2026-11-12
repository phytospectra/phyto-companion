# Acceptance checklist and evidence ledger — 2026-11-12 durability repair

Scope: repair the six audit findings of PROMPT-COMPLETION-AUDIT-2026-11-12,
complete the supported persistence/security boundary, frontend acceptance,
operational verification, and documentation reconciliation. This ledger is
the evidence record: each item names the requirement, the implementation, the
test or inspection, the result, and the exact revision when it lands.

Inspected starting revisions (verified on 2026-11-12):
backend `2520ad062d746edcf0e7ef529d214a5ca091a9cf` (branch
`agent/phase0a-phase1a-failclosed-runtime`), frontend
`4ef2716fb9cb393da7eb851e491ea2b71ab05f5a` (branch
`agent/frontend-auth-accounting-2026-11-09`). Both working trees were clean;
frontend `origin` is a local bundle, `github` is the hosted remote.

Work branch: backend `agent/durability-repairs-2026-11-12`.

## Phase 1 — audit findings

### A. Persist state before execution
- Requirement: durable admission → execution → terminal lifecycle; no
  idempotency claim without a run row; orphan claims discoverable and
  reconcilable; conflicting key reuse rejected; no repeat after ambiguity.
- Implementation: `submit_analysis` validates/plans first, admits the key
  (transactional), persists an `admitted` row (worker id, lease) BEFORE
  `engine.run`, then records the immutable creation response and the
  terminal outcome. Orphan claims surface via `orphan_claims()` and the
  reconcile CLI; a duplicate whose run row is missing fails closed.
- Tests: `tests/test_durability_fault_injection.py`
  (`test_crash_after_admission_before_execution`,
  `test_crash_during_execution_after_effect`,
  `test_crash_before_final_persistence`,
  `test_orphan_claim_fails_closed_and_is_discoverable`).
- Result: PENDING (first local run; CI TBD).

### B. Selective, safe reconciliation
- Requirement: distinct waiting-for-review vs executing; lease/heartbeat or
  quiesced-operator staleness; explicit run selection and dry-run previews;
  operator/reason/evidence recorded; uncertain preserved; elapsed time
  alone never authorizes replay.
- Implementation: outcome values split (`awaiting_review` separate from
  `admitted`/`executing`); schema v2 adds worker/lease columns, a workers
  heartbeat table and a reconciliations table; `reconcile_stale(dry_run,
  run_ids, require_quiesced, operator, reason, now, heartbeat_ttl)`;
  `resolve_run(...)` records the only safe decision (`abandoned` → failed).
- Tests: `tests/test_durable_run_store.py` (`test_reconciliation_selectivity`,
  `test_explicit_run_selection_and_quiesced_override`,
  `test_interrupted_execution_reconciles_to_uncertain_never_reruns`,
  `test_concurrent_reconciliation_applies_once`,
  `test_resolve_run_records_decision_and_never_reruns`).
- Result: PENDING.

### C. Recover interrupted approvals
- Requirement: approval bound to exact checkpoint + verified principal;
  concurrent/repeated continuation prevented; uncertainty recorded when the
  process fails after admission; safe resolution without blind rerun.
- Implementation: `admit_approval` (durable, status `admitted`) before the
  pre-continuation persist (outcome `executing`), `complete_approval` after
  terminal persist; retry of an admitted approval returns 409 with
  reconciliation guidance; resolution via CLI `--resolve` or admin endpoint.
- Tests: `tests/test_durability_fault_injection.py`
  (`test_approval_crash_before_admission_is_recoverable`,
  `test_approval_crash_before_continuation_never_blindly_reruns`,
  `test_approval_crash_after_continuation_effect`).
- Result: PENDING.

### D. Idempotency contract across auth/accounting/runs
- Requirement: one end-to-end contract; keys scoped to principal +
  operation; canonical content binding; safe replay or stable conflict;
  never reserve/charge again for a replay; no cross-user leak; actionable
  in-flight/uncertain info; money and execution guarantees distinct.
- Implementation: middleware passes consumed keys through to
  `POST /analysis` without a reservation (`idempotency_replay` state); the
  run layer resolves replay/conflict from durable state; replays carry
  `X-Idempotency-Replay` and are refunded like cache hits if a fresh
  reservation was made (mock-ledger restart window); all other routes keep
  the strict 409.
- Tests: `tests/test_idempotency_contract.py` (7 tests through the real
  middleware with offline collaborators).
- Result: PENDING.

### E. Immutable response/evidence records
- Requirement: creation response preserved across approval/share/revoke;
  provenance and timestamps preserved; stale writes prevented.
- Implementation: `response_json` column (schema v2 data migration moves it
  out of mutable state); `record_response` first-write-wins; `save_run`
  never touches it; CAS `expected_updated_at` guard on saves raises
  `StaleStateError` → 409.
- Tests: `tests/test_durable_run_store.py`
  (`test_creation_response_is_immutable`, `test_save_run_cas_refuses_stale_writes`,
  `test_v1_database_migrates_response_out_and_relabels_review_pending`);
  `test_replay_survives_approval_share_and_revocation` (API level).
- Result: PENDING.

### F. Correct recovery instructions
- Requirement: no "retry with a new key" on uncertainty; document the
  resolution procedure; no exactly-once claims beyond evidence.
- Implementation: rewritten `docs/DURABLE-RUN-STORE.md`,
  `docs/ACCOUNTING-GUARANTEE.md`, `scripts/reconcile_runs.py` help text.
- Inspection: docs and CLI text reviewed (this ledger's Phase 6 check).
- Result: PENDING.

## Phase 2 — persistence and security boundary
| Item | Status |
|---|---|
| Owner/tenant isolation on every read/mutation | PENDING |
| Cross-process freshness (CAS, lease/heartbeat) | implemented; tests above |
| Share-token + revocation persistence | implemented (`SQLiteShareRegistry`); test PENDING |
| Audit continuity across restart | PENDING review |
| Artifact ownership/integrity/lifecycle | PENDING review |
| Run-history pagination and bounds | implemented (`offset`, cap 200); test added |
| Migration compatibility and failure handling | v1→v2 data migration + test |
| Backup/restore/rollback behavior | existing API; restore-into-fresh-env exercise PENDING |
| Single-host boundary documented | DURABLE-RUN-STORE.md updated |

## Phase 3 — frontend
| Item | Status |
|---|---|
| Outcome/state contract update (types, fixtures, MSW) | PENDING |
| Approval 409 structured-detail handling | PENDING |
| No auto-retry / no fabricated fallback | PENDING inspection |
| Sign-out/account isolation | PENDING inspection |
| TypeScript / unit tests / production build | PENDING |
| Browser interaction with integrated backend | PENDING |
| Keyboard/labels/focus/error announcements | PENDING |
| Desktop + narrow layout; loading/empty/failure/recovery | PENDING |

## Phase 4 — operations
| Item | Status |
|---|---|
| Clean-env install + offline quickstart | PENDING (venv rebuilt outside Documents 2026-11-12) |
| Authenticated API contract checks | PENDING (covered by test suite; live smoke TBD) |
| Worker interruption / restart exercises | PENDING (fault injection at API boundary; real-process restart TBD) |
| Backup → restore into separate environment | PENDING |
| Concurrency + sustained load | 12-writer smoke exists; sustained run PENDING |
| Resource bounds (request/payload/history/execution) | limit/offset caps exist; audit PENDING |
| Timeout/cancellation semantics | PENDING |
| Readiness dependency reporting | existing /health + /capabilities |
| Correlation logs | existing X-Request-ID echo/mint + test |
| Secret redaction checks | PENDING |
| Dependency/secret scanning in CI | PENDING |

## Phase 5 — research follow-through
| Item | Status |
|---|---|
| Triage Nov-11 proposals (controlled vs exploratory vs future) | PENDING |
| NaFM/NPCLM verification (models, licenses, deps, interface) | PENDING (metadata only; no downloads/spend) |
| No new arms added to the locked comparison | PENDING audit |
| OpenRouter cap recheck (safe, no secrets) | PENDING |
| Colab allowance recheck | PENDING |

## Phase 6 — documentation reconciliation
| Item | Status |
|---|---|
| DURABLE-RUN-STORE.md | rewritten |
| ACCOUNTING-GUARANTEE.md | rewritten |
| reconcile_runs.py help | rewritten |
| STAGING-ACCEPTANCE stale wording | PENDING |
| FRONTEND-AUTH-ACCOUNTING wording | PENDING |
| COMPLETION-LEDGER update | PENDING |
| Readiness split restated | PENDING |

## Environment hazards observed
- The cloud-managed Documents tree evicted the repo's `.venv` entirely
  mid-session (2026-11-12). A clean venv was rebuilt at
  `/tmp/phyto-venv-20261112` outside Documents with the CI dependency set.
  Commands below use `PYTHONPATH=shared:build/auth-path:.` per the Makefile.
- `git status` in the frontend repo stalls for minutes on dataless files;
  bounded diagnosis only, no resets.

## Spending and authorization (as of 2026-11-12)
- OpenRouter: PENDING recheck. Colab: PENDING recheck. Inference/compute
  spend this increment: $0 (no paid requests, no training, no downloads).
- No merge to main, no deployment, no third-party contact.
