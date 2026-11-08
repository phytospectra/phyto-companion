# Release and authentication readiness — 2026-11-08

## Decision and scope

**NO-GO for a public production/scientific release. GO for preparing a clearly labelled draft prerelease, subject to the gates below.** Authentication is already activated in the observed Firebase project; the old activation instructions are stale. Authentication working does not establish scientific-runtime readiness.

Investigation root: `/Users/subhadipbanerjee/Documents/NMR-MS-data analysis/phyto-companion`.

Only this document was intentionally added to the checkout. No source/configuration changes, installs, commits, pushes, releases, Firebase user operations, credential changes, traffic changes, OSF uploads/registrations, paid inference, model downloads, or training were performed. Tests used the existing `.venv`; bytecode and pytest/Ruff caches were disabled for these runs. The benchmark rebuilding script ran in a disposable copy, not against checked-in artifacts. Tests can use their own temporary artifacts and loopback fake HTTP servers.

## 1. Git, licensing, version and release controls

- Backend Git repository exists (despite the session's initial environment description). Initial worktree was clean.
- Branch: `agent/phase0a-phase1a-failclosed-runtime`.
- HEAD: `57c45541c6907a831f7f8ec72dc06b6918187239`, committed `2026-11-02T10:10:09+07:00`.
- Origin: `https://github.com/phytospectra/phyto-companion.git`; public, not archived, default branch `main`.
- Local comparison: `git rev-list --left-right --count main...HEAD` returned `0 18`. Read-only `git ls-remote` confirmed remote feature branch at HEAD and remote main at `bff329ae0f75c8f0c96f20112dd31072221306e5`.
- `git tag --list`, GitHub tags API, and `gh release list` returned no entries. No release/tag was created.
- Root `pyproject.toml` version is **0.1.0**; `CITATION.cff`, shared package and inspected service metadata also use 0.1.0.
- **Recommended draft tag/version: `v0.1.0a1` / `0.1.0a1`**, a PEP 440 alpha of the actual 0.1.0 base, not an invented 1.x or completed stable release. Draft title: “0.1.0 alpha 1 — fail-closed runtime and evidence harness”. Keep draft and prerelease flags, document mock/scaffold limitations, and do not publish until the chosen source revision passes CI and metadata review. No version files were changed here.
- Root `LICENSE` is Apache-2.0, matching pyproject, CITATION and GitHub's license detection; `NOTICE` names the 2026 copyright holder. `data/LICENSE` is CC0, **not a blanket relicensing of external datasets**. SUMMIT/NP-MRD lineage has CC-BY-NC restrictions in the benchmark protocol. Per-record provenance, redistribution and external-inference rights must be checked independently.
- `CITATION.cff` still contains affiliation/ORCID placeholders and `date-released: "[DATE TBD — to be filled at Zenodo release]"`. Resolve or omit unknown metadata before publication; do not fabricate ORCIDs, affiliations, dates or a DOI. No DOI was minted or inferred. Mock DOI/citation code is not a registration receipt.
- No dedicated release workflow or Zenodo configuration was found in the inspected tracked configuration.
- `frontend/` is ignored by the backend repository and is a separate Git checkout. Its local HEAD is `d7d7a491bdb21082ef4248b024acdc2923829463`, clean, local `main` one commit ahead of its local `github/main` tracking ref (not freshly fetched). GitHub remote is `phytospectra/phyto-companion-frontend`; `origin` points to a local bundle. A backend source archive does not include that ignored frontend checkout. Pin and release the two independently.

### CI and repository safeguards (read-only GitHub observations)

- `.github/workflows/ci.yml`: push on **main only**, all pull requests; Ubuntu, Python 3.12, 30-minute job; unpinned pip dependency install, `make stage-auth`, `make lint`, `PHYTO_ENV=local make test`.
- Actual `make lint` scope is **`shared services scripts tests`**, with Ruff E9/F rules, not all files. `make test` discovers root `tests/` only; the standalone benchmark regression script is not part of that gate.
- Latest listed CI run: `40478567240`, success, 2026-10-27, SHA `bff329ae0f75c8f0c96f20112dd31072221306e5`. The later successful Dependency Graph run is not the application test gate.
- HEAD check-runs API returned `total_count: 0`. Local passes below do not constitute CI coverage of these 18 feature commits.
- Main protection API: HTTP 404 “Branch not protected”; repository rulesets: `[]`.
- Actions enabled, all actions allowed, SHA pinning not required. Default workflow permission is read; Actions PR review approval is disabled.
- Before release: review/merge via a PR, obtain fresh CI on the selected revision, consider required checks/branch protection and reproducible dependencies, and explicitly add benchmark checks to the intended release gate. No repository setting was changed.

## 2. Actual check results

Environment: Python **3.12.15**, pytest **9.1.1**, Ruff **0.17.2**, existing `.venv`. No installs.

Commands were run from the investigation root. Common pytest prefix:

```sh
PYTHONDONTWRITEBYTECODE=1 PHYTO_ENV=local .venv/bin/python -m pytest -p no:cacheprovider
```

| Check | Actual result |
|---|---|
| Focused `tests/test_osf_evidence.py tests/test_benchmark_context.py -q` | `49 passed in 10.12s`, exit 0 |
| Focused `tests/test_auth tests/test_elucidation/test_runtime_modes.py tests/test_elucidation/test_structure_db_provider.py -q` | `222 passed in 2.98s`, exit 0 |
| Full root `-q` | **`1641 passed, 4 skipped, 14 warnings in 362.84s (0:06:02)`**, exit 0 |
| `.venv/bin/ruff check --no-cache services shared tests` | `All checks passed!`, exit 0 |
| Exact CI scope `.venv/bin/ruff check --no-cache shared services scripts tests` | `All checks passed!`, exit 0 |
| Broad `.venv/bin/ruff check --no-cache .` | **exit 1; six findings**, below |
| Isolated `benchmark/blinded_pilot/test_repairs.py` | **15 checks, 0 failures**, exit 0 |

Full-suite warnings: one Starlette/httpx test-client deprecation, 11 SciPy precision-loss warnings on nearly identical samples, and two pytest class-scoped instance-method fixture deprecations. The four skips are `tests/test_metabolomics/test_mzmine_live_integration.py`: live MZmine integration requires both `MZMINE_HOME` and `MZMINE_USER_FILE` (a login-created `.mzuser4` profile). These missing prerequisites were not installed or configured. An additional targeted `test_nmr_advanced_bundle.py` check returned `4 passed in 1.92s`.

Broad Ruff findings (outside configured CI scope):

```text
benchmark/blinded_pilot/build_cases.py:36: F401 csv imported but unused
benchmark/blinded_pilot/test_repairs.py:18: F401 hashlib imported but unused
benchmark/blinded_pilot/test_repairs.py:84: F841 dups assigned but never used
benchmark/blinded_pilot/test_repairs.py:142: F841 rc assigned but never used
benchmark/blinded_pilot/test_repairs.py:219: F841 reg assigned but never used
benchmark/fine_tune_gate/colab_finetune_template.ipynb:cell 3: F401 os imported but unused
```

Benchmark execution details: the first disposable-copy attempt copied only `blinded_pilot/` and failed with `CalledProcessError` from its builder because the repository-relative fixture layout was missing. Corrected the **temporary copy layout**, not source: copied `benchmark/blinded_pilot/` and `tests/fixtures/real_npmrd/` under the same temporary root and invoked the copy with the absolute existing `.venv/bin/python`. All 15 checks passed, including packet blinding, chemistry consistency, valid HSQC/fabricated HMBC/cross-case citations, missing/duplicate/unknown submissions, placeholder exclusion and reproducible rebuilding. The original package hash was independently checked, without rebuilding it:

```text
fdc1646b460acc0c2d41ccc014d48cc4d63d35e3f8a6863cd148aa806a39acc1
seed=20261102
```

OSF tests use fake local servers and fail-closed embargo/auth/upload conflict paths; these are not proof of OSF account access, successful remote upload, embargo registration, or a DOI. No live OSF operation was run.

## 3. Deployment access and live observations

Read-only access was available through existing `gh` and `gcloud` sessions. GitHub reports repository admin permission; **write/deploy permission was not tested**. Firebase CLI was not present, but authenticated Identity Toolkit GETs succeeded. No browser/provider login, valid user token, Secret Manager contents, Cloudflare access, end-user acceptance, deployment build, or inference execution was available/used for verification. Those remain unverified, not assumed.

Project `phyto-companion-prod`, region `us-central1`:

| Service | Serving revision (100% traffic) | Observed image digest |
|---|---|---|
| Backend | `phyto-orchestrator-00010-xbk` (created 2026-10-25) | `us-central1-docker.pkg.dev/phyto-companion-prod/phyto/orchestrator-fullplane@sha256:ffb732f5aa01420822920f58a1317761775e114c48e6f52accb287deb3d91527` |
| Frontend | `phyto-frontend-00005-6qh` (created 2026-10-26) | `us-central1-docker.pkg.dev/phyto-companion-prod/phyto/frontend@sha256:a9bb24a2bf993b4004fb3dd7d5517d3e60e089ef47281ac189a5a157d9740a33` |

Backend URL: `https://phyto-orchestrator-sbqloxr3xa-uc.a.run.app`.
Frontend URL: `https://phyto-frontend-sbqloxr3xa-uc.a.run.app`.

Backend service-level environment override names contain **only `PHYTO_CORS_ORIGINS`**. Value: `https://app.phytospectra.com,https://phytospectra.com,http://localhost:3000`. No volume mounts were reported. Runtime service account is the project's default compute service account, not a separately evidenced least-privilege identity. Absence from service overrides does not prove absence of image-baked defaults: the current Dockerfile sets `PHYTO_ENV=cloud`.

Live non-mutating checks:

```text
GET /health -> HTTP 200
{"status":"ok","service":"orchestrator","version":"0.1.0","environment":"cloud","registry_size":27,"audit_records":0,"dry_run":false}
GET /tools (no Authorization) -> 401
{"detail":"Missing or malformed Authorization header; expected 'Bearer <token>'."}
GET /tools (deliberately invalid bearer) -> 401
{"detail":"Invalid or expired token: InvalidIdTokenError"}
```

This supports real token-verification enforcement on the observed old backend; it does not prove valid-user ledger access or deployment of feature HEAD. Live registry size 27, revision dates, and missing revision-to-Git mapping prevent claiming that this is the current source runtime.

Firebase configuration GET succeeded: **Email/Password enabled**, anonymous sign-in disabled; Google provider `google.com` **enabled**. Authorized domains: `localhost`, `phyto-companion-prod.firebaseapp.com`, `phyto-companion-prod.web.app`, `phytospectra.com`, `phytospectra.org`, `app.phytospectra.com`.

The documented canonical `www.phytospectra.com`, other `www` variants, and `app.phytospectra.org` are not all in that observed authorization list; they are also not all in backend CORS. Resolve the actual supported host set deliberately. Do not bulk add localhost/preview origins to production by default.

Frontend runtime overrides: `NEXT_PUBLIC_USE_MOCKS=true`, `NEXT_PUBLIC_API_BASE_URL=https://phyto-orchestrator-819429615655.us-central1.run.app`. **These are runtime observations, not proof of the already-built Next.js client bundle.** Its actual browser auth flow was not exercised. `frontend/Dockerfile` correctly notes build-time inlining, but declares only those two build args, not the four Firebase build variables required by source.

## 4. Authentication and scientific-mode coupling

Source of truth: `services/auth/app/config.py`, `install.py`, `firebase_auth.py`, `middleware.py`; `services/elucidation/app/runtime_config.py`, `main.py`, `shift_predictor.py`; `services/orchestrator/app/main.py`, `tool_registry.py`.

| Runtime settings | Authentication collaborators | Scientific shift runtime |
|---|---|---|
| Local defaults | enforcement off; mocks | mock predictor allowed |
| `PHYTO_ENV=cloud`, `PHYTO_DRY_RUN=1`, auth enabled | **mock verifier/ledger**, accepts `mock:<uid>` | mock predictor |
| Cloud, dry-run unset or `0`, auth enabled/default | real Firebase + Firestore + Pub/Sub | real predictor selection; requires endpoint/project |
| `PHYTO_AUTH_ENABLED=0` | middleware bypasses authentication | does not independently disable scientific/cloud work |

**There is no existing environment switch for real Firebase verification plus globally mocked science.** `PHYTO_AUTH_ENABLED=1` controls enforcement only; it does not override dry-run collaborator selection. Do not use `PHYTO_DRY_RUN=1` as a safe public auth staging toggle. A request JSON `dry_run` field is a different workflow value, not the global security/client switch.

Real token verification uses Firebase Admin `verify_id_token(..., check_revoked=True)` and returns 401 on failure. ADC is supported when `FIREBASE_CREDENTIALS_PATH` is absent. If used, that variable must contain a **filesystem path to a readable service-account JSON**, not JSON contents from a secret env mapping. The deployment guide's placeholder secret command and env mapping are not a safe production recipe; prefer the Cloud Run workload identity/ADC and least privilege, or an approved mounted secret file, without printing its contents.

Real auth also constructs Firestore token balances, entitlements/workspaces and Pub/Sub `usage-events`. Verify IAM, Firestore database/index/transaction behavior and topic existence independently. Some authenticated requests may initialize/grant an allowance; valid-token smoke checks are therefore a controlled staging activity, not part of this read-only investigation.

Elucidation real mode validates **`VERTEX_ENDPOINT_ID` and `GOOGLE_CLOUD_PROJECT`**, with `VERTEX_LOCATION` default `us-central1`. Important timing detail: the runtime is cached and constructed lazily by `get_runtime()`, including from `/health`; `create_app()` does not eagerly construct the scientific runtime. Thus failure at runtime construction/health is implemented, but process-start readiness alone must not be described as an eager scientific startup check. `PHYTO_STRUCTURE_DB_PATH` loads a validated curated CSV; unset in real mode leaves candidate retrieval unavailable (503), not a mock fallback. `data/structures/starter.csv` exists but is not proof of a production-rights-cleared database. The full-plane Dockerfile does not copy that file or install `google-cloud-aiplatform`; the local environment also lacks that SDK. Merely staging specialist source directories in an image does not expose their routes: the deployed app is the orchestrator app.

**Additional major limitation:** the current orchestrator constructs `MockToolExecutor` unconditionally. Its `_mock_rank_candidates` explicitly uses deterministic mock spectral ranking (with real taxonomy reranking when available), and mock LLM implementations are registered. A healthy orchestrator with `dry_run:false` reports auth configuration, **not evidence of real scientific execution**. Clearing global dry-run cannot by itself produce a genuinely wired scientific pipeline, and may activate real accounting/cloud dependencies. Treat the elucidation standalone startup gates and orchestrator tool wiring as separate acceptance surfaces.

Public exceptions are intentional: `/health`, documentation/OpenAPI paths, `/billing/plans`, signature-verified `/billing/webhook`, and signed `/share/` links. Do not demand 401 for those; verify webhook signatures and share expiry/isolation separately. Fail-closed unpriced routes return 503; insufficient balances return 402.

Frontend real mode requires all four Firebase public configuration fields. `AuthProvider.tsx` obtains a token on `onAuthStateChanged`, not a token-refresh subscription; prove expired-token recovery in staging. Its mock token `mock-id-token-mock-user-001` is not the backend mock verifier's `mock:<uid>` contract. ORCID remains a separately gated provider, not a launch prerequisite unless advertised.

## 5. Exact known environment variables and prerequisites

Do not populate credentials from this report; no secret values are required here.

| Variable | Known semantics / intended controlled staging setting |
|---|---|
| `PHYTO_ENV` | `cloud` for cloud client defaults; local defaults otherwise |
| `PHYTO_DRY_RUN` | `0` for real Firebase/cloud clients; `1` only for isolated, non-public mock plane |
| `PHYTO_AUTH_ENABLED` | explicitly `1` for real-user environments |
| `GOOGLE_CLOUD_PROJECT` | explicitly `phyto-companion-prod` for the observed project; use the actual separate staging project if provisioned |
| `FIREBASE_CREDENTIALS_PATH` | optional readable JSON file path; omit for correctly configured ADC |
| `PHYTO_FREE_TOKENS` | default `50`; confirm controlled test-account grants and budget |
| `PHYTO_CORS_ORIGINS` | comma-separated exact approved frontend origins |
| `PHYTO_APP_BASE_URL` | intended canonical application URL, documented `https://www.phytospectra.com`; align domains before using |
| `VERTEX_ENDPOINT_ID` | real approved deployed endpoint ID; **not known/verified** in this investigation |
| `VERTEX_LOCATION` | default `us-central1`; match the actual endpoint |
| `PHYTO_STRUCTURE_DB_PATH` | readable validated curated CSV path inside the serving container; production path/rights not established |
| `PHYTO_LLM_PROVIDER` | exact LLM-service selector; supports `vertex`, `openrouter`, `local`; `local` selects the mock client. Do not select a paid arm during auth checks. |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `OPENROUTER_SITE_URL` | OpenRouter-specific settings, if that separately approved arm is ever used; not verified or exercised here |
| `NEXT_PUBLIC_USE_MOCKS` | build-time string `false` for real frontend, not a post-build runtime fix |
| `NEXT_PUBLIC_API_BASE_URL` | build-time tested backend URL |
| `NEXT_PUBLIC_FIREBASE_API_KEY` | approved Firebase web-app public config, not an Admin private key |
| `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN` | approved Firebase auth domain |
| `NEXT_PUBLIC_FIREBASE_PROJECT_ID` | matching Firebase project ID |
| `NEXT_PUBLIC_FIREBASE_APP_ID` | matching registered web app ID |
| `SHARE_HMAC_KEY` | stable secret; preserve across rollout/rollback unless intentionally invalidating links |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | independently gated commerce prerequisites; never infer production readiness from placeholders |
| `STRIPE_PRICE_PRO`, `STRIPE_PRICE_LAB`, `STRIPE_PRICE_DEPARTMENT` | exact supported recurring price configuration; not verified |

Do not assume `.env.example` is loaded automatically. Supply configuration using the actual deployment mechanism. Do not copy its placeholders or create new service-account keys merely to satisfy a checklist.

## 6. Proposed rollout and verification (NOT executed)

1. **Establish ownership and access.** Name release/auth operators, confirm approved staging project/service/domain and Firebase provider owner. Read-only project access exists; deployment/write authorization, browser login, DNS ownership, billing/provider configuration and user acceptance remain unverified. If those are unavailable, stop rather than claiming launch readiness.
2. **Freeze rollback evidence.** Record the two revisions/digests above, traffic, complete configuration through a secure operator channel, IAM, secret references/versions (not contents), domain/provider allowlists and frontend build provenance. Do not rely on mutable `latest`. Confirm current revisions are security-acceptable rollback targets before any launch.
3. **Resolve source blockers first.** Review the 18 commits, get feature revision CI, fix/add benchmark lint coverage, reconcile the stale deployment guide, and decide whether this is an honestly labelled mock-science alpha or a real-science deployment. An env-only change cannot decouple auth and science with current code.
4. **Firebase preflight, without reactivation.** Confirm current email/password and Google settings, intended project/web app, exact authorized domains, OAuth consent/provider settings, recovery/abuse controls and workload identity. Do not click “Get started”, add users or reconfigure providers based on the old guide. Validate revocation permissions and least-privilege Firestore/Pub/Sub access in controlled staging.
5. **Prepare an immutable candidate revision with no public traffic.** Only after explicit deployment approval, use a reviewed image digest, separate staging service or a no-traffic tagged revision, and preserve existing settings with reviewed incremental updates. Do not paste the old guide's `--set-env-vars` command: it replaces settings and drops observed CORS. Do not use `--allow-unauthenticated` as a substitute for app auth; evaluate Cloud Run IAM and public health access separately.
6. **Build the frontend correctly.** Make the Docker build consume the four known Firebase public variables as well as mocks=false/API URL; currently the checked-in Dockerfile lacks those Firebase arguments. Verify built bundle behavior, not runtime env labels. Keep ORCID hidden until independently configured.
7. **Auth-only staging tests, no inference.** Use an approved existing test identity and protected free `GET /tools`; first confirm route pricing on the candidate revision. Check missing/invalid/expired/revoked/wrong-project tokens all yield 401, a valid matching token yields the expected non-error result, and identities/workspaces cannot cross-access each other. Never paste tokens into logs/docs. No `/analysis`, `/elucidate/candidates`, `/elucidate/novel`, `/elucidate/localize`, training notebook, billing checkout or workflow approval is needed for auth smoke checks.
8. **Browser and CORS verification.** From every supported origin, verify password login, Google popup/redirect, cancellation, sign-out, refresh/expiration and backend bearer propagation. OPTIONS must allow only approved origins and preserve CORS headers on 401/402/503. Verify unsupported origins fail. Perform this with browser access when available; it was not executed here.
9. **Scientific verification is a separate gate.** Inspect candidate specialist health for predictor identity, runtime mode and database availability. Prove missing endpoint/project fails startup, missing database yields explicit unavailability, and no mock fixture science can be described as real. Confirm the actual executor path reaches approved predictors. Any real model call requires separate budget/rights approval; none is authorized by this report.
10. **Controlled promotion only after all gates.** Verify audit events and ledger grant/reservation/refund/idempotency on isolated accounts, monitor 401/402/503 rates and latency, then stage traffic deliberately with named abort thresholds. No production success claim until end-user login and the chosen scientific capability are verified.

Non-mutating commands an authorized operator can repeat:

```sh
gcloud run services describe phyto-orchestrator --project=phyto-companion-prod --region=us-central1 --format='yaml(status.latestReadyRevisionName,status.traffic,status.url)'
gcloud run services describe phyto-frontend --project=phyto-companion-prod --region=us-central1 --format='yaml(status.latestReadyRevisionName,status.traffic,status.url)'
curl -i https://phyto-orchestrator-sbqloxr3xa-uc.a.run.app/health
curl -i https://phyto-orchestrator-sbqloxr3xa-uc.a.run.app/tools
curl -i -H 'Authorization: Bearer deliberately-invalid' https://phyto-orchestrator-sbqloxr3xa-uc.a.run.app/tools
```

The last two should be 401, not an invitation to try mock-user impersonation against production. Health must not be accepted as proof of real scientific collaborators.

## 7. Proposed rollback (NOT executed)

Abort on mock-token acceptance, disabled auth, wrong-project acceptance, missing CORS for supported origins, valid-user 5xx, ledger anomalies, unexpected paid calls, or unsupported scientific claims.

- Stop promotion and preserve evidence; rollback immutable traffic/revisions, not security settings. The recorded targets are historical serving revisions, **not newly certified secure builds**.
- Only if separately approved and those revisions remain appropriate, operator traffic commands are:

```sh
# MUTATING recovery commands — recorded only, NOT run in this investigation.
gcloud run services update-traffic phyto-orchestrator --project=phyto-companion-prod --region=us-central1 --to-revisions=phyto-orchestrator-00010-xbk=100
gcloud run services update-traffic phyto-frontend --project=phyto-companion-prod --region=us-central1 --to-revisions=phyto-frontend-00005-6qh=100
```

- **Do not “rollback” by setting `PHYTO_DRY_RUN=1` or `PHYTO_AUTH_ENABLED=0` on a public service.** That changes the security boundary. Do not disable the existing Firebase providers or revoke all users to repair a frontend release.
- Frontend's historical runtime setting is mocks=true; if its built bundle is unsafe/incompatible, use a reviewed maintenance/deny-access route instead of blindly restoring a mock frontend.
- Traffic rollback does not undo Firebase config, IAM, Firestore writes, grants, audit events, DNS or secret rotations. Reconcile those explicitly; never delete or overwrite ledgers as a generic rollback. Preserve share HMAC and approved secret versions unless responding to compromise.
- Repeat /health, absent/invalid-token 401 checks, approved staging valid-user checks and browser/CORS tests after rollback. Confirm traffic points to intended revision and monitor audit/accounting. If there is no known-safe revision, restrict access/serve maintenance rather than bypass authentication.

## 8. Benchmark and fine-tuning readiness

**Harness ready for regression use; scientific benchmark/model comparison not ready.**

- Four actual packets C001–C004; all reviewer fields are null. C005/C006 are protocol placeholders but not present as actual case files. Expert adjudication of H-3 absence, B-ring pattern and C004 abstention remains mandatory.
- Current cases are NMR-only curated HSQC smoke tests. Protocol says scientific benchmark denominator is zero until 20–30 cases are assembled/reviewed. No accuracy claim follows from 15 passing software checks or fixture-context tests.
- Obtain paired NMR/MS from the same documented sample, MS-only/NMR-only/paired arms, near-isomers, insufficient-evidence cases, source/family-held-out splits, locked prompts/model versions/seeds/tolerances and explicit missing/duplicate response accounting. Citation existence is not expert support for a conclusion.
- The context tests validate historical committed SUMMIT fixture statistics (including candidate-pool difficulty); they do not rerun a predictor or certify accuracy of the current deployed product.
- Per-record rights/external-inference permissions are unresolved for some sources. CC-BY-NC data is not commercial product training permission; private case archives stay local. NMRGym/NMRArena access cannot be assumed; expert and data-provider access were not established here.
- `.venv` has RDKit, firebase-admin and Firestore; does **not** have `google.cloud.aiplatform`, `torch`, `transformers`, or `peft` (module-presence checks only). GPU capacity, weights, gated datasets, paid provider access and Vertex endpoint readiness were not established. No install/download was attempted.
- `FINETUNE-DECISION-GATE.md` keeps training closed until retrieval-complete cases show repeated evidence-interpretation errors that tuning could plausibly fix. Retrieval, packaging or parser failures are not justification to train.
- The notebook is a template, uses remote model loading, and must not be executed as a test. Require separate rights-cleared training data, leakage checks, reviewed model terms, approved compute/budget, pinned dependencies and memory/checkpoint/restart tests before any GPU run. No training or paid arm ran here.

## Final release checklist

- [ ] Fresh selected-revision CI and reviewed PR; intentional protection/release policy.
- [ ] Broader benchmark lint issues resolved or explicitly scoped and disclosed.
- [ ] Citation placeholders and actual release version synchronized, without invented DOI.
- [ ] Frontend build-time Firebase configuration and real login verified.
- [ ] Authorized-domain/CORS/canonical-host alignment verified.
- [ ] Real auth isolated from mock-science claims; executor/predictor wiring proven.
- [ ] Least-privilege identity, ledger/audit, secrets and known-safe rollback acceptance.
- [ ] Expert-reviewed rights-cleared benchmark expanded; no unsupported accuracy/training claims.

Until these gates are satisfied, keep any release **draft alpha/prerelease**, not stable or production-ready.

## 10. Current source-only orchestrator update — 2026-11-08

This note is based on inspection of the current checkout, not a deployment or a new validation result. Configuration and behavior remain subject to final integration on `main`. The exact request, schema, library, provenance, health, audit, and checkpoint contract is in [ORCHESTRATOR_EXECUTION.md](ORCHESTRATOR_EXECUTION.md). No status/ADR document is updated by this note, and no commit, push, deploy, paid inference, download, or training is claimed.

### What changed in the inspected source

- Scientific mode is selected by `PHYTO_SCIENTIFIC_EXECUTION=mock|real`, independently of authentication `PHYTO_DRY_RUN`. The mode is explicitly required in every environment; missing or invalid configuration fails closed locally and in cloud. Explicit cloud mock also requires auth dry-run.
- Real mode requires `PHYTO_STRUCTURE_DB_PATH` and `PHYTO_HSQC_REFERENCE_LIBRARY`, with local CSV and JSON files loaded once at startup. Missing or malformed configuration fails rather than invoking mock science.
- The real executor supports only `reference_hsqc_identify` and `reference_hsqc.rank`. It uses local `FileStructureDB` candidate retrieval and `match_hsqc` against supplied references. It does not invoke a mock predictor or LLM and does not make the other workflows, registry tools, MS providers, or LLM providers real.
- The plan is one ranking task followed by a mandatory `before_confirmed_claim` checkpoint. Tool output is schema-validated and hash-audited, the API returns ranked results with `awaiting_review`, and checkpoint approval records a human decision without recomputing or scientifically validating the result.
- Reference `candidate_id` is the normalized structure name, not an arbitrary name or InChIKey. Reference JSON requires source, solvent, explicit license, peaks, and `provenance:"experimental"`; the last is curator-asserted, not independently verified. Public reference provenance and reuse rights remain release responsibilities.
- Files are limited to 8 MiB each, structures to 1000 rows, references to 256 records with 256 peaks each and 8192 total, and real observed peaks to 256. Execution is synchronous and offline with no provider network timeout, retry, or nested ledger. Bounds are not wall-clock deadlines.
- Health distinguishes process/configuration/dependency availability from science. Both modes report `scientific_ready:false`; neither a healthy process nor a `dry_run:false` request establishes scientific readiness.

This supersedes only the source-level mock-only description in the earlier orchestrator discussion above. It does not supersede the observed live-service evidence: no claim is made that this source or these health fields are deployed.

### Acceptance and release position

The designated local acceptance command is:

```sh
make stage-auth
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=shared:build/auth-path:services:. \
  .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_real_orchestrator.py -q
```

Its expected state is **pending** until run on the integrated source; no result or pass count is asserted here. Tests inject offline authentication and temporary local libraries, so even a future pass would not establish cloud auth, deployment, dataset licensing, or scientific validity. No paid provider is needed by this real executor.

The initial real `awaiting_review` path does not mint a citation. **[Update 2026-11-09: verified on the current branch — real-mode checkpoint approval now returns HTTP 200 with `citation: null` (clean citation-free completion; asserted in `tests/test_real_orchestrator.py`), and `GET /runs/{id}/citation` returns the explicit 503 message below.]** Existing mock runs can mint placeholder `10.0000/phyto...` identifiers; they are not registered DOIs. Real local reference retrieval is a bounded capability, not validated identification, not general real-tool execution, and not deployment readiness.

The release decision remains **NO-GO for public production/scientific release** and draft prerelease preparation only. Real local reference retrieval is a bounded capability, not validated identification, not general real-tool execution, and not deployment readiness.
