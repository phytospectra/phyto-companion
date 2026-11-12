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

Work branches and final revisions (2026-11-12):
- Backend `agent/durability-repairs-2026-11-12` — final commit `9aecd29`
  (f1dd5d9 durability repair → bd8f4ba security/ops increment →
  9aecd29 gitleaks CI fix). Final CI run 43218430032: test job + the new
  security-scan job (gitleaks tree scan + pip-audit) both green.
- Frontend `agent/frontend-durability-contract-2026-11-12` — final commit
  `d3c8f38` (29dc536 contract → 9c3d9f3 a11y fix + acceptance record →
  d3c8f38 CI advisory). Final CI run 43217825626 green.

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
- Result: PASS at f1dd5d9 (local full suite 1918 passed / 4 skipped, ruff
  clean; GitHub CI run 43139833532 green).

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
- Result: PASS at f1dd5d9 (tests listed).

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
- Result: PASS at f1dd5d9 (7 contract tests through the real middleware).

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
- Result: PASS at f1dd5d9.

### F. Correct recovery instructions
- Requirement: no "retry with a new key" on uncertainty; document the
  resolution procedure; no exactly-once claims beyond evidence.
- Implementation: rewritten `docs/DURABLE-RUN-STORE.md`,
  `docs/ACCOUNTING-GUARANTEE.md`, `scripts/reconcile_runs.py` help text.
- Inspection: docs/CLI text rewritten and reviewed.
- Result: PASS at f1dd5d9.

## Phase 2 — persistence and security boundary
| Item | Status |
|---|---|
| Owner/tenant isolation on every read/mutation | PASS — owner-scoped runs/history/audit-records (new test); cross-user denial suite |
| Cross-process freshness (CAS, lease/heartbeat) | PASS — StaleStateError CAS, worker/lease columns, heartbeat table + tests |
| Share-token + revocation persistence | PASS — SQLiteShareRegistry + `test_share_revocation_survives_restart` + ops exercise |
| Audit continuity across restart | Reviewed — audit chain is per-process JSONL (documented boundary), `/audit/records` now owner-scoped or admin |
| Artifact ownership/integrity/lifecycle | Reviewed — artifacts live in the owner-scoped run state; share artifact views redact |
| Run-history pagination and bounds | PASS — `offset` + cap 200 (store + API tests, ops bound check 200/100 pages) |
| Migration compatibility and failure handling | PASS — v1→v2 data migration test (response column, review-pending relabel) |
| Backup/restore/rollback behavior | PASS — backup API + restore into a separate environment (ops exercise); accounting-interaction note documented |
| Single-host boundary documented | PASS — DURABLE-RUN-STORE.md + capabilities limitations text |

## Phase 3 — frontend
| Item | Status |
|---|---|
| Outcome/state contract update (types, fixtures, MSW) | PASS — research.ts schemas accept running + 6 outcomes; 29dc536 |
| Approval 409 structured-detail handling | PASS — structured message/recovery shown; conflict adopts run id; 29dc536 |
| No auto-retry / no fabricated fallback | PASS — 409 never unfreezes the form; no demo fallback in live mode (tests pin both) |
| Sign-out/account isolation | PASS — existing generation/remount tests still green (68/68) |
| TypeScript / unit tests / production build | PASS — typecheck clean; 68/68 vitest; build clean (CI 43139847866; 9c3d9f3) |
| Browser interaction with integrated backend | PASS — live workspace exercised in a real browser against the real authenticated backend via the build-gated offline demo identity (submit → awaiting_review → SIGKILL/restart → history re-hydration → approve → completed); live multi-identity still needs Firebase staging |
| Keyboard/labels/focus/error announcements | PASS — zero unnamed controls after FileDropZone fix; role=alert verified; drop zone keyboard wiring |
| Desktop + narrow layout; loading/empty/failure/recovery | PASS — 1280px and 390px (no horizontal overflow); screenshots preserved |

