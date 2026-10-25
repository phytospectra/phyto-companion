# Review report: qNMR capabilities — deconvolution, region integration, custom equations

**Date:** 2026-10-26 · **Status: NOT COMMITTED, NOT PUSHED — for review.**
**ADRs:** ADR-0073 (deconvolution + qNMR integration), ADR-0074 (custom equations).
**Suite:** 1458 passed, 0 failed (`make test`, offline, Python 3.12) · `make lint` clean.

## 1. What was built

The three ACP 2-style capabilities Phytospectra lacked for extract work (see the
Mnova/ACP capability audit of 2026-10-26): mathematical resolution of overlapped
signals, quantitative peak/shape/region integration, and user-defined derived
quantities with pass/fail specifications.

| Capability | Module | What it does |
|---|---|---|
| Deciphering overlapping signals | `deconvolution.py` | Prominence-gated 1D peak picking (MAD noise gate), seeded least-squares fitting of pseudo-Voigt/Lorentzian/Gaussian components on a local baseline; per-region `fitted`/`failed`/`skipped` status with reasons; analytic per-component areas |
| Peak fitting, shape fitting, region integration | `quantify.py` | Trapezoid region integration (edge-anchored linear baseline option), fit-based integration (subset-selectable components of an overlapped cluster), classical internal-standard qNMR with substituted-equation audit text |
| Custom equations | `equations.py` | Whitelisted AST tree-walking evaluator (no eval/exec/compile anywhere); comparisons and and/or allow spec expressions; `check_specs` emits warning/severe pass-fail results (QCAlert convention); three builtin equations |

**New files (service):** `services/nmr_preproc/app/deconvolution.py`,
`services/nmr_preproc/app/quantify.py`, `services/nmr_preproc/app/equations.py`.

**New files (tests):** `tests/test_metabolomics/test_deconvolution.py`,
`test_quantify.py`, `test_equations.py`, `test_qnmr_endpoints.py`.

**Modified:** `services/nmr_preproc/app/main.py` (four endpoints:
`/nmr-preproc/deconvolve`, `/integrate`, `/quantify`, `/equations/evaluate`;
`SpectrumInput` accepts inline arrays or a Bruker experiment dir, raw FIDs put
through the documented minimal chain), `services/nmr_preproc/app/report.py`
(`SampleReport.region_fits`/`quantification`; deconvolution overlay figure;
`tables/deconvolution_<id>.csv`, `tables/quantification.csv`),
`services/orchestrator/app/tool_registry.py` (three Tier-1 tools with
real-implementation mocks), `services/metabolomics/app/normalization.py`
(qnmr placeholder comment now points at the real service; behaviour unchanged),
`docs/DECISIONS.md` + `docs/adr/INDEX.md` (ADR-0073/0074).

## 2. Tests

| File | Tests | Covers |
|---|---|---|
| `test_deconvolution.py` | 16 | MAD noise estimate; picker correctness/SNR gating; singlet area vs analytic truth; two overlapped doublets recovered to 4 components with areas ≤5% error; buried shoulder needs manual seed (documented limit); failed/skipped paths raise nothing, record everything; batch contract; model re-evaluation |
| `test_quantify.py` | 14 | Lorentzian integral vs analytic area; linear-baseline correction; fit-based subset integration of an overlapped pair; qNMR mole-ratio recovery (exact), mass/wt%, standard amount from mass+purity; every non-physical input is a named `SkipQuantification` |
| `test_equations.py` | 29 | Operator precedence; whitelisted functions; audit text; 15 forbidden constructs each raise `EquationError` naming the construct; spec pass/fail with levels; builtins match `qnmr_calculate` |
| `test_qnmr_endpoints.py` | 11 | HTTP contract for all four endpoints; inline + directory spectra; skips return 200 with named reasons; Curcuma CL_D00_S1 real-data smoke (region record in a declared state) |
| `test_tool_registry_metabolomics.py` | 15 (8 new) | Tier-1 registration + dry-run for the three tools; real execution through the tool contract; executor round-trip |
| `test_nmr_report_bundle.py` | 17 (2 new) | Deconvolution/quantification CSVs + figure in the bundle, checksummed; bundle without fits unchanged |

Two real bugs were caught by these tests before commit and fixed:
the MAD noise-sigma formula was inverted (factor ~2 low, letting noise through
the SNR gate) and `pick_peaks` mixed up the props-array index space with the
sample index space (seeds placed at wrong positions, degenerate fits).

## 3. Design invariants worth checking in review

1. **Failures are data, not exceptions** — inside a batch, one bad region
   becomes a `RegionFit(status="failed"|"skipped", reason=...)`; only unusable
   *input* (mismatched axes, no regions, unknown model) raises a named skip.
2. **No eval anywhere** — equations are walked as an AST against a whitelist;
   `eval`/`exec`/`compile` are rejected constructs like any other (tested).
3. **Every number is auditable** — qNMR results carry their substituted
   equation; fits carry model_id, parameters, r², RMSE, baseline coefficients.
4. **Units are explicit** — linewidths quoted in Hz and converted through
   `sf_mhz`; endpoint defaults are overridden by the directory's true frequency.
5. **Skips are HTTP 200** with named reasons, matching the service convention —
   the quantify request models deliberately do NOT `gt=0`-constrain integrals so
   non-physical values reach the service and come back named, not as a 422.
6. **Dependencies: numpy + scipy only** — `curve_fit`/`find_peaks` follow the
   existing house precedent; no lmfit.

## 4. Known limitations

- Prominence seeding misses peaks buried entirely under a stronger neighbour;
  `extra_seeds_ppm` is the documented manual escape hatch.
- One shared local baseline (degree ≤ 1) per region; curved baselines inside a
  narrow region are approximated by a straight line.
- Multiplet fine structure is fitted as free components, not J-coupled groups;
  no error propagation from the covariance matrix yet (areas are point estimates).
- Raw-FID directories go through a minimal apodise→FFT→auto-phase chain; for
  publication-grade fitting, feed vendor-processed `pdata/1` spectra.
- The evaluator whitelist is deliberately total-numeric: no strings, no
  conditionals beyond comparison logic, no user-defined functions.

## 5. Not done (deferred)

- Switching the elucidation ranker's input from picked peaks to deconvolved
  component lists (the evidence bridge is designed but not enabled; ADR-0073
  records the deferral pending fit-quality validation on real extracts).
- Planner workflow tasks + `SampleMetadata` fields for orchestrated qNMR runs.
- Error bars on fitted areas (needs covariance propagation through
  `component_area`), GMP-style audit trails, and Excel-format export.
- Frontend surface (the API and the publication bundle are the interfaces today).
