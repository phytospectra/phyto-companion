# Case-study review fixes: readers, consistency, lipids, MSI levels

What was built from the external review of the "NMR file for prediction"
case study, in the review's own priority order.  Every fix is verified
against the real case-study data, not only synthetic tests.

## 1. Read 2rr files correctly (XDIM block storage undone)

`services/nmr_preproc/app/twod.py`

- `undo_submatrix_storage` re-tiles TopSpin XDIM submatrix storage into
  the full (F1, F2) matrix — the same layout nmrglue's `read_pdata`
  implements, in-house because nmrglue is deliberately not a dependency
  (ADR-0068).
- The processed dtype now follows `DTYPP`/`BYTORDP` (not hardcoded int32).
- The COSY orientation check was replaced: the old element-wise
  matrix-vs-transpose correlation is meaningless for non-square spectra
  (F1 SI 1K vs F2 SI 2K) and silently transposed correctly-read data.
  The new `_partner_symmetry_score` is the physical test — every
  cross-peak at (F2=a, F1=b) must have a partner at (F2=b, F1=a),
  searched with a +/-1-point neighbourhood because the two axes have
  different ppm spacings.
- **Verified on the real M6-4 COSY** (`M6-4-2/3`): partner-symmetry
  0.0 (flat read) -> 1.0 (de-blocked); real cross-peaks recovered
  ((1.28, 1.28), (0.85, 0.88)/(0.88, 0.85), (7.29, 7.28) ...).

## 2. Honour DTYPA when reading FIDs

`services/nmr_preproc/app/pipeline.py`

- `_fid_data_type(acqus)`: `DTYPA=2` -> float64 re/im pairs (nmrglue
  convention), else int32; byte order from `BYTORDA`.  The chosen dtype
  is recorded as `fid_data_type` in the provenance metadata.
- The processed `1r` path honours `DTYPP` the same way.
- **Verified on the real export**: every 1H `fid` declares `DTYPA=2`;
  decay ratios now read 101.9 / 580.7 / 23.1 / 162.9 (previously ~1.0
  with garbage magnitudes).  The case study's recorded finding "raw
  FIDs are corrupt (quantized noise, no FID decay)" was this misread —
  the FIDs are healthy.  The README correction is in
  `CaseStudy_Photospectra/README_case_study.md`.

## 3. Automatic consistency checks (numbers in tables and text must match)

`services/nmr_preproc/app/consistency.py` (new), wired into
`render_report_docx` and `export_bundle` (voice rule R7, ADR-0075).

- Parses the ACS text back into (shift, label) pairs — tolerant of
  nested parentheses ("OCH2O (a)") and hyphenated labels ("H-11a") —
  and cross-checks against the assignment table, the header metadata,
  the picked-peak list (warning) and deconvolution centres (error).
- Any error REFUSES the docx/bundle (fail-loud, like R1-R6); every
  bundle carries `tables/consistency_report.txt` and a docx
  "Cross-artefact consistency" section.

## 4. Exclude lipid regions from 13C assignment

`shared/phyto_shared/lipid_regions.py` (new, ADR-0076) +
`candidate_ranker.lipid_curation_13c`.

- One shared, citable mask: default = the aliphatic fatty-acyl window
  `[10, 40]` ppm only (the case-study precedent and the one window that
  cannot overlap pterocarpan/flavonoid carbons); glycerol / olefinic /
  triglycerol-ester windows are catalogued with reasons but opt-in.
- Applied to `c_1d` and the HSQC carbon dimension before any matching,
  with every dropped peak recorded as an `EvidenceItem`
  (`relation="lipid_region_excluded"`) on every rank.
- **Verified on the case-study M6-4 13C list**: the lipid chain carbons
  (14.1 / 22.7 / 29.7 / 31.9 / 34.0) drop; all ten analyte carbons
  (66.8 ... 175.9) survive.  A first draft with all four windows on was
  rejected against this very list — it deleted genuine C-6/C-11a/aromatic
  carbons.

## 5. MSI levels as the standard defines them

`shared/phyto_shared/constants.py`, `nmr_preproc/home_library.py`,
`elucidation/candidate_ranker.py`, `orchestrator/layer_chain.py`,
`orchestrator/main.py`, `shared/phyto_shared/models.py` (ADR-0075).

- `MSI_LEVEL_NAMES` states the standard's own level names (Sumner et
  al. 2007): identified compound / putatively annotated compound /
  putatively characterized compound class / unknown compound; the
  requirement texts are restated accordingly.
- `home_library.dereplicate` no longer stamps "MSI Level 1 pending
  human sign-off" on a curated *literature* table match: strong matches
  are Level 2 (putatively annotated), partial matches Level 3, none
  Level 4.  Level 1 stays the calibration service's authentic-standard
  evidence (ADR-0061).
- `CandidateRank.msi_level` (new field) makes the attribution explicit:
  ranker baseline 2 (spectral similarity without a standard), set to 1
  by the calibration layer together with `level_1_match`; the
  orchestrator summary reports the attributed level instead of a
  blanket 1-or-2.

## Per-file test map

| File | Tests | Covers |
| --- | --- | --- |
| `tests/test_metabolomics/test_bruker_reader_fixes.py` | 12 | XDIM undo round-trip, flat fallback, non-divisible XDIM, truncated file, partner-symmetry orientation, DTYPA float64/int32 |
| `tests/test_metabolomics/test_report_consistency.py` | 9 | ACS text parsing, clean pass, warnings vs errors, refusal at export, consistency_report.txt |
| `tests/test_metabolomics/test_lipid_msi_fixes.py` | 10 | lipid mask defaults/catalog/curation, ranker c_1d + HSQC plumbing, disable switch, MSI level names, ranker Level-2 attribution |
| `tests/test_metabolomics/test_nmr_advanced_bundle.py` | 1 updated | home-library verdict now asserts Level 2 (putatively annotated) |
| existing suites | unchanged behaviour | 1488 passed total, ruff clean |

## Design invariants

- Readers never guess: unknown XDIM/DTYPA combinations fall back to the
  historical layout and record what was read.
- Exclusions are recorded, never silent: every lipid drop and every
  consistency finding carries a reason and lands in the audit chain.
- Refusal beats repair: an inconsistent bundle is not written, a
  non-physical orientation is not rendered.
- Standards are cited verbatim, not paraphrased into house vocabulary.

## Known limitations

- The XDIM undo covers the 2D case (one submatrix level); 3D+ processed
  Bruker data (3rrr) is out of scope until a dataset demands it.
- The lipid default protects the platform's target chemistries
  (pterocarpans, flavonoids); genuinely aliphatic natural products
  (prenyl chains 17-35 ppm) require `exclude_lipid_regions_13c=()` or a
  tightened window — stated in the module docstring.
- `_text_entries` tolerates one level of nested parentheses; labels
  containing commas are flagged as inconsistent rather than parsed.
