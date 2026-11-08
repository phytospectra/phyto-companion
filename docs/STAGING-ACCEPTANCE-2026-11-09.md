# Staging acceptance procedure — offline verified, live pending

## Offline operational coverage (all passed locally, not live Firebase)

Real HTTP/auth middleware with mock SDK verifier and mocked ledgers
(`tests/test_operational_integration.py`): missing/malformed/expired/revoked/
wrong-project identities return 401 before any reservation or state change;
run reads, approvals, citations, share creation and per-run revocation are
owner-bound and unauthorized attempts leave runs/shares/balances/allowances/
audit events unchanged; signed shares are capability access (no Firebase)
and revoked/tampered tokens fail; duplicate idempotency keys return 409
without re-executing handlers, in parallel and across operations/UIDs;
reservation success/refund/cache paths settle exactly once; CORS preflight
allows only approved origins and 401/402/503 preserve CORS headers.
See `test_orchestrator_run_ownership.py` for the ownership contract.

## Live prerequisites (exact inputs needed, none attempted)

1. An isolated, non-production Firebase project ID and two approved test
   identities with empty, disposable balances; secret/service-account access
   is NOT needed for the tester (auth emulator or staging project only).
2. An approved staging backend URL and frontend build with
   `NEXT_PUBLIC_USE_MOCKS=false` plus the four Firebase public config fields;
   approved CORS origins list.
3. Browser access for the documented flows, or an approved headless
   automation environment.

## Procedure once provided (not executed here)

- Missing/invalid/expired/revoked/wrong-project bearer on `/tools` → 401.
- Valid staging identity: login/logout, token refresh, account switch;
  stale-response rejection; `/analysis` with the documented real workflow;
  review/resume checkpoint; provider failure returns explicit status.
- CORS: OPTIONS from each approved origin; unsupported origins fail; CORS
  headers persist on 401/402/503.
- Ledger: reservation/refund/idempotency on isolated test accounts only;
  no production transactions, balances, IAM, traffic or billing changes.
- Record revisions, image digests, observed responses, and abort criteria.

Live login, revocation, authoritative billing and multi-instance persistence
remain unverified. Runs/shares are process-local; cloud restart/cross-instance
persistence and revocation semantics are a separate production design decision.

See docs/ACCOUNTING-GUARANTEE.md for the precise guarantee (at-most-once admission, exactly-once settlement) and failure/recovery matrix.

## Read-only cloud observation (2026-11-09)

Active `gcloud` session lists exactly one project, `phyto-companion-prod`,
with services `phyto-orchestrator` and `phyto-frontend` on Cloud Run. No
staging/isolated project or service exists in the visible inventory. Live
acceptance therefore requires creating (or being granted access to) a
separate non-production project — a consequential action that needs
approval: no project, identity, IAM, traffic, billing or secret change was
made and none is requested here beyond the preparation below.

## Approval packet for staging creation (prepared, not executed)

Requested change: create an isolated project `phyto-companion-staging`
(exact name to be chosen by the operator), deploy the pinned revisions
(backend `5d56a24863f492a0b1fee4e9296a39cf85f65157`, frontend
`e8bdbe6875ca3f587dfdc27dd56d3be0804079a5`) with two disposable test
identities, CORS limited to the staging frontend URL, and run the offline
checklist from this document against it. Cost boundary: compute/storage
only, no paid inference, Stripe test mode or emulator, no production data.
Cleanup: delete the project or disable the services after the run.
Rollback: none needed in an isolated project; production untouched.

If the user approves staging creation, the checklist above is the exact
acceptance run. If they prefer, supply an existing isolated project +
two test identities instead, and no new project is needed.
