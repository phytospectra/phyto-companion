# Frontend / auth / accounting verification — 2026-11-09

## Scope and exact source baseline

Offline source and automated-test work only. No production HTTP calls, real sign-ins, identity/provider changes, purchases, billing changes, secret changes, deployment, commit or push were performed by this workstream. Backend executor, reference-library and benchmark files were not edited. Other agents' changes were preserved.

- Root: `/Users/subhadipbanerjee/Documents/NMR-MS-data analysis`.
- Existing `HANDOFF_frontend_gap_audit.md` identifies the nested **separate Git repository** at `/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/phyto-companion/frontend`. Backend ignores this directory; frontend changes require a separate commit.
- Frontend initial clean `main`: `d7d7a491bdb21082ef4248b024acdc2923829463`, one commit ahead of local `github/main` `45b73dec1d8efee628e38141351a0a75b93a5c77`. These are local refs, not freshly fetched deployment evidence.
- Created branch `agent/frontend-auth-accounting-2026-11-09` before editing. No AGENTS.md or CLAUDE.md was found in the inspected project ancestry/frontend source.
- Frontend `github` remote: `https://github.com/phytospectra/phyto-companion-frontend.git`. `origin` is a local bundle path, **not** the publishing remote.
- Read-only `gh repo view phytospectra/phyto-companion-frontend --json nameWithOwner,url,defaultBranchRef,viewerPermission,isArchived` confirmed the exact repository, main default branch, nonarchived, ADMIN viewer permission. `gh auth status` confirmed active HTTPS account `subhadipban-lgtm`; no token value recorded here.
- Backend source baseline at inspection: `424fdda0a51d628cfdc363743d059472678bfa13`, branch `agent/phase0a-phase1a-failclosed-runtime`; main agent advanced this concurrently. Backend tests below ran against the shared working tree, not an isolated immutable revision.

## Findings and bounded frontend corrections

### Build-time Firebase / auth

`NEXT_PUBLIC_*` values are statically embedded by Next.js. `AuthProvider` selects demo only for the literal `NEXT_PUBLIC_USE_MOCKS === "true"`; real mode requires API key, auth domain, project ID and app ID. Docker previously forwarded only the mock flag and API base URL. Added the four Firebase ARG/ENV pairs before the build. Runtime-only container environment cannot repair a bundle built without these values. No real values were added or changed.

Changed `AuthProvider` to subscribe to `onIdTokenChanged` rather than only sign-in/out events, clear bearer/query cache/balance/402 retry state when accounts change or sign out, reject late token completions from a previous identity, handle token/import failures as unauthenticated, and unsubscribe/clear on real-mode unmount. Firebase SDK is stubbed in the new test; these changes are **not verified against a real Firebase project**.

### Workflow support and availability

Backend source contract `docs/ORCHESTRATOR_EXECUTION.md` supports only real `reference_hsqc_identify`; legacy `identify`, `compare`, `novelty`, `mechanism_hypothesis` frontend cards are not implemented live science. Frontend starts via `POST /runs`, while backend creates via `POST /analysis`. Form sends file names rather than uploaded spectrum bytes and does not implement the reference-HSQC observation contract. Renaming labels alone cannot fix this.

Added explicit demo-only/live-unavailable text, disabled all four live cards and live submit, and guarded submit against programmatic legacy payload dispatch. Mock workflows remain available in demo builds. This intentionally **does not implement or advertise reference-HSQC launch support**.

### Accounting and isolation

Billing previously loaded hardcoded transactions and simulated token purchases even in real mode; store initialized a fictional signup balance in every build. Live billing now renders an unavailable notice without mounting demo transaction/purchase logic. Only demo builds initialize the fixture balance; real balance starts unknown (`null`). No real checkout/balance endpoint was invented.

Backend 402 body (`balance`, `required`, `topup_url`) matches `InsufficientTokensError`; existing client tests cover bearer attachment, 401, 402 and idempotency-header propagation. Upload catches 402 into modal/retry state. The new auth isolation test seeds account-A query/balance/retry state, switches to account B, checks clearing, rejects A's late token, and checks token failure. Test authored, but frontend execution remains blocked as below.

**Server ownership defect found and corrected offline:** original backend read process-global `runtime.runs` without owner checks. On coordinator request, added `owner_uid` binding before execution and shared owner guard on run GET, approval, citation and share creation. Other principals and missing legacy owner metadata return 404 before state read/mutation. Owner survives approval entry replacement. Anonymous runs are allowed only with `PHYTO_ENV=local` and auth enforcement disabled; authenticated users cannot claim anonymous runs, and cloud missing-identity calls fail 401 even if auth middleware was disabled. Public tokenized share retrieval remains intentional capability access. No run events/cancel/pause routes exist in this backend. Four offline HTTP regression tests inject test principals only in a test app, covering Alice/Bob/anonymous/legacy-owner/cloud cases; no production exploit or real-user test attempted. Live identity/workspace integration still needs staging validation.

Other demo-first result/network/elucidation surfaces and hardcoded pricing still exist. This change is a bounded safety correction, not a complete live product conversion. Header may display `0` when balance is unknown; that is not authoritative balance evidence. Local mock transaction/idempotency state is not a substitute for server accounting.

## Commands and actual results

Commands run from backend cwd unless a frontend prefix/cwd is stated. No browser automation or GUI testing was used.

