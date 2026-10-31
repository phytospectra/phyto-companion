# Agent Status

Reporting template per the enterprise handoff brief. One row per milestone.

## Milestone: Phase 0 evidence baseline + Phase 1A fail-closed runtime

- **Milestone/status**: Phase 0 + Phase 1A — **complete, tests passing, pending commit**.
- **Base SHA → result SHA**: base `bff329a` (main); work on local uncommitted tree — will be committed to an isolated work branch (this is the only agent session working in this checkout).
- **Implemented behavior**:
  - Phase 0: verified repo state (`bff329a`, branch `main`), confirmed Phase 1A defect sites, inventoried LLM provider paths (finding 5 confirmed: provider-backed completions exist beyond the QA critic in `services/llm/` — Vertex + OpenRouter clients and a `/complete` route).
  - Phase 1A (ADR-0078): new `services/elucidation/app/runtime_config.py` with `ElucidationConfig.from_env().validate()`; real mode (`PHYTO_ENV=cloud`, `PHYTO_DRY_RUN` unset/0) refuses startup when `VERTEX_ENDPOINT_ID` or `GOOGLE_CLOUD_PROJECT` is missing; `_Runtime` builds `VertexAIShiftPredictor` from the validated config (never re-deriving dry-run from env) and leaves the structure-DB slot empty in real mode — access raises `CloudServiceError` → HTTP 503 instead of silently serving the 9-molecule fixture; `get_shift_predictor()` factory is fail-closed in real mode (missing config or missing `google-cloud-aiplatform` raises, never returns mock); `/health` now reports `predictor_mode`, `structure_db_available`, `db_size`, `dry_run`. `.env.example` documents the requirement. ADR-0078 appended to `docs/DECISIONS.md` + `docs/adr/INDEX.md` (numbering check OK).
  - Phase 0A: `NMR_RESEARCH_REGISTER.md` created — every load-bearing claim from `LLM_SOTA_NMR_elucidation.md` marked verified / disputed / unresolved with the required columns. Verified live: ChemLLM-7B-Chat Apache-2.0 (InternLM2, card adds commercial-licensing wording), CHEMLLM-2b Apache-2.0, ChemDFM AGPL-3.0 (blocker confirmed), LlaSMol CC BY 4.0, SMolInstruct CC BY 4.0 (3M+), NMRTrans-Data MIT, NMRGym MIT + email-gated (269,999 mols), nmrexp peaklist **license undeclared confirmed** (3.7M rows), rose-1h-nmr Apache-2.0 1D encoder (7.8M params, 3.2M spectra), arXiv 2607.19406 (71% top-1 vs 66% grad students confirmed), arXiv 2606.29776 NMRAgent. Unresolved: NMRArena returns HTTP 401 unauthenticated (license unreadable). Disputed: "only the QA judge calls an LLM". Exclusivity claims ("only 2D-NMR instruction data", "no open-weight NMR elucidation LLM exists") marked unverified — excluded from external claims.
  - Coordination artifacts created: `CAPABILITY_REGISTER.md` (v1, 18 capabilities + LLM inventory), `IMPLEMENTATION_BACKLOG.md` (v1), this file.
- **Evidence**:
  - `tests/test_elucidation/test_runtime_modes.py` → 11 passed (config defaults, validation refusals citing the missing var names, real-runtime fail-closed `.predict()`, health honesty incl. 503 body).
  - `tests/test_elucidation/test_shift_predictor.py` → 16 passed (factory: dry-run forces mock; local defaults mock; cloud real + no config raises CloudServiceError naming `endpoint_id`; cloud real + no SDK raises "not installed"; explicit cloud bring-up dry-run = mock).
  - Full `tests/test_elucidation` → 156 passed.
  - Full suite + ruff: run launched (result appended below when confirmed).
  - `scripts/check_adr_numbers.py` → "ADR numbering check: OK".
  - One design bug caught by my own test and fixed before commit: `_Runtime` initially delegated to the env-reading factory, so an explicitly-passed real-mode config silently resolved to the mock under `PHYTO_ENV=local` test pinning. Fix: construct `VertexAIShiftPredictor` directly from the validated config.
