# HANDOFF — metabolomics evidence layers (ADR-0068..ADR-0072)

**Date:** 2026-10-22 · **Status:** implementation complete, all phases
delivered; nothing committed (per brief).  Companion review artifact:
`REVIEW_metabolomics_layers.md`.

## What was delivered

Additive, named evidence layers for standard metabolomics analytics, in
the house style of the chromatography layers (ADR-0061..0066): each layer
that RUNS appends typed EvidenceItems with named sources and thresholds;
each layer that is SKIPPED for missing inputs is reported with a specific
reason, both in `skipped_layers` AND as evidence — never silent, never a
fabricated number.

### Shared models (`shared/phyto_shared/metabolomics.py`)

`PreprocessingRecord` (NMR + LC-MS field families; the absent family
stays `None`, never zero), `PCAModelEvidence`, `PLSModelEvidence`,
`VIPFeature`, `STOCSYCorrelation`, `ReportingChecklist`,
`MetabolomicsInput` (dataset reference), `PreprocessingOutcome`.
Relation names are constants in the same module
(`nmr_preprocessing_applied`, `pca_model`, `opls_regression_model`,
`pls_da_model`, `vip_feature`, `stocsy_correlation`,
`reporting_checklist`, plus skip relations).  `SampleMetadata` gained an
optional `metabolomics: MetabolomicsInput | None` field (schemas.py).

### Services (Phase 3)

- **`services/nmr_preproc/`** — in-house ¹H NMR pipeline: Bruker
  fid/acqus loader (with the SFO1 fallback for acqus files lacking SF)
  and processed-1r loader (with the pdata-inside-`1` fallback),
  exponential apodisation → FFT → auto-phase → TSP referencing → ASLS
  baseline → correlation alignment → PQN → 0.04 ppm bucketing.  Raises
  `SkipPreprocessing` with a named reason on unusable input; mixed
  raw/processed batches are refused explicitly.  FastAPI endpoint
  `POST /nmr-preproc/preprocess` returns `applied` (full serialised
  result) or `skipped` (reason).
- **`services/lcms_preproc/`** — adapter ONLY (no peak picking, no
  deconvolution — ADR-0069): MZmine 3 / MS-DIAL / XCMS wide exports and
  a tool-neutral long format, building the same `PreprocessingRecord`
  shape (modality `lcms`).  Non-sample metadata columns (`feature area`,
  `fwhm`, …) are skipped by a known-label list, not a values heuristic.
  Endpoint `POST /lcms-preproc/ingest`.
- **`services/metabolomics/app/`** additions — `opls_regression.py`
  (proper O-PLS: predictive + orthogonal components, balanced
  Monte-Carlo Q2, permutation test with a fixed seed; OPLS-DA itself
  stays unimplemented per ADR-0015), `vip.py` (VIP from either model,
  with the pairing note when no univariate test was supplied),
  `stocsy.py` (1D driver and 2D all-pairs, BH FDR, hypothesis warning
  attached), `reporting.py` (ADR-0072 checklist: fields present from
  records or listed missing — never filled).

### Orchestrator wiring (Phase 4)

- `layer_chain.py` gained `apply_metabolomics_layers()` running
  preprocessing-evidence → feature matrix → PCA → supervised model
  (continuous Y ⇒ O-PLS regression; two-class labels ⇒ PLS-DA; neither ⇒
  named skip) → VIP → STOCSY (driver + 2D) → reporting checklist.  Every
  layer failure is recorded as `model_validation_failed` evidence and the
  remaining steps still run.
- `tool_registry.py`: two Tier-1 tools `metabolomics.preprocess` and
  `metabolomics.apply_layers`, both dry-run enabled; the mock executor
  runs the REAL pipeline/chain (the layers are the deliverable), with
  sample-sheet auto-loading from the CSV path named in
  `MetabolomicsInput`.
- `planner_agent.py`: `metabolomics-preprocess` →
  `apply-metabolomics-layers` tasks planned only when
  `sample_metadata.metabolomics` exists; otherwise a logged skip.

### Fixture + tests (Phase 5/6)

`tests/fixtures/metabolomics/curcuma/` — the real 20-sample Curcuma
longa postharvest dataset (raw fid + processed 1r, 5.4 MB) with sample
sheet, peak-assignment ground truth, acquisition parameters, the vendor
README/verify script, and the R metabom8 reference pipeline (GPL, kept as
a comparison artifact only — NOT used at runtime, ADR-0070).
Seven test files under `tests/test_metabolomics/`:

1. `test_nmr_preproc_pipeline.py` (11) — loader, PQN, mixed-modality
   refusal, and the documented decay: enol-CH (5.95 ppm) intensity
   anti-correlates with storage day (r ≈ −0.9), monotone across days,
   recovered half-life in a plausible band around the documented ~22 d.
2. `test_lcms_preproc_adapter.py` (5) — schema detection, pivot, named
   skips, record shape with NMR fields absent-not-zero.
3. `test_statistics_layers.py` (12) — O-PLS recovers storage day
   (R2Y 0.97, Q2 0.99, permutation p 0.0099 on the fixture), VIP/STOCSY
   behaviour and named skips.
4. `test_layer_chain_metabolomics.py` (9) — pipeline order, additive
   behaviour (PCA still runs when the supervised model skips), skip
   visibility as evidence, validation-failure handling, checklist.
5. `test_tool_registry_metabolomics.py` (7) — tier/dry-run, real
   execution through the registry contract, sheet auto-loading.
6. `test_planner_metabolomics.py` (7) — task presence/absence, graph
   validity, `MetabolomicsInput` validation.
7. `test_reporting_checklist.py` (5) + `test_preproc_endpoints.py` (6) —
   checklist honesty and the two HTTP contracts.

**Full suite: 1343 passed** (was 92 collected in test_metabolomics before
this work; the metabolomics additions contribute 62).

## Key decisions made during implementation

- **STOCSY driver choice:** the vendor README recommends the 7.55 ppm
  oxidation marker, but after PQN the weak marker (base amplitude 0.05
  vs 0.55 for curcuminoids) has no bucket correlation above |r| ≥ 0.7.
  The enol-CH singlet (5.95 ppm) is the defensible driver; the raw-1r
  marker trend itself is real and verified (4.7× rise, monotone) — tests
  assert the documented facts, not the vendor's driver choice.  This is
  recorded in the test docstrings, not silently worked around.
- **`SF` vs `SFO1`:** real Bruker acqus may omit `SF`; the loader falls
  back to `SFO1`.  Same discipline for pdata living inside `1/`.
- **MZmine metadata columns:** columns like `feature area` after the
  id/mz/rt block are skipped by an explicit label list
  (`METADATA_COLUMN_LABELS`); a sample name never needs to be guessed.
- **FeatureTable bridging:** the chain bridges bucket matrices into the
  existing `FeatureTable` model (`mz = index + 1` because `Feature.mz`
  is `gt=0`); the mz/rt values are placeholders — the chemical axis is
  the ppm axis carried alongside, and LC-MS features keep their own ids.

## Not done (deliberate, per brief/ADRs)

- OPLS-DA (ADR-0015), DIABLO (ADR-0018), R/metabom8 at runtime (GPL).
- No frontend changes; no commits or pushes.
- Batch-association testing in PCA remains a documented manual-inspection
  note, not a formal test.

## Verification

```
.venv/bin/python -m pytest tests/ -q   # 1343 passed in ~72 s
```
