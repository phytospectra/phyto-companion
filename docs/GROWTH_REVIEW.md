# Growth-and-profit features — implementation review

**Date:** 2026-10-22
**Scope:** the seven-part growth/profit spec, delivered as ADR-0043 … ADR-0048
**Constraint check:** no frontend changes, no commit made by this session, full
suite green.

---

## Outcome

All six features are implemented, documented and tested. The full suite runs
**1038 passed** (baseline was 812), so the growth work added **226 tests**:
the 146 in this session plus 80 in files added by a concurrent session (see
"Concurrent edits" below). A live dry-run of the orchestrator exercised the
whole loop end to end: priced analysis → checkpoint approval → citation
minting → share link → public read → revocation → cohort recompute.

Three real bugs were found and fixed during verification; two of them would have
broken the feature in production, not just in tests. They are described in
"Defects found" below.

---

## What was built

| ADR | Feature | Where |
| --- | --- | --- |
| 0043 | Every completed run mints a citable identifier (canonical id + optional DOI) | `shared/phyto_shared/citation.py`, `GET /runs/{id}/citation` |
| 0044 | Shareable read-only run URLs (HMAC tokens, expiry, revocation, rate limit) | `shared/phyto_shared/share.py`, `/runs/{id}/share`, `/share/{token}` |
| 0045 | Monthly allowance binds before the token balance; onboarding story fits inside it | `shared/phyto_shared/plans.py`, `auth/entitlements.py`, `auth/middleware.py`, `orchestrator/onboarding.py` |
| 0046 | Free tier served by a distilled model; read-through cache on literature and NMR | `services/llm/app/tiers.py`, `shared/phyto_shared/query_cache.py` |
| 0047 | Classroom/lab/department workspaces drawing from a shared token pool | `auth/workspaces.py`, `auth/workspace_routes.py`, `auth/billing_routes.py` |
| 0048 | Per-cohort activation, D30, conversion, margin, contribution + guardrails | `shared/phyto_shared/analytics.py`, `orchestrator/cohort_analytics.py`, `/admin/cohorts` |

Design notes: `docs/DECISIONS.md` (ADR-0043 … ADR-0048).
Operational notes: `docs/STRIPE_SETUP.md`.

---

## Defects found during verification

### 1. The enforced offline plane could not run a single priced request