1. `npm --prefix '/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/phyto-companion/frontend' run test:unit`
   - **Failed**, exit 1. Vitest worker boot timeouts (`[vitest-pool-runner]: Timeout waiting for worker to respond`); no usable passing suite evidence.
2. Same command with `-- --maxWorkers=1`
   - Stalled after startup; explicitly stopped after approximately five minutes. **Not passed**.
3. `npm --prefix '<frontend>' exec -- tsc --noEmit --project '<frontend>/tsconfig.json'`
   - Stalled without diagnostics; explicitly stopped. **Not passed**.
4. In frontend cwd, direct `node node_modules/typescript/bin/tsc --noEmit --incremental false`, bounded with Python `subprocess.run(timeout=40)`
   - **Timed out at 40 seconds**, no diagnostics. No typecheck-success claim.
5. In frontend cwd, direct `node node_modules/vitest/vitest.mjs run tests/unit/live-boundaries.test.tsx tests/unit/auth-isolation.test.tsx tests/unit/client.test.ts --pool=threads --maxWorkers=1`, bounded at 40 seconds
   - **Timed out** after Vitest startup, no test results. New frontend regressions are authored but unverified.
6. `PHYTO_ENV=local PHYTO_DRY_RUN=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_auth tests/test_billing` (absolute interpreter and test paths used)
   - **113 passed, 1 warning in 7.20s**. Warning: Starlette TestClient/httpx deprecation. Includes reservation/confirm/refund/idempotency, billing handlers and mocked accounting contracts. Does not validate live Firestore/Stripe/Firebase.
7. Same offline pytest flags for `tests/test_growth/test_workspaces.py`
   - **9 passed in 0.21s**. Workspace/access/accounting contracts only, not run-resource ownership proof.
8. An initial combined pytest command included nonexistent `tests/test_entitlements_workspaces.py`: exit 4, no tests ran; corrected to the actual workspace path above.
9. `git -C '<frontend>' diff --check`
   - Passed, no whitespace diagnostics at final check.

Toolchain inspected: local Node `v26.11.1`, npm `12.2.2`. Current committed manifest has Vitest `^5.0.5`, Vite `^8.3.5`, Next `^16.1.1`, TypeScript `^5`; lock pins Vitest `5.0.5`, Next `16.4.2`, TypeScript `5.9.3`. No package/lockfile edits by this workstream. An earlier status message suspected a Vitest version mismatch; subsequent HEAD/manifest/lock inspection disproved that suspicion. Do not infer a proven runtime-version root cause from the hangs. No Node 22/24 binary found at Homebrew alternative paths. No clean install or dependency upgrade attempted. Final process check found no residual Vitest/tsc workers from these runs.

**Production frontend build was not run or verified**; it may fetch Google fonts via `next/font/google`. No frontend CI workflow is tracked, so backend CI is not frontend verification evidence. A controlled clean toolchain run remains required.

## Files and review/staging requirements

Frontend repository changes (relative to its root):

- `Dockerfile`
- `src/auth/AuthProvider.tsx`
- `src/components/phyto/billing/BillingPage.tsx`
- `src/components/phyto/upload/UploadPage.tsx`
- `src/components/phyto/upload/WorkflowSelector.tsx`
- `src/lib/phyto/stores.ts`
- `tests/unit/auth-isolation.test.tsx` (new)
- `tests/unit/live-boundaries.test.tsx` (new)

Backend changes: `services/orchestrator/app/main.py`, new `tests/test_orchestrator_run_ownership.py`, and `docs/FRONTEND-AUTH-ACCOUNTING-2026-11-09.md`. Parent staging was observed; this workstream did not stage, commit, push or modify manifests/locks. Coordinator subsequently reported frontend commit `9632bb1` and push underway; that is coordinator-reported integration, not a commit made by this workstream. Review staged and unstaged diffs before committing, preserving concurrent changes.

Additional bounded validation after the owner fix: `PHYTO_ENV=local PHYTO_DRY_RUN=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q tests/test_orchestrator_run_ownership.py tests/test_orchestrator.py` — **74 passed, 1 warning in 2.94s**. `.venv/bin/ruff check --no-cache services/orchestrator/app/main.py tests/test_orchestrator_run_ownership.py` — **All checks passed**. Backend `git diff --check` passed. These tests cover the new owner checks and existing anonymous offline orchestrator compatibility, not live Firebase enforcement.

Recommended eventual frontend publish target, only after parent review/authorization and validation: `git push -u github agent/frontend-auth-accounting-2026-11-09`. Do not push to the local-bundle `origin`; do not merge main automatically.

Before any release claim:

1. Reproduce unit/type/build checks on a clean lockfile-based controlled Node toolchain; add/require frontend CI independently of backend CI.
2. Use isolated staging only, build with mock flag false plus all four Firebase public settings and staging API URL; confirm bundle selection rather than setting runtime env alone.
3. Verify successful login, refresh, sign-out, account switch, missing/invalid/expired token and CORS behavior with dedicated test accounts/emulators, never production accounts.
4. Validate implemented run owner enforcement with staging identities, including cross-user GET/approval/share rejection and account-scoped idempotency/balance effects; current offline owner tests are not live tenant-isolation evidence.
5. Integrate actual supported workflow request/result/status contract and spectrum transfer before enabling launch; preserve unavailable labels meanwhile.
6. Connect authoritative balance/transactions/checkout and mocked/emulator accounting settlement tests before offering real purchase or automatic retries. No billing/identity/secrets change is authorized by this report.