- **Scientific validation**: no expected scientific numbers changed; all §13 case-study regression expectations untouched. Fixture library remains labeled and excluded from scientific claims.
- **NMR work**: Phase 0A register (model/data/benchmark/license verification) feeds Phase 4B data-tier gating: NMRGym email request + NMRArena access request are the zero-cost first actions; jablonkagroup/* and SpectrumWorld/* stay off-limits until license declared.
- **Security/deployment impact**: positive — closes brief finding 1 (silent mock fallback in production). Real deployments now fail fast with actionable config names; unconfigured capabilities answer 503 with an explicit "unavailable" message; health exposes the actual mode. No secrets touched, no keys logged, no external calls made.
- **Remaining blockers**: NMRArena license unverifiable without authenticated access; NMRGym gated (email request pending user authorization — contacting authors requires authorization per brief); deployed-mode verification (finding 6) needs live environment access; findings 2–4, 7, 8 not yet addressed (backlog items 1–3).
- **Next bounded task**: backlog **[P1B]** — audit HMBC regression labels vs coordinates (finding 4) and the Word-report COSY/HMBC disagreement (finding 3), documentation only, no expectation changes.

## Milestone: Phase 1B — §13 HMBC coordinate reconciliation (ADR-0079) + findings 2/3 verified

- **Milestone/status**: P1B — **complete, 18/18 regression tests pass**.
- **Base SHA → result SHA**: `efeccde` → (this commit, branch `agent/phase0a-phase1a-failclosed-runtime`).
- **Implemented behavior**:
  - Measured every HMBC label's cross-peak against the real fixture under the §4 reference S/N implementation. Brief finding 4 confirmed: `H-11a→C-4a` was pinned at (5.47, 66.5) duplicating `H-11a→C-6`'s peak; 66.5 is C-6, the only 13C signal there. The real C-4a cross-peaks sit at 157.2: H-11a→C-4a S/N 13.9 (2.3× control), H-6eq→C-4a S/N 32.2 (6.8× control) — reconciling §13's "verified ≈27" that the old pin mis-filed as a divergence at 3.7.
  - Corrected the two rows in `test_case_study_regression.py` with measured baselines (ADR-0079; not expectation-gaming: every new number is a measurement, and §13's own claimed S/N values reconcile within its ±30% tolerance). Suite: 18/18 pass. ADR-0079 appended, index updated, numbering check OK.
  - Finding 3 verified: the two Word reports' COSY/HMBC contradiction is a timeline artefact — the 2026-10-24 `FullCapabilities` report read the COSY flat (pre-XDIM-fix, "artifact-dominated reconstruction"); the 2026-10-25/26 report re-verified all six COSY + 15 HMBC correlations after the reader fix. Older report needs a superseded banner (P1D).
  - Finding 2 verified: `phytospectra-paper/PHYTOSPECTRA-MANUSCRIPT-DRAFT1.md` (~line 94) still says the FIDs show "corrupted quadrature … no FID decay"; ADR-0075 proved the FIDs healthy (decay ratios 101.9/580.7/23.1/162.9) and the workspace README already carries the strikethrough correction. Manuscript rewrite tracked as P1D.
  - Full reconciliation note: `docs/CASE_STUDY_COORDINATE_RECONCILIATION.md`.
- **Evidence**: full F1-column scans (H-11a, H-6eq, H-1, H-7, OCH2O, H-4) with S/N at every §13 coordinate; `test_case_study_regression.py` 18 passed; `scripts/check_adr_numbers.py` OK; Word reports extracted and compared (431 vs 647 lines; docx mtimes 10-24 vs 10-25/26).
- **Scientific validation**: no §13 expectation was changed to make a test pass — the two corrected pins are measurements at the physically correct carbon, and §13's own claimed S/N values reconcile within its stated tolerance. The remaining divergence set is unchanged.
- **Security/deployment impact**: none (test- and docs-only).
- **Remaining blockers**: P1C (LLM endpoint provenance tagging), P1D (stale metadata sweep: Word-report banner, manuscript FID paragraph, README/release metadata); NMRArena license and NMRGym contact still gated on user authorization.
- **Next bounded task**: **P1C** — test asserting every provider-backed LLM endpoint records model id + version in response provenance.

## Milestone: Phase 1C — LLM judge model-identity provenance (ADR-0080)

- **Milestone/status**: P1C — **complete, 1527 passed, ruff clean**.
- **Base SHA → result SHA**: `758b5e5` → (this commit, branch `agent/phase0a-phase1a-failclosed-runtime`).
- **Implemented behavior**: The QA critic's `llm_judge` verdicts now carry the judge's model identity. `LLMClient` protocol gains a non-secret `model_identity` property (`mock-llm-client:deterministic` / `vertex-endpoint:<id>`); `CheckResult` gains an optional `model_identity` field (`None` for rule-based checks; serialized only when set); `_llm_judge_check` stamps it from the client, with an honest `"unknown-judge"` `getattr` fallback. This closes the gap in brief finding 5 for the one path that wasn't already tagging: `services/llm` `/complete` already records `model_id`+`model_version` into the provenance manifest (verified in `clients.py`/`openrouter_client.py`, incl. the served-model id from the response body). ADR-0080 documents it; index updated, numbering check OK.
- **Evidence**: `tests/test_orchestrator.py` 68 passed (4 new: judge verdict carries model identity; rule-based checks carry none; to_dict() serialization; mock identity is not a credential). Full suite: **1527 passed** (1524 + 3 net new), ruff clean. `scripts/check_adr_numbers.py` OK.
- **Scientific validation**: no scientific expectations touched; judge text/verdict logic unchanged.
- **Security/deployment impact**: positive — every judge verdict is now attributable to a model (endpoint id or model slug, never a key), enabling Phase 4C comparison and fine-tune replacement audits.
- **Remaining blockers**: P1D (stale metadata sweep: Word-report superseded banner, manuscript FID paragraph, README/release metadata); Phase 2 provider wiring (structure DB).
- **Next bounded task**: **P1D** — stale metadata sweep (finding 8): dated superseded banner on the October 24 Word report, manuscript FID paragraph rewrite tracked, README/release metadata vs. repo state.

## Milestone: Phase 1D — stale metadata sweep (findings 2, 3, 8)

- **Milestone/status**: P1D — **complete** (manuscript + README + banner; see below for what was deliberately NOT changed).
- **Base SHA → result SHA**: `0fa15b4` → `e7d1d9d` (README, branch `agent/phase0a-phase1a-failclosed-runtime`).
- **Implemented behavior**:
  - **Finding 2 (manuscript FID wording)**: rewrote `phytospectra-paper/PHYTOSPECTRA-MANUSCRIPT-DRAFT1.md` data-basis paragraph — the "corrupted quadrature" diagnosis is now presented as what it was (a named skip that turned out to be an int32 misread of DTYPA=2 float64 FIDs), with the ADR-0075 audit date, the healthy decay ratios (101.9/580.7/23.1/162.9), and an explicit statement that the corrected reading supersedes any earlier text calling the FIDs corrupt. Also fixed the "Data-quality guards" table row to state the decay check is read under the declared dtype.
  - **Finding 3 (Word-report disagreement)**: added `CaseStudy_Photospectra/READ_ME_FIRST_superseded_report.txt` — a dated banner marking the 2026-10-24 `Phytospectra_NMR_Report_FullCapabilities.docx` (and its copies) as superseded, naming the stale 2D verdicts (pre-XDIM-fix flat read) and the stale FID claim, and pointing to the post-fix 2026-10-25/26 report and `docs/CASE_STUDY_COORDINATE_RECONCILIATION.md` §2. The docx itself was not edited (binary, owner decision).
  - **Finding 8 (stale metadata)**: README's Vertex env row now states the ADR-0078 fail-closed requirement (missing config refuses startup). Checked the rest: README architecture, layout, provider table, and the frontend-repo pointer all verified current (frontend repo exists and was updated 2026-10-26); manuscript's "Archived version: [TO ADD — Zenodo DOI upon release]" stays as-is because no release exists — creating one requires owner authorization (Phase 5).
- **Evidence**: manuscript line 94 rewrite (grep shows no remaining uncorrected "corrupt" claims); banner file in `CaseStudy_Photospectra/`; README commit `e7d1d9d`; `gh repo view phytospectra/phyto-companion-frontend` → updated 2026-10-26.
- **Scientific validation**: none required (metadata/documentation only; no numbers changed).
- **Security/deployment impact**: none (docs); the banner prevents sharing a stale report that understates the platform's 2D capability.
- **Remaining blockers**: Phase 2 (structure-DB provider wiring), Phase 3 (deployed-mode verification), Phase 4 (agent modes).
- **Next bounded task**: Phase 2 — wire a real structure-DB provider behind `_Runtime.db` (currently 503 in real mode) with a contract test.

## Milestone: Phase 2 — FileStructureDB provider wired (ADR-0081)

- **Milestone/status**: Phase 2 Step 6+ — **complete, 1539 passed, ruff clean**.
- **Base SHA → result SHA**: `e7d1d9d` → `ae96706` (branch `agent/phase0a-phase1a-failclosed-runtime`).
- **Implemented behavior**: `FileStructureDB` — a real curated structure library loaded from CSV (`name`, `smiles`, optional `provenance`/`source`), wired via `PHYTO_STRUCTURE_DB_PATH` on either plane. Loading is fail-closed: missing file, missing columns, invalid SMILES, duplicate InChIKey, or empty file is a startup `CloudServiceError` — never a partially-loaded library, which would quietly miss candidates and read as a negative result. When set, the real library serves candidate retrieval in both planes; when unset, behavior is unchanged (mock in dry-run, 503 in real mode per ADR-0078). `.env.example` documents the variable. ADR-0081 appended; numbering check OK.
- **Evidence**: 12 new tests in `tests/test_elucidation/test_structure_db_provider.py` (loading, formula/InChIKey indexing, provenance/source defaults, every failure mode, runtime wiring in both planes); elucidation suite 168 passed; full suite 1539 passed; ruff clean. One real behavior caught during authoring: the test's first maackiain SMILES string was invalid — the fail-closed loader caught it at startup, which is exactly the designed behavior.
- **Scientific validation**: none required (infrastructure); the mock library stays labeled and excluded from scientific claims; the FileStructureDB library's provenance column is recorded per candidate.
- **Security/deployment impact**: positive — candidate retrieval is now a real, configurable capability without credentials or network; a bad library file refuses startup with an actionable message.
- **Remaining blockers**: Phase 3 enterprise security/ops.
- **Next bounded task**: ~~Phase 2 vertical~~ → complete; see next milestone.

## Milestone: Phase 2 vertical — one end-to-end API run on a real library

- **Milestone/status**: Phase 2 production vertical — **complete, 1541 passed, ruff clean, ADR numbering OK**.
- **Base SHA → result SHA**: `ae96706` → `b0edccb` (branch `agent/phase0a-phase1a-failclosed-runtime`).
- **Implemented behavior**: `TestPhase2Vertical` in `tests/test_elucidation/test_elucidation_main.py` — a 2-molecule curated CSV library (phenol + catechol, provenance `experimental`, source `curated-vertical-test`) is wired via `PHYTO_STRUCTURE_DB_PATH` and traversed through three touchpoints in one run: `/health` reports `structure_db_available` and `db_size=2`; `/elucidate/candidates` returns phenol at rank 1 with the curated provenance/source traveling on the candidate; `/elucidate/novel` (catechol + one OH → C6H6O3 hydroxyquinol, not in the library) returns a `predicted`-provenance candidate that survived the known-InChIKey filter. The missing-library startup path asserts `CloudServiceError` specifically.
- **Evidence**: full suite **1541 passed**, ruff clean, `check_adr_numbers.py` OK.
- **Scientific validation**: none — this is the wiring vertical, run on the local plane with a labeled fixture library, excluded from scientific claims per the brief.
- **Security/deployment impact**: proves the Phase 2 capability end to end with no credentials and no silent mock fallback; the only remaining local-plane mock is the shift predictor, which is the documented dry-run behavior.
- **Remaining blockers**: Phase 3 (verify deployed auth is real — finding 6; Stripe webhook contract; frontend evidence-field contract — finding 7); Phase 4.
- **Next bounded task**: Phase 3 — auth/ops evidence collection and contract tests.

## Milestone: Phase 3 — auth contract tests (finding 6) + FE evidence-field contract (finding 7)

- **Milestone/status**: Phase 3 first slice — **complete** (finding 6 finding-7 addressed; deployed-mode live verification still pending user/environment).
- **Base SHA → result SHA**: `4c7786c` → `672874e` (backend, branch `agent/phase0a-phase1a-failclosed-runtime`); frontend `45b73de` → `d7d7a49` (frontend repo, `main`).
- **Implemented behavior**:
  - **Finding 6 (401 contract)**: every authed service route must return 401 for unauthenticated callers. `tests/test_auth/test_service_health.py` extended (+85/−14) with contract tests for the 7 services that lacked them, plus a structural fail-closed guard: a test that introspects registered routers and fails if any future authed router ships without the guard. Frontend contract mirror (finding 7): backend `models.py:482` carries `msi_level` (MSI confidence level) on `CandidateRank`; mirrored into `src/lib/phyto/types.ts`, `src/lib/phyto/fixtures.ts`, `src/__fixtures__/rich_identify_response.json`, and pinned in `tests/unit/contract-type-fixes.test.tsx` so the FE cannot silently drop the evidence field.
- **Evidence**:
  - `tests/test_auth/test_service_health.py` → **33 passed**.
  - Full backend suite: **1556 passed**, 14 warnings (pre-existing class-scoped-fixture deprecations), 72.1s; `ruff check .` → All checks passed.
  - Frontend: `tsc --noEmit` clean; `scripts/check_fe_contract.py` → OK (allowlisted pass-2 gaps excluded, pinned shapes intact).
- **Environment limitation (frontend vitest suite)**: `npx vitest run` (vitest 4, Node v26.11.1) fails at worker startup in this environment — pool errors `[vitest-pool-runner]: Timeout waiting for worker to respond` / `Failed to start forks worker for test files ...` (10 errors, 0 tests executed, 120s). Recorded in `HANDOFF_repo_health.md` as an environment limitation, not a code defect: the worker-spawn path times out under this sandbox (forks pool, Node 26.11.1). Verification for this slice therefore relied on `tsc --noEmit` + the FE contract checker + backend tests; the vitest unit suite (incl. the new `msi_level` pin in `contract-type-fixes.test.tsx`) should be run on a machine with a working worker-spawn path before merge to `main`.
- **Scientific validation**: none required — auth contract tests and type mirroring only; no scientific expectations touched.
- **Security/deployment impact**: positive — closes the 401 coverage gap (finding 6) for 7 services with a structural guard against regressions; `msi_level` now travels to the FE so MSI confidence is not silently dropped at the contract boundary.
- **Remaining blockers**: deployed-mode auth verification (needs live environment access); Stripe webhook contract test; frontend vitest run (environment limitation above); Phases 4–5.
- **Next bounded task**: Phase 3 remainder — Stripe webhook signature contract test, then deployed-mode verification setup.

## Test/lint addendum

- Full suite: `pytest -q` → **1556 passed**, 14 warnings, 72.1s (2026-10-31, Python 3.12, Phase 3 auth contracts included). Warnings are pre-existing class-scoped-fixture deprecations, not failures.
- Lint: `ruff check .` → **All checks passed!**
- Frontend: `tsc --noEmit` clean; `scripts/check_fe_contract.py` OK; vitest suite not runnable in this environment (see Phase 3 milestone, environment limitation).

## Milestone: Reconciliation + M0 honest coverage (ADR-0082/0083/0084)

- **Milestone/status**: Enterprise-brief reconciliation + M0 of the
  multi-vendor LC–MS prompt — **complete on the agent branch, 1593 passed,
  ruff clean, ADR numbering OK**. NOT a validated LC–MS processing slice: see
  the honest-boundary note below.
- **Base SHA → result SHA**: backend `c2558a8` → (this commit, branch
  `agent/phase0a-phase1a-failclosed-runtime`); frontend unchanged at `d7d7a49`
  (verified HEAD, clean).
- **Claim verification against code (requested)**: ADR-0079's corrected pins
  confirmed present in `test_case_study_regression.py` (157.2 rows, duplicate
  66.5 row gone); manuscript FID paragraph (line 94) + superseded-report
  banner + DOI-placeholder decision verified on disk; the 1556-test claim
  reproduced before this work began (1556 passed).
- **Implemented behavior**:
  - **ADR-0082 (honest provenance defaults)**: mock-library candidates now
    `synthetic` (were `experimental`); undeclared `FileStructureDB` rows
    default `structure_only` (were `experimental`); declared values preserved.
    Score-neutral (no ranker reads provenance).
  - **ADR-0083 (fail-closed startup validation extended)**: new
    `runtime_config.py` in metabolomics/networking/pharmacology mirroring
    `ElucidationConfig`; `_Runtime` built from validated config; `create_app`
    validates eagerly (container entrypoint fails at import, not first
    request). Real-mode requirements: `MZMINE_HOME`; GNPS route
    (`GNPS_API_KEY` or explicit `PHYTO_ALLOW_PUBLIC_GNPS=1`); `NEO4J_URI`
    (pharmacology real mode additionally explicitly unavailable until the
    Step-8 Neo4j backend — honest unavailability, not a mock). `/health`
    reports `detector_mode`/`gnps_mode`/`kg_mode`.
  - **ADR-0084 (MZmine contract unverified)**: `MZmineDetector.detect()` now
    refuses to run with a named error ("integration is unverified") after
    validating inputs exist; the dead CSV "batch" code path is removed; the
    block persists until a live test validates the XML batch format, version
    and export schema against a real installation.
  - **M0 deliverables**: `ANALYTICAL_CAPABILITY_MATRIX.md` (25 feature rows vs
    reference families), `FORMAT_SUPPORT_MATRIX.md` (LC–MS + NMR), 
    `DATABASE_CONNECTOR_REGISTRY.md` + machine-readable
    `data/connector_registry.json` (36 connectors, rights-status honesty),
    `docs/M0_RUNTIME_TRACE_AND_SCHEMAS.md` (runtime trace + 5 canonical schema
    proposals), rewritten `CAPABILITY_REGISTER.md` v2 (evidence-tier columns),
    rewritten `IMPLEMENTATION_BACKLOG.md` v2 (M0/M1 priorities).
- **Evidence**: new tests —
  `test_metabolomics/test_detector_runtime_modes.py` (8),
  `test_networking/test_gnps_runtime_modes.py` (13),
  `test_pharmacology/test_kg_runtime_modes.py` (11), factory/runtime updates in
  `test_feature_detection.py` (+3), `test_structure_db_provider.py` (reworked
  provenance tests), updated GNPS factory tests to the explicit-config
  contract. Full suite **1593 passed**, 14 warnings (pre-existing), ruff
  `All checks passed!`, `check_adr_numbers.py` OK.
- **Local tests vs live integration vs scientific validation vs deployment**:
  - Local tests: done (above).
  - Live integration: none performed — no MZmine binary, no real mzML fixture,
    no live GNPS/Vertex/Neo4j call; every real-mode path remains unexercised.
  - Scientific validation: unchanged; §13 suite passes; the blocked MZmine
    path claims nothing; fixture outputs stay excluded from scientific claims.
  - Deployment: none; no push/PR/deploy was performed. Publication of the
    branch requires explicit authorization per the brief.
- **Scientific validation**: no expected scientific number changed anywhere in
  this milestone; all new numbers are startup-failure contracts, not
  measurements. §13 case-study regressions untouched.
- **Security/deployment impact**: positive — closes the silent-mock hole in
  three more services (a cloud deployment without MZmine/GNPS/Neo4j config now
  refuses startup with an actionable name instead of serving fabricated
  science); eager validation means the failure is at import time. No secrets
  touched; registry lists rights-status honestly (`unknown` ⇒ no bulk
  ingestion/training).
- **Remaining blockers**: M1-a needs a real MZmine installation + rights-verified
  open mzML fixture (M0-b shortlist) — both external to this environment;
  deployed-mode auth verification needs live environment access; frontend
  vitest run needs a working worker-spawn machine; Phase 4 agent work.
- **Next bounded task**: **[M0-b]** verify rights and pick the first open
  reference mzML dataset (accession + hash recorded, nothing unpublished);
  then **[M1-a]** the MZmine live-integration contract when a binary is
  available.

## Milestone: M0-b dataset selection + M1-a MZmine batch contract (ADR-0085)

- **Milestone/status**: M0-b **complete (dataset verified, recorded)**; M1-a
  **implemented + unit-tested, live feature run BLOCKED on MZmine user
  profile** (see below — the run has not happened; do not cite a feature
  table).
- **Base SHA → result SHA**: base `d971478` (branch
  `agent/phase0a-phase1a-failclosed-runtime`); work currently uncommitted in
  the working tree.
- **Implemented behavior**:
  - **M0-b dataset**: MassIVE `MSV000090047`
    (`20220426_UMETAB266_POS_Tetracycline.mzML`, 33,379,150 bytes), downloaded
    to `data/raw/MSV000090047/` (gitignored; not redistributed), MD5
    `396f11793f20870de150b56408a33c21` matches the Zenodo record
    (10.5281/zenodo.6948449), local SHA-256
    `fa3910452ae96f754a3eb05b2cf07ae2993f053fa4862b78d365cf1d857f6b9e`.
    Rights: MassIVE page states CC0 1.0, Zenodo mirror records CC-BY-4.0 —
    divergence recorded, CC-BY attribution treated as the stricter term
    (`docs/DATA_SOURCES.md` §11). Independent anchors: instrument/scan
    metadata inside the file (Orbitrap ID-X, 4,988 scans = 782 MS1 + 4,206
    MS2, RT 0.708–11.002 min), MassBank `MSBNK-BAFG-CSL23111012700`
    (tetracycline [M+H]+ m/z 445.1605, dl-de/by-2-0 attribution), formula
    arithmetic on the file's own MS2 fragments.
  - **M1-a batch contract**: new `services/metabolomics/app/batch_xml.py`
    generates a version-pinned 11-step XML batch (import → MS1/MS2 mass
    detection → ADAP chromatogram building → smoothing → local-minimum
    resolver → isotope grouper → join aligner → gap filling → duplicate
    filter → modular CSV export), authored from the official v4.11 CI
    integration-test batch and cross-checked against `BatchQueue.java`;
    module/parameter versions verified at tags v4.11, v4.10.90, v4.10.6;
    any other version is refused. `DetectParams` values (m/z tolerance, RT
    tolerance, min intensity, noise threshold) now map into the relevant
    batch steps.
  - **Adapter**: `parse_mzmine_csv` maps the CSVExportModular schema
    (`id/mz/rt(minutes)/...` + `datafile:<name>:<quantity>` columns) to the
    platform `FeatureTable` (RT minutes → seconds), fail-closed on missing
    columns/empty export. `MZmineDetector` resolves the executable (macOS
    `.app` or `bin/` layouts), validates inputs, then runs
    `mzmine -user <profile> -batch <xml>` with NO `-input`/`-output`
    overrides — the resolved input paths and the exact CSV export path are
    written into the batch XML itself (MZmine's documented `-output` renames
    every export file with module suffixes, which would make the parser's
    expected path a guess). Provenance records engine version + batch
    SHA-256. `runtime_config` gained `MZMINE_USER_FILE`; the login gate is
    fail-closed at detect time.
  - **Login gate (measured)**: MZmine 4.11.0, 4.10.90 and 4.10.6 ALL abort
    batch mode without an authenticated user ("Requires user login" /
    "Invalid user: You need to be logged in to continue"; gate in source
    since v4.0.x). Options are `-cli-token`, `-login` (interactive browser),
    `-user <file>`. No account was created and no credentials supplied —
    creating one requires explicit user authorization.
- **Evidence**:
  - Focused suites: `test_mzmine_batch_contract.py` +
    `test_feature_detection.py` + `test_detector_runtime_modes.py` +
    `test_mzmine_live_integration.py` → **39 passed, 4 skipped** (skips =
    live tests without `MZMINE_USER_FILE`).
  - Partial-run probe: MZmine 4.10.90 accepted a generated batch and parsed
    all 4,988 scans of the dataset, then stopped at the login gate. This
    validates batch-XML acceptance and mzML import ONLY — mass detection,
    feature resolution, alignment and CSV export were never executed.
  - Not yet run: full pytest, ruff, the live run itself.
- **Local tests vs live integration vs scientific validation vs deployment**:
  - Local tests: contract + adapter + fail-closed tests pass (above).
  - Live integration: **incomplete** — engine executes only up to the login
    gate; no feature table has ever been produced by this integration.
    The live test module (`test_mzmine_live_integration.py`) is correctly
    skipped, not faked.
  - Scientific validation: none for M1-a — no feature table, no comparison
    against the m/z 445.1605 / RT anchors. Nothing scientific may be
    claimed.
  - Deployment: none; nothing pushed, no PR, no external calls beyond the
    dataset download (public, documented) and MZmine CLI help/version
    probes.
- **Remaining blockers**: the MZmine `.mzuser4` profile — a human action
  (browser sign-in to an mzio account, or an mzio console token) that the
  agent cannot and must not perform for the user. Second-order: once the
  profile exists, verify the first real export's column naming against
  `parse_mzmine_csv` before any downstream use.
- **Next bounded task**: with `MZMINE_USER_FILE` set, run
  `scripts/replay_mzmine_m0b.sh` (pytest entry → validated config →
  `MZmineDetector.detect` on the verified dataset), compare the resulting
  feature table against the three independent anchors, then record the
  measured comparison in `ANALYTICAL_CAPABILITY_MATRIX.md` and this file.
  Vendor-native conversion (.raw/.d) remains the separate M1-b acceptance
  test.

## Milestone: M1-a detector replay PASSED (measured) — record + HTTP verification

- **Milestone/status**: **M1-a replay passed** — authenticated live run
  produced a real feature table and the tetracycline acceptance anchors
  were met; recorded in ADR-0086. M1-b (vendor-native conversion) is the
  next acceptance test and has not started.
- **Base SHA → result SHA**: base `1bd5dfb`; result pending commit.
- **Measured result (2026-10-31)**:
  - Engine: MZmine 4.11.0 (pinned), macOS ARM, user profile
    `subhadip.ban@mfu.ac.th.mzuser4` (user-created; machine-locked; no
    credentials recorded in the repo).
  - Dataset: MSV000090047 tetracycline mzML, MD5/SHA-256 re-verified at
    run time and recorded in the run summary.
  - **Direct path**: `MZmineDetector.detect()` → 1,388 features.
  - **Platform entry point**: `POST /metabolomics/detect` (real mode) →
    HTTP 200, identical table, provenance notes carry engine version +
    batch sha256 prefix `61a4bf375c5a9ac7`.
  - **Tetracycline (precursor-mass + RT match, pending MS/MS
    confirmation)**: features 559 (445.16056, +0.135 ppm, RT 326.65 s),
    576 (445.16058, +0.180 ppm, RT 332.44 s), 610 (445.16068, +0.404 ppm,
    RT 345.96 s; dominant height 1.6e8; 0.019 min from the MassBank apex
    anchor 5.747 min).
- **Preserved artifacts** (`data/validated_runs/M1a/`, gitignored under
  `data/raw/` rules but the run dir itself is intended for commit unless
  policy says otherwise — flagged for review): `batch.mzbatch`
  (sha256 dddf7310…), `features.csv` (sha256 549c4dbd…),
  `mzmine_run.log`, `run_summary.json` (checksums, engine identity,
  feature count, per-match m/z/ppm/RT), `api_detect_response.json`.
- **Evidence boundaries**: single dataset, single compound standard, one
  engine version. Cite as "precursor-mass and retention-time match
  pending MS/MS confirmation" — NOT an identification. Not a broad
  scientific validation.
- **Next**: M1-b vendor-native conversion acceptance test (separate).

## Milestone: M1-b vendor-native conversion PASSED (measured) — ThermoRawFileParser chain (ADR-0087)

- **Milestone/status**: **M1-b passed** — a Thermo `.raw` file was converted
  vendor-natively by the pinned ThermoRawFileParser 2.0.0.0 (bundled under
  the pinned MZmine 4.11.0 `external_tools/`) and the resulting mzML was
  run through the same `MZmineDetector` batch as M1-a. Recorded in
  ADR-0087. The fail-closed contract (no parser → `CloudServiceError`
  naming the gap) is covered by `tests/test_metabolomics/test_vendor_conversion.py`.
- **Measured result (2026-10-31)**:
  - Input: `20220426_UMETAB266_POS_Thiostreptone.raw` (MSV000090047, Zenodo
    mirror; 68,362,907 bytes; MD5 `ac0890f515c05f2ad3d3b6e599a3b11c`
    matching the Zenodo record checksum; SHA-256 `0f039d2578c2b244f…`).
  - Conversion: 5,023 scans, indexed mzML 34,638,312 bytes
    (SHA-256 `f1e7409e717790a7…`).
  - Detection: MZmine 4.11.0 batch → 1,926 features.
  - **Thiostrepton [M+2H]²⁺ (precursor-mass + RT match, pending MS/MS
    confirmation)**: theoretical m/z 832.75345 (neutral 1663.492353,
    C72H85N19O18S5); measured feature 1423 at m/z 832.75345 (0.54 ppm),
    RT 505.22 s (8.4204 min), area 6.9e5; the per-charge M+2 satellite
    (833.75404, feature 1424) appears at the identical RT — consistent
    with the 5-sulfur isotope envelope. Two weaker 832.753 features at
    other RTs (1187, 1460) are also recorded.
- **Evidence boundaries**: same as M1-a — single dataset, single compound
  standard, precursor-mass + RT match pending MS/MS confirmation, NOT an
  identification. The file has no MS2 scans (full-scan-only acquisition),
  so MS/MS confirmation remains pending for both M1-a and M1-b.
- **Preserved artifacts** (`data/validated_runs/M1b/`): `run_summary.json`
  (checksums, engine identity, per-match m/z/ppm/RT), `batch.mzbatch`
  (sha256 0c101f42…), `features.csv`, `mzmine_run.log`,
  `converted/20220426_UMETAB266_POS_Thiostreptone.mzML`.
- **Next**: MS/MS confirmation path (needs an MS2-containing file) and/or
  additional vendor converters (Waters, Bruker, Agilent, Sciex) under
  follow-up ADRs.