## Phase 4 — operations
| Item | Status |
|---|---|
| Clean-env install + offline quickstart | PASS — clean venv outside Documents (Python 3.12.15) with the CI dependency set; suites re-run there |
| Authenticated API contract checks | PASS — live uvicorn exercise with real middleware (mock verifier): replay/conflict/history/approval/share/revocation; 16/16 checks |
| Worker interruption / restart exercises | PASS — API-boundary fault injection tests + real-process SIGKILL/restart cycles in the ops exercise |
| Backup → restore into separate environment | PASS — hot backup → restore → second server serves history + identical replay; **restore audit (`reconcile_runs.py --audit-restore`) lists keys replaying pre-restore responses and runs the restore would lose** (exercise + test) |
| Concurrency + sustained load | PASS — burst: 300/300 at ~65 req/s; **10-minute soak: 7,480 requests, 0 errors, 12.5 req/s sustained, p95 1447 ms** with store growing to 43.7 MB (macOS arm64, single host); not a capacity certification |
| Resource bounds (request/payload/history/execution) | PASS — history cap 200 + offset on 300 rows; **payload 413 guard (PHYTO_MAX_REQUEST_BYTES) + cooperative execution budget (PHYTO_EXECUTION_BUDGET_SECONDS)** implemented with tests (test_execution_limits.py) |
| Timeout/cancellation semantics | PASS — server-side execution budget enforced between tasks (explicit failure, never silent partial success); single-tool overrun documented as a cooperative bound; client 120s timeout + abort wiring |
| Readiness dependency reporting | PASS — /health + /capabilities report storage mode + executor health truthfully |
| Correlation logs | PASS — X-Request-ID echo/mint + test |
| Secret redaction checks | PASS — new regression test: audit chain never carries bearer tokens/HMAC key/share URL |
| Evidence preservation (durable, sanitized) | **PASS — transcripts, soak summary and browser screenshots committed to docs/evidence/2026-11-12/ (README-indexed; no credentials)** |
| Dependency/secret scanning in CI | PASS — gitleaks + pip-audit blocking in backend CI; frontend: 2 dead deps removed, 12 remaining findings (6 high accepted with reachability rationale in DEPENDENCY-SECURITY-2026-11-12.md) enforced by an npm-audit baseline gate (fails on new critical/high) |

## Phase 5 — research follow-through
| Item | Status |
|---|---|
| Triage Nov-11 proposals (controlled vs exploratory vs future) | PASS — docs/MODEL-COMPARISON-FOLLOW-THROUGH-2026-11-12.md |
| NaFM/NPCLM verification (models, licenses, deps, interface) | PASS (bounded) — arXiv metadata verified; weights/license/interface NOT verifiable → retrieval arm BLOCKED, status recorded |
| No new arms added to the proposed comparison (protocol NOT LOCKED per benchmark/paired_hsqc/DECISION-SHEET.md) | PASS — arms A–E + Antigravity track untouched; retrieval idea kept as a separate exploratory experiment |
| OpenRouter cap recheck (safe, no secrets) | Result: OPENROUTER_API_KEY absent from environment → live recheck impossible; no paid requests; ceiling unchanged |
| Colab allowance recheck | Result: 0.00 compute units, 0.00/hr, 0 active assignments (`colab usage`); no allocation, no training |

## Phase 6 — documentation reconciliation
| Item | Status |
|---|---|
| DURABLE-RUN-STORE.md | rewritten |
| ACCOUNTING-GUARANTEE.md | rewritten |
| reconcile_runs.py help | rewritten |
| STAGING-ACCEPTANCE stale wording | PASS — dated correction added |
| FRONTEND-AUTH-ACCOUNTING wording | PASS — dated correction section appended |
| COMPLETION-LEDGER update | PASS — this ledger records the increment; the 2026-11-09 ledger is left as a historical record |
| Readiness split restated | PASS — see the verdict section below |

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

## Final readiness verdict (2026-11-12)

1. **Software research preview: GO with caveats** — the repaired durability
   contract, reconciliation procedure and frontend contract are implemented
   and verified by 1918 backend tests (4 skipped; ruff clean; CI
   43139833532), 68 frontend unit tests + typecheck + build (CI
   43217825626), a 16/16 live-process operational exercise, and a browser
   inspection record. Caveats: single-instance SQLite, per-process audit
   log, no server-side execution timeout, no payload-size guard.
2. **Independently scientifically evaluated workflow: NO-GO** — no
   rights-cleared measured reference/query set, no locked independent
   evaluation, no expert adjudication. Unchanged; nothing in this increment
   claims otherwise (all synthetic fixtures remain labelled synthetic).
3. **Production service: NO-GO** — live Firebase/Stripe/ledger verification,
   staging identities and multi-instance persistence remain unverified;
   the supported topology is single-host.
4. **Manuscript submission: NO-GO** — author approvals and declarations
   pending; review exports remain NOT AUTHOR APPROVED.

Spending this increment: US$0 inference, US$0 compute, no downloads, no
training, no deployment, no third-party contact. OpenRouter key absent from
the environment; Colab reports 0.00 compute units.

## Environment notes
- The cloud-managed Documents tree evicted the backend `.venv` and frontend
  `node_modules` mid-session. All verification used clean environments
  outside Documents (`/tmp/phyto-venv-20261112`, `/tmp/phyto-fe-hydrated`).
- Ops exercise transcript and browser screenshots:
  `/tmp/phyto-ops-20261112/` (transcript.json, ui-evidence/).