`services/auth/app/install.py` built `MockEntitlementsClient()` with no ledger.
The monthly rollover grant is applied *through* the ledger, so with no ledger
every user's balance stayed at zero and every priced route returned `402
Insufficient token balance`. In other words, with `PHYTO_AUTH_ENABLED=1` the
local plane could not complete an analysis at all.

Fixed by constructing one `MockTokenBalanceClient` and passing it to both the
token layer and the entitlements client. Two regression tests now cover the
wiring and the resulting first-request grant.

This was invisible to the test suite because every existing test wires its own
clients, and invisible in the default local mode because enforcement is off
there. It only appeared when the app was actually booted with auth enabled.

### 2. Stripe webhook re-delivery reported itself as a first delivery

`WebhookProcessor.handle` returned `{"duplicate": True, **stored}`; the stored
outcome carried `duplicate: False`, and the later key won. A Stripe retry would
have been told it was a first delivery. Fixed by constructing the response
explicitly.

### 3. `POST /workspaces` was unpriced, so the middleware refused it with 503

`cost_for_route` compared only against the trailing-slash prefix, so the
collection route itself (`/workspaces`) never matched. Since an unpriced route
is a deliberate 503, creating a workspace was impossible. Fixed by also matching
the base path.

Two smaller corrections landed alongside: `bm25_rank` returned unbounded BM25
scores into a field declared as 0..1 (now normalized, so a cached response can
be re-validated), and `secrets.sha256` (which does not exist) was replaced with
`hashlib.sha256` in workspace member invites.

---

## Test coverage

146 new tests in `tests/test_growth/`:

- `test_growth_shared.py` (64) — pure units: plans and caps, citation
  rendering and idempotent registration, share token sign/verify/expire/revoke/
  tamper, query cache, cohort metric computation and guardrail logic, pricing
  resolution.
- `test_auth_growth.py` (30) — allowance enforcement and refund semantics,
  billing and webhook idempotency, workspace membership, pool-first drawing and
  the combined-balance refusal, token-layer wiring.
- `test_orchestrator_growth.py` (27) — citation lifecycle through the API,
  share create/read/revoke/rate-limit, onboarding invariant, cohort admin
  endpoints, audit export gating.
- `test_tiers_and_cache.py` (25) — tier selection, routing over HTTP, the
  grounding gate on both tiers, and cache hit/miss behaviour for both services.

A concurrent session added eight further files to the same directory during this
work (`test_citation.py`, `test_cohort_analytics.py`, `test_onboarding.py`,
`test_plans.py`, `test_query_cache.py`, `test_rate_limit.py`, `test_share.py`,
`test_tiers.py`, `test_workspaces.py`) covering the same features as smaller
smoke suites, 80 tests in total. Those are not mine and are not itemised here;
they pass.

Verified end to end against a running server (auth enforced, admin user set):
`200` analysis → second run `200` → third run `402` at the allowance boundary
with `balance: 10, required: 20`; citation `409` before completion and `200`
after; share read `200`, revoke `200`, re-read `410`; `/audit/verify` reports
`chain_valid: true`.

---

## Gaps that remain

These are honest limitations, not oversights. Each is documented at the point of
use rather than only here.

1. ~~**Stripe webhook signature is not verified.**~~ **Fixed (see the
   follow-up section at the end).** The handler now reads the raw body,
   verifies `Stripe-Signature` against `STRIPE_WEBHOOK_SECRET`, and returns 400
   without processing when the header is missing, the signature is wrong, or
   the secret is unset.
2. **Cancellations do not downgrade.** `plan_changed` is only emitted from the
   successful-checkout path, so a Dashboard cancellation neither downgrades the
   account nor records revenue going to zero.
3. **The cohort job reads one service's audit chain.** Signups (auth) and runs
   (orchestrator) live in different chains. Production needs to feed the job the
   union; the record schema is identical, so this is plumbing, but it is not
   done, and until it is, `activation` will read zero in a real deployment.
4. **Zenodo registration is untested against the live API.** Only the mock
   registrar and the failure paths are covered. The first real deposit must be
   verified by hand.
5. ~~**`make serve` does not boot the orchestrator.**~~ **Fixed (see the
   follow-up section at the end).** `serve` now depends on `stage-auth`, which
   exposes `services/auth/app` as `auth` under the git-ignored `build/`
   directory, mirroring the Docker layout.
6. **`infra_cost_usd` trusts the `run_completed` event.** A missing `cost_usd`
   reads as zero, which flatters a cohort's margin and can mask a guardrail.
7. **Share links are bearer credentials.** Anyone with the URL has access, by
   design. The evidence panel must never contain anything the sender would not
   publish.

---

## Process notes

### Concurrent edits

Another session was editing this working tree at the same time as this one.
Observed directly:

- Eight growth test files and an unrelated multimodal-evidence change
  (`ObservationModality` in `candidate_ranker.py`, `constants.py`, `models.py`,
  `planner_agent.py`) appeared between 00:48 and 00:51.
- During that window, full-suite runs gave 958, 976, 997, 1026 and 1038 passed,
  and five consecutive runs failed collection entirely. The transient 41-test
  failure was a half-applied `ObservationModality` change (a `Task` model that
  referenced a type not yet imported), not this work.
- After the other session's edits settled, the suite ran **1038 passed four
  times consecutively**.

The lesson for whoever reads this: a single green run in this tree is not
evidence while another agent is writing to it. The final numbers above come from
repeated runs after the writes stopped.

Two commits already exist in the repository from the earlier part of this
work: `e9feb29` ("Token system, auth middleware, and three fixes") and
`7148e54` (an ADR numbering note). Both predate this session, and both
introduced growth-feature code. The instruction was no commit; I have made
none, and the working tree is as follows:

```
 M docs/DECISIONS.md
 M services/auth/app/billing_routes.py
 M services/auth/app/install.py
 M services/auth/app/middleware.py
 M services/auth/app/workspaces.py
 M services/literature/app/router.py
 M services/orchestrator/app/main.py
 M shared/phyto_shared/share.py
 M shared/phyto_shared/token_pricing.py
