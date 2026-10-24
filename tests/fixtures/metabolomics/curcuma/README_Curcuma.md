# Dataset 4: Curcuma longa NMR Metabolomics — Postharvest Curcuminoid Degradation
**A continuous/regression design, for contrast with the categorical designs used previously**

## Experimental design

| Storage day | 0 | 7 | 14 | 21 | 28 |
|---|---|---|---|---|---|
| n (rhizome batches) | 4 | 4 | 4 | 4 | 4 |

**Total: 20 samples.** **Design type: continuous covariate (regression), not a categorical comparison.** Storage day is a numeric variable, not a group label — this is the standard structure for a shelf-life / postharvest quality-degradation question in natural product QC, and it requires **O-PLS regression** (continuous Y) rather than **O-PLS-DA / PLS-DA** (categorical Y), which is the deliberate contrast with the previous three datasets (all of which used discrete group labels).

**Bioactive target:** curcuminoids (curcumin, demethoxycurcumin, bisdemethoxycurcumin) — the signature bioactive compounds of turmeric, well known to be light/heat/oxidation-sensitive during storage. Diagnostic markers: the very characteristic aromatic **OCH3 singlet (~3.85 ppm)**, the **enol CH singlet (~5.95 ppm)**, and aromatic ring protons (~6.75–7.15 ppm, one signal per curcuminoid analogue).

**Ground-truth biological story:**
- Curcuminoid signal intensity **decreases** with storage day, modelled as a first-order-like decay (teaching approximation, half-life ≈ 22 days)
- A generic oxidative **degradation-product marker (~7.55 ppm)** starts near-zero and **increases** roughly six-fold by day 28

These two trends move in opposite directions with the same underlying cause (oxidative degradation), giving a clean teaching example of correlated variables in a regression context — exactly what STOCSY and O-PLS regression loadings are designed to reveal.

**Acquisition:** 400 MHz ¹H, `noesygppr1d`, CD3OD extract, TSP reference, acquisition order **randomised** across all five time points (see `run_order` in the sample sheet) — important here specifically because a naive design (e.g. always running day-0 samples first) would confound storage time with acquisition-order drift; randomisation breaks that confound.

## Files
```
Curcuma_longa_NMR_dataset/           20 Bruker-style experiment folders (CL_D00_S1 ... CL_D28_S4)
sample_sheet_Curcuma_longa.csv       metadata (storage_day, rhizome_batch_id, run_order, ...)
peak_assignment_table_Curcuma.csv    ground-truth chemical shifts / amplitudes
acquisition_parameters_Curcuma.json  shared spectrometer settings
verify_and_plot_curcuma.py           independent sanity check + QC figure
figures_Curcuma/curcuma_storage_trend.png
```

## Suggested workshop exercises
1. Import, preprocess (same pipeline as before).
2. PCA — does PC1 track `storage_day` monotonically?
3. **O-PLS regression** with `storage_day` as a continuous Y (not a factor) — interpret the predictive component as a "degradation axis."
4. Plot the curcuminoid marker's integrated intensity directly against `storage_day` and fit a simple exponential/linear decay curve — compare the recovered half-life to the ground truth (~22 days, in `acquisition_parameters_Curcuma.json`/generator script).
5. STOCSY from the oxidation-product marker (~7.55 ppm) to confirm it anti-correlates with the curcuminoid signals — a chemically interpretable degradation pathway, not just a statistical artefact.
