# Enterprise foundations — implementation scope, 2026-11-09

This increment connects the existing real reference-ranking executor to the live
frontend and strengthens review and request boundaries. It is not a production
certification or independent scientific validation.

## Backend contract

- `GET /capabilities` is authenticated, explicitly zero-priced, and returns
  versioned capability metadata. Real mode advertises only its configured
  supported workflows; mock mode never advertises the real workflow.
- Configuration/dependency availability is separate from scientific validation.
  `production_ready` remains false and `run_storage` is `process_local`.
- `GET /runs/{id}` returns the original ranked outputs, assessment and provenance
  behind the existing ownership check. Reloading does not rerun scientific tools.
- Authenticated checkpoint attribution uses the verified principal, not the
  caller-supplied `actor` text. Offline anonymous tests retain explicit actor text.
- Review/resume is serialized within the process so concurrent approvals cannot
  execute the continuation twice. This is not a distributed locking guarantee.
- Orchestrator responses use `Cache-Control: no-store`, `nosniff`, and frame denial,
  including authenticated failures and revocable-share responses.

## Frontend contract

Live `/upload` now hosts the HSQC reference workspace. It validates versioned
capabilities before permitting submission and calls `/analysis`, `/runs/{id}`,
and `/runs/{id}/approve`, rather than the legacy demo `/runs` creation API.

The workspace accepts a sample identifier, known neutral formula, and up to 256
HSQC peaks (H ppm, C ppm, optional intensity; omitted intensity is 1). The server
remains authoritative for chemistry and input validation. The token price comes
from the backend and is explicitly provisional. No paid provider is introduced.

Results show ranking, missing references, assessment reasons, limitations and
provenance. JSON export includes the full returned evidence. Completing a human
checkpoint does not turn the ranking into a validated identification.

The live shell hides unsupported demo screens and blocks direct navigation before
mounting their loaders. Demo mode remains explicit. Account UID changes remount
the workspace; outstanding requests are aborted and older-session API responses
are rejected. Private spectra/results are not added to browser persistence.

All client requests have a bounded deadline (default 120 seconds), with cleanup
of abort listeners/timers. Mutations are never automatically retried. An uncertain
submission freezes launch and retains its request key and any known run ID.
Operators must reconcile unknown outcomes before a new-key submission. HTTP 409
is not evidence that the original operation failed. A refund likewise does not
prove no external effect occurred. Accounting guarantees are stated as at-most-once
terminal settlement, not guaranteed eventual settlement after a crash.

## Build and CI

The frontend now has an Actions workflow for main, agent branches, and PRs:
locked npm installation, TypeScript, the complete unit suite, and production build.
Local verification uses an isolated hydrated checkout outside Documents; symlinked
external node_modules are not compatible with Turbopack's hermetic root.

Backend regression tests cover capability authentication, mock/real discovery,
result reload, cross-user rejection, verified review attribution, private-response
headers, and concurrent approval. Frontend tests cover the connected workflow,
review consent, malformed inputs, unknown outcomes, account transitions, deadline
handling, duplicate-key refusal and direct-navigation safeguards.

## Remaining enterprise acceptance gates

1. Durable run/task/checkpoint/artifact storage and distributed concurrency control.
   Current run dictionaries and review locks are process-local. Do not deploy this
   runtime with multiple workers/replicas or promise restart recovery.
2. Durable request-state reconciliation with content-bound keys, explicit recovery
   state, operator tools, and crash testing against the actual production store.
   Duplicate suppression alone is not exactly-once external execution.
3. Isolated live identity/tenant/accounting acceptance, least-privilege IAM, secret
   rotation, audit retention, backups and tested restore/rollback.
4. Load/soak tests, quotas/resource limits, service-level objectives, monitoring and
   incident response for the supported execution path.
5. Rights-cleared measured libraries, independent query evaluation under a locked
   protocol, scientific review, and final author declarations.

No production deployment, release, DOI, paid inference, or training is part of
this source increment. The research preview is useful for controlled review;
scientific and production readiness remain unverified.

## Verification update

Backend application increment `4127b3e` passed GitHub CI (run 42523780897).
Frontend application increment `f327754` passed GitHub CI (run 42523787108).
Follow-up frontend `0d361bf` preserves same-account token refresh, rejects missing
CSV fields and recovers a known run after a result-fetch failure. Its isolated
local TypeScript check, 62 tests across 15 files and production build pass.

The synthetic model pilot adds offline tests and a plan-only command. It has not
called a model. Budget/account preflight findings and proposed scientific design
are recorded separately in `COLAB-CLI-PREFLIGHT-2026-11-09.md` and
`../benchmark/agent_comparison/RESEARCH-PLAN.md`. These preparations do not close
the scientific or enterprise acceptance gates above.
