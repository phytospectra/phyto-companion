# Case-study reconciliation: HMBC coordinates, Word-report disagreement, FID wording

Date: 2026-10-31 · Phase 1B of the enterprise brief · evidence: measurements on
`tests/fixtures/nmr_case_study/archive_a/M6-4-2/4/pdata/1` (M6-4 HMBC, maackiain)
under the §4 reference S/N implementation (`test_case_study_regression._sn`).

## 1. Brief finding 4 — HMBC labels vs coordinates: RESOLVED (ADR-0079)

The brief observed that the §13 regression suite pinned `H-11a→C-4a` at
(5.47, 66.5) with exactly the same frozen baseline and control as
`H-11a→C-6` (18.3, 6.6) — the same physical cross-peak listed twice — and that
`H-6eq→C-4a` measured 3.7 at 66.5 while §13 claims "verified ≈27".

Measurement on the fixture (full F1 scans of the H-11a, H-6eq and H-1 columns):

| Correlation | Old pin | Measured at old pin | Correct carbon | Measured at correct carbon |
|---|---|---|---|---|
| H-11a→C-6 | (5.47, 66.5) | 18.3 ✓ | 66.5 (C-6; the only 13C signal there) | 18.3, 2.8× control — verified |
| H-11a→C-4a | (5.47, 66.5) ← duplicated C-6 peak | 18.3 (same peak) | **157.2** | **13.9, 2.3× control — verified** |
| H-6eq→C-4a | (4.22, 66.5) | 3.7 (mis-filed "divergence") | **157.2** | **32.2, 6.8× control — verified, reconciles §13's ≈27** |

The 13C axis has exactly one signal at 66.5 (§13's own list: 157.2, 132.1,
109.8, 104.7, 103.7, 101.3, 93.8, 78.5, 66.5, 40.2). A separate C-4a pin at
66.5 asserted a carbon that does not exist. C-4a is the oxygenated aromatic
quaternary at 157.2. With the corrected coordinates, §13's S/N claims
(≈10, ≈27) reconcile within its ±30% tolerance — these were verification
wins, not divergences. The remaining §13 divergences (H-7→C-10a, H-7→C-9,
H-1→C-3/4a, OCH2O→C-8, OCH2O→C-9, H-1→C-11a, H-11a→C-11b, H-2→C-11b,
H-6a→C-10a) still measure 3–6 and stay pinned as presence bands; the suite
(18 tests) passes unchanged except for the two corrected rows.

Also re-verified while scanning: `H-1→C-3/4a` at 66.5 measures 3.9; the
strongest H-1 cross-peaks sit at 112.7/103.7/103.0 (S/N 10.6–12.8), which
supports §13's own "C-3/4a" ambiguity label — left unchanged.

## 2. Brief finding 3 — Word reports disagree on COSY/HMBC: CONFIRMED, dated

Two Word reports describe the same M6-4 sample with contradictory 2D verdicts:

- `Phytospectra_NMR_Report_FullCapabilities.docx` (431 lines, generated
  2026-10-24, before the XDIM reader fix): "COSY H-11a↔H-6a verified … other
  2D correlations **unverifiable (artifact-dominated reconstruction)**";
  "reconstruction is artifact-dominated (NUS-aliasing stripes)".
- `NMR Structure Elucidation Report — Flavonoid Isoflavonoid Samples…docx`
  (647 lines, generated 2026-10-25/26, after the XDIM submatrix fix): "All six
  COSY correlations and 15 HMBC correlations … present well above noise; four
  HMBC contacts are weak".

The disagreement is a **timeline artefact, not a scientific conflict**: the
October 24 report read the COSY flat (XDIM block storage not undone), which
garbles the cross-peaks; the reader fix (partner-symmetry orientation check,
`REVIEW_case_study_fixes.md` §1) restores the real cross-peaks
(partner-symmetry 0.0 flat → 1.0 deblocked), and the later report re-verified
them. **Action:** the October 24 report must carry a visible superseded banner
(before external sharing), or be deleted; only the post-fix report represents
the platform's current read. Recorded in `IMPLEMENTATION_BACKLOG.md` (P1D).

## 3. Brief finding 2 — manuscript "FIDs corrupt" wording: CONFIRMED STALE

`phytospectra-paper/PHYTOSPECTRA-MANUSCRIPT-DRAFT1.md` (line ~94, *Data basis*)
still states the raw FIDs show "a corrupted quadrature layout … no
free-induction decay". The ADR-0075 reader fix (`REVIEW_case_study_fixes.md`
§2) proved this was a misread: the FIDs are healthy float64 (DTYPA=2) — decay
ratios 101.9 / 580.7 / 23.1 / 162.9 — and the workspace README already carries
the correction as a strikethrough (`README_case_study.md` line 17). The
manuscript paragraph and the "Data guards" table row (line ~116) must be
rewritten to the corrected reading before any submission; tracked in
`IMPLEMENTATION_BACKLOG.md` (P1D).
