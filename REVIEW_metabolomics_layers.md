# REVIEW — metabolomics evidence layers (ADR-0068..ADR-0072): DoD report

**Date:** 2026-10-22 · **Status:** review artifact — records
implementation against the brief's definition of done; nothing committed.

## 1. Definition-of-done checklist

| # | DoD item (brief) | Evidence | Status |
|---|---|---|---|
| 1 | Shared metabolomics models in `phyto_shared` | `shared/phyto_shared/metabolomics.py` (PreprocessingRecord, PCAModelEvidence, PLSModelEvidence, VIPFeature, STOCSYCorrelation, ReportingChecklist, MetabolomicsInput); `SampleMetadata.metabolomics` field in `schemas.py` | **done** |
| 2 | In-house NMR preprocessing service; non-FID input skips with reason | `services/nmr_preproc/` (pipeline.py, main.py); `SkipPreprocessing` reasons asserted in tests 1/7 | **done** |
| 3 | LC-MS adapter-only; feature tables in, no peak picking | `services/lcms_preproc/` (adapter.py, main.py); no deconvolution code exists | **done** |
| 4 | O-PLS **regression** for continuous Y; NO OPLS-DA (ADR-0015) | `opls_regression.py` (continuous Y only); OPLS-DA absent from the public API (constant + test from the earlier statistics suite unchanged) | **done** |
| 5 | VIP selection; empty selection is evidence with a reason | `vip.py`; `select_vip_features(...).empty_reason` tested | **done** |
| 6 | STOCSY 1D driver + 2D all-pairs; correlations labelled as hypotheses | `stocsy.py`; `STOCSY_HYPOTHESIS_WARNING` carried on `STOCSYResult.warning` and in every evidence text | **done** |
| 7 | Reporting checklist (ADR-0072); gaps listed, never filled | `reporting.py`; `missing_fields`/`completeness` honesty tests | **done** |
| 8 | `apply_metabolomics_layers` in the orchestrator chain; additive, skip-visible | `layer_chain.py::apply_metabolomics_layers`; order + additive + skip-as-evidence tests | **done** |
| 9 | Registry tools (Tier-1, dry-run) executing the REAL chain | `tool_registry.py` `metabolomics.preprocess` + `metabolomics.apply_layers`; registry-contract tests | **done** |
| 10 | Planner tasks gated on the dataset reference; no silent absence | `planner_agent.py` `_identify_tasks`; presence/absence + topology tests | **done** |
| 11 | Curcuma longa fixture with documented ground truth | `tests/fixtures/metabolomics/curcuma/` (20 real Bruker dirs, 5.4 MB, sheet + assignment table + README + R reference pipeline as artifact-only) | **done** |
| 12 | Tests assert against DOCUMENTED ground truth, not implementation | decay story verified in raw 1r (marker 4.7× rise; enol-CH r(day) ≈ −0.9; half-life band test) | **done** |
| 13 | Full suite green; no regressions | `1343 passed` | **done** |
| 14 | No commits, no pushes, no frontend changes | working tree only | **done** |

## 2. Ground-truth verification notes (for the record)

- The fixture's vendor story was verified **in the raw 1r data** before
  any test asserted it: oxidation marker (7.50–7.60 ppm integral) rises
  1.55 → 7.36 (×4.7, monotone); enol-CH (5.90–6.00 ppm) falls
  11.5 → 3.2 (monotone); log-linear slope ⇒ half-life ≈ 24.8 d,
  consistent with the documented ~22 d teaching approximation.
- After PQN, the weak 7.55 marker loses its dominance; the vendor's own
  suggested STOCSY driver therefore yields no correlations above
  threshold on a bucketed matrix.  Tests use the enol-CH driver and
  assert the shared-decay correlation structure instead.  The vendor's
  driver choice is documented as a limitation of the teaching fixture
  under PQN, not hidden.
- O-PLS regression on the real fixture: R2Y 0.9665, Q2 0.9858,
  permutation p 0.0099 (n=100), and the top VIP features land on buckets
  with |r(day)| ≈ 0.85–0.96 — chemically interpretable, not circular.

## 3. Deviations from the brief

1. **ADR numbering:** the brief assumed ADR-0067 was the next free
   number; ADR-0067 did not exist.  Per the owner's instruction the new
   decisions took **0068–0072**, leaving 0067 free for the pending
   licensing decision.  All new code cites ADR-0068..ADR-0072.
2. **Runtime metabolomics chain does not import `nmrmetaproc` or
   `nmrglue`:** neither is installable under the pinned dependency set;
   the preprocessing is in-house (`services/nmr_preproc`), which the
   brief's ADR-0068 itself anticipated.  No silent dependency swap.
3. **STOCSY driver on the fixture** is 5.95 ppm, not the README's 7.55
   ppm (see §2).  Tests cite the reason inline.

## 4. Acceptance

The phases were approved stepwise by the owner ("go ahead for 0067 to
0068" covering Phases 2–6).  This document completes the brief's Step 6
reporting requirement; `HANDOFF_metabolomics_layers.md` carries the
operational handoff.  Suggested follow-ups (not in scope): staging
`services/nmr_preproc` + `services/lcms_preproc` into the container
image, a Curcuma-based demo run through the orchestrator's public
endpoint, and the paper-draft disclosure update for the new layers
(`PAPER_self_review.md` items 1–6 still stand; the metabolomics layers
use real fixture data but are NOT validated on independently acquired
biological samples).