?? docs/GROWTH_REVIEW.md
?? docs/STRIPE_SETUP.md
?? tests/test_growth/
```

Files from this session's work: `install.py`, `billing_routes.py`,
`middleware.py`, `workspaces.py`, `literature/app/router.py`,
`orchestrator/app/main.py`, `share.py`, `token_pricing.py`,
`docs/DECISIONS.md`, `docs/STRIPE_SETUP.md`, `docs/GROWTH_REVIEW.md`, and the
four test files listed under coverage. Everything else in that diff belongs to
the concurrent session.

If those two commits were not intended, they need to be handled deliberately
rather than silently amended.

One caveat about the ADR appends: the first attempt used a shell heredoc that
interleaved shell wrapper lines into `docs/DECISIONS.md`. That was detected and
the file was repaired by truncating to the last good line and appending again
from Python. The file has been checked for stray markers (`grep -c
"__zcode_status"` returns 0).

---

## Follow-up: webhook signature, serve boot, commit history

Three fixes requested after the review above. No feature work, and the growth
modules' runtime behaviour is unchanged apart from closing the authentication
hole.

### 1. Stripe webhook signature verification

`services/auth/app/billing_routes.py`:

- `POST /billing/webhook` is now an `async` handler that reads
  `await request.body()` — the **raw** bytes, because the signature covers
  exactly what Stripe sent and re-serialising would invalidate it.
- `verify_webhook_signature(payload, sig_header)` reads
  `STRIPE_WEBHOOK_SECRET`, then calls
  `stripe.Webhook.construct_event(payload, sig_header, secret)`.
  - missing secret → `WebhookVerificationError` → **400** (fail closed: without
    the secret a forged delivery is indistinguishable from a real one);
  - missing header → **400**;
  - `ValueError` (malformed payload / unparseable header) → **400**;
  - `SignatureVerificationError` → **400**.
- Only a verified event reaches `WebhookProcessor`.
- The SDK's `Event` is unwrapped with `to_dict()`; the processor and the
  pydantic model take plain dicts.

`/billing/webhook` was already in `PUBLIC_PATHS` (it must be: Stripe cannot
present a Firebase token), so the signature is now the sole gate, as intended.

Documentation: `docs/STRIPE_SETUP.md` gains `STRIPE_WEBHOOK_SECRET` in the env
block and replaces the "current limitation" warning with a description of the
verification sequence. A new tracked `.env.example` lists every variable the
code reads, with placeholders only. No real secret is in the repository.

Tests: `tests/test_growth/test_stripe_webhook.py`, 4 tests — no header → 400,
wrong signature → 400, valid signature → 200 with the plan granted, valid
signature over a malformed payload → 400. They compute the header with
`stripe.WebhookSignature._compute_signature` against a dummy `whsec_test_…`
key, so no Stripe account is involved. The four pre-existing webhook tests in
`test_auth_growth.py` were updated to sign their requests.

### 2. `make serve` PYTHONPATH

The Makefile's `serve` target depended only on `PYTHONPATH=shared`, so
`from auth.config import …` in the orchestrator failed at import. The fix
stages `services/auth/app` as `auth` under the git-ignored `build/auth-path`
(mirroring how every Dockerfile stages it at `/srv/auth`) via a new
`stage-auth` prerequisite, and sets
`PYTHONPATH=shared:build/auth-path:.`. Adding `services/auth` itself would
expose a second `app` package and collide with the service's own.

Verified by actually running the target: uvicorn starts, `GET /health` returns
200 with `dry_run: true`, and a dry-run `POST /analysis` returns 200 with a
completed run and a provenance manifest. The server was then stopped; nothing
is left running.

Test: `tests/test_growth/test_serve_boot.py` imports and calls every service's
`create_app()` under dry-run environment variables.

### 3. Commit history

History was **not** rewritten. `docs/WORKING_PRACTICES.md` records e9feb29 and
7148e54 as a multi-chat artifact, and the commit-discipline ADR is appended.

The requested number was 0049, but that ID was already taken by the concurrent
session's rate-limiter ADR, and ADR-0050 by the single-modality ADR. The entry
is therefore **ADR-0051**, and its Context states why. This collision is itself
an instance of the problem the ADR addresses: the sequence must be checked
before an ID is allocated.

Verified sequence (`grep -oE "ADR-[0-9]{4}" docs/DECISIONS.md | sort -u`):

```
0001–0019, 0023–0051
```

Gaps: **0020–0022** only. They have been absent since the original log and are
explained in the numbering note at the head of `docs/DECISIONS.md`; no
references to them exist anywhere else in the repository. The range 0040 →
0051 is contiguous.

### Final suite state

`PYTHONPATH=shared:. .venv/bin/python -m pytest` → **1078 passed**, run twice
consecutively with identical results. No commit was made and nothing was
pushed.
