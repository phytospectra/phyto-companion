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

## Test/lint addendum

- Full suite: `pytest -q` → **1524 passed**, 14 warnings, 346.93s (2026-10-31, Python 3.12, includes the new runtime-mode tests). Warnings are pre-existing class-scoped-fixture deprecations, not failures.
- Lint: `ruff check shared services scripts tests` → **All checks passed!**
