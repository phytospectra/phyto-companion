# Analytical Capability Matrix — M0 (honest coverage and contract design)

Date: 2026-10-31 · Milestone M0 of `../MULTIVENDOR_LCMS_NMR_DISCOVERY_PROMPT.md`
· Companion documents: `FORMAT_SUPPORT_MATRIX.md`,
`DATABASE_CONNECTOR_REGISTRY.md` (+ `data/connector_registry.json`),
`CAPABILITY_REGISTER.md` (service-level), `IMPLEMENTATION_BACKLOG.md`.

Rules applied (from the prompt §1/§3): one row per concrete feature, not per
service; reference tools are comparison targets, never claims of equivalence or
permission to embed; a capability claims only the evidence tier it has
(`local tests` / `live integration` / `scientific validation` / `deployment`).

## How to read this table

- **Existing code**: the actual module(s) today — "none" means none.
- **Actual runtime mode**: what happens if you invoke it right now
  (mock / real / partial / unavailable / blocked).
- **Gap**: the honest distance to the reference family's corresponding feature.
- **Priority**: P1 = M1 vertical, P2 = M2/M3, P3 = M4/M5.

## Feature-level matrix

| # | Capability | User workflow | Reference tool/version/source | Existing code | Actual runtime mode | Gap | Proposed reuse/integration/build | Acceptance dataset/check | Priority | Owner |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Raw LC–MS import (mzML/mzXML) | bring instrument data into a study | MZmine 3.9–4.x; ProteoWizard msconvert | ingestion service handles NMR archives only; no mzML reader (`pyteomics`/`pymzml` not installed) | unavailable | no raw LC–MS ingestion at all | add mzML reader (pyteomics or pymzml, pinned version) behind the ingestion service with archive-path limits | one open mzML fixture (verified-redistributable, M0-b shortlist); scan-count/range/polarity checks vs an independent reference read | P1 | lcms |
| 2 | Vendor raw-format conversion (.d/.raw/.wiff/.lcd) | same, for vendor files | ProteoWizard msconvert (documented SDK requirements); vendor SDKs | none | blocked — needs isolated conversion worker + license review (prompt §4) | every vendor family unconverted | isolated msconvert worker (OS per SDK requirements); vendor-export fallback with provenance | one licensed fixture per advertised format/version/mode + negative cases (incomplete dirs, corruption, mislabeled extensions) | P1 (first family) → P3 (rest) | lcms |
| 3 | Chromatogram building / EIC | review TIC/BPC/EIC, build XICs | MZmine ADAP chromatogram builder; XCMS findChromPeaks; MS-DIAL | none (adapter is table-only; `MockDetector` is synthetic) | **blocked** — `MZmineDetector` batch contract unverified (ADR-0084: CSV "batch" ≠ MZmine XML batch; export schema unproven; no binary/fixture locally) | whole raw-processing chain missing | validate MZmine live-integration first (M1-a); keep engine-agnostic `FeatureDetector` protocol | real mzML + recorded engine version/config; feature table compared against the engine's own export | P1 | lcms |
| 4 | Peak deconvolution / isotope / adduct grouping | resolve coelution, isotope envelopes, adducts | MZmine minimum-search/SMILES; MS-DIAL deconvolution | none | unavailable | missing entirely | engine-provided first (MZmine batch steps); in-house only if a benchmark says so | isotope/adduct hypotheses on a reference mixture with frozen tolerances | P1 | lcms |
| 5 | Cross-sample alignment + gap filling | align features across injections | MZmine join aligner; XCMS obiwarp | `DetectParams.alignment_method` names "join"/"obwarp" but nothing implements them | unavailable | same chain | engine-provided (join/obwarp batch steps); distinction gap-filled vs directly-detected recorded per feature | replicate-alignment benchmark (pooled QC pairs) | P1 | lcms |
| 6 | Feature-table import (already-processed tables) | import MZmine/XCMS/MS-DIAL exports | — | `services/lcms_preproc/app/adapter.py` (ADR-0069) | real (local tests) | named-skip on unknown schemas; no unit/samplesheet validation | keep; add samplesheet/role validation per prompt §4 | real exported tables (owner-provided); negative cases | P1 | lcms |
| 7 | QC monitoring (blanks, pooled QC, drift) | injection QC, batch diagnostics | Agilent MPP QC workflows; MZmine QC modules | `qc_monitor.py` (qc_report/alerts, QC-correction, suggest_rerun), `normalization.py` | real on any supplied table | input-mode responsibility sits with the caller; no carryover/saturation checks | extend qc_monitor with carryover/saturation/IS-response checks | synthetic ground-truth suite (existing); then a real QC-padded table | P1 | metabo |
| 8 | Normalization + batch correction | study preprocessing | MPP; MetaboAnalyst | `normalization.py` (normalize, qc_correction, flag_low_quality) | real (pinned statistics) | method choice not study-design aware; no before/after plots yet | keep; document assumptions per method; add before/after artifacts | pinned statistics (existing) + real-table acceptance | P1 | metabo |
| 9 | Differential statistics / PCA / PLS-DA / RF | group comparison, biomarkers | MPP; MetaboAnalyst; scikit-learn | `statistics.py`, `differential_analysis.py`, `multi_block_fusion.py` (MFA, consensus PCA, permutation) | real (pinned statistics) | OPLS-DA policy: documented stance maintained (brief) — do not add methods to fill a checklist | keep as-is | pinned tests (existing) | — | metabo |
| 10 | Formula generation (elemental enumeration) | candidate formulas from accurate mass | SIRIUS formula ranking; MassHunter formula ID | `FormulaCandidateEngine` exists in elucidation (enumerate/rank under element bounds) | partial — deterministic enumeration; no isotope-envelope scoring yet | isotope-fit and fragment-compatibility components missing | extend with isotope-envelope scoring; never hard-exclude non-CHNO elements by default | isotope-benchmark mixture (e.g. reference standard list) with frozen tolerances | P1 | elucidation |
| 11 | Experimental MS/MS library search | spectral matching vs references | GNPS spectral libraries; MassBank; MoNA | networking service submits MGF to GNPS (mock client locally; real client fail-closed, never exercised) | real (fail-closed) / never run live | no recorded live integration; no mirrored-spectrum dedup | GNPS first (route exists); MassBank/MoNA connectors in M2 | one recorded PUBLIC-dataset FBMN job, results reconciled | P1 | networking |
| 12 | In-silico fragmentation / candidate ranking | fragment trees, explained peaks | SIRIUS/CSI:FingerID; MetFrag; CFM-ID | `candidate_ranker.py` ranks by mass/formula/NMR evidence; no MS/MS fragmentation engine | partial (no fragment evidence) | biggest single elucidation gap | MetFrag or CFM-ID behind the evidence interface (license-checked); predicted spectra kept distinct from experimental | isomer/analog benchmark incl. no-hit compounds (prompt §6) | P2 | elucidation |
| 13 | NMR processing, deconvolution, qNMR | 1D/2D processing, quantification | Mnova; TopSpin; NMRPipe; NMRium | `services/nmr_preproc` (pipeline, deconvolution, qNMR equations AST-gated, consistency gate) | real (local tests; real archives in regression) | NUS reconstruction distinct capability (absent); held-out archive validation pending | keep; NUS only with sampling schedule + validated processing | §13 case-study suite (18 tests, CI-gated); ADR-0079 coordinates | — | nmr |
| 14 | 2D NMR assignment support (HSQC/COSY/HMBC) | structure elucidation evidence | Mnova; CMC-assist analogs | `twod.py`, case-study regression, HSQC networks | real (pinned) | NOESY/ROESY/DOSY handling explicit but minimal | keep; explicit unsupported states for unvalidated experiments | §13 suite | — | nmr |
| 15 | NMR shift prediction (ranking evidence) | candidate ranking | in-house mock + Vertex endpoint | `shift_predictor.py` (mock/real, ADR-0078) | mock (local) / real (cloud, fail-closed) | real mode never exercised | one recorded live Vertex prediction with model/version in provenance | §13 expected shifts (mock pinned); live run pending | P1 | elucidation |
| 16 | Evidence-separated candidate ranking | combined MS+NMR ranking | — (in-house design) | `candidate_ranker.py` + `FileStructureDB` (ADR-0081/0082) | real (with curated CSV) | provenance separation done for structures; MS/MS evidence tier absent (ties to #12) | add fragment-evidence tier when #12 lands | Phase-2 vertical (2-molecule library); §13 case | P1 | elucidation |
| 17 | Molecular networking (MS2, FBMN/IIMN) | spectral families, ion identity | GNPS/GNPS2 FBMN + IIMN | `services/networking` (client + parser + visualization) | real (fail-closed) / mock (dry-run) | live run never recorded; IIMN edge evidence unparsed | run one public-dataset FBMN job; parse/reconcile; keep mock labeled | public-dataset job recorded end-to-end | P2 | networking |
| 18 | Fraction lineage / bioactivity networking | activity-guided discovery | — (in-house design; ZODIAC-adjacent concepts) | `bioactivity_networking.py`, isolation service records | real (local) | `compound_links` establishment not yet validated (prompt §8) | co-variation + mass-constraint link rules with recorded evidence/error rate | fraction-series case study | P2 | networking |
| 19 | Targeted quantification (LC–MS) | calibration, quantifier/qualifier | MassHunter Quant; Skyline | calibration service (MSI Level-1 curves); no LC–MS quant pipeline | partial (NMR-side only) | whole LC–MS quant workflow missing (calibrators, IS, integration audit) | build on calibration service; method-specific LOD/LOQ | reference-mixture acceptance with frozen tolerances (prompt §9) | P2 | lcms |
| 20 | qNMR review | purity/quantitation | qNMR best practice; Mnova qNMR | `nmr_preproc` qNMR region integration + AST equations | real (pinned) | method-validation framing: equations exist, validation record does not | add method-validation record template (relaxation, purity, uncertainty) | §13 qNMR case | P2 | nmr |
| 21 | Study-level workflow (design → stats → report) | comparative metabolomics | MPP; MetaboAnalyst | metabolomics service endpoints + orchestrator | real (pinned statistics) | study-design metadata model absent (paired/covariate/batch) | design-first schema before statistics (prompt §9) | synthetic two-group suite; then real study | P2 | metabo |
| 22 | Unified UI review surfaces | inspect spectra/tables/networks | Mnova; vendor viewers; NMRium | frontend (Next.js) with evidence fields + fixtures | partial (MSI level mirrored; vitest env-limited) | linked TIC/BPC/EIC views, mirror plots, lineage graph all absent | §10 of the prompt, phased | FE contract tests + visual review | P2 | frontend |
| 23 | Agent-assisted workflows (bounded) | guided processing/elucidation | — (in-house) | orchestrator planner + QA critic (model identity tagged) + tiered tool access | real/partial | recipe selection must never be silent (prompt §10) | agent proposes recipe + justification; reviewer confirms | tier-violation tests (existing); recipe-justification test | P2 | orchestrator |
| 24 | Isolate vs mixture workflow separation | correct claims per sample type | — (design requirement) | novel-structure proposal handles isolates; mixture mode absent | partial | mixture component bookkeeping (ambiguous peak ownership) missing | explicit sample-type declaration gating downstream claims | mixture case with known composition | P3 | elucidation |
| 25 | ADMET / target prediction | computational hypotheses only | DeepPurpose-class tools; pharmacophore engines | `services/pharmacology` (rule/KB-based, DISCLAIMER on every response) | partial; real mode explicitly unavailable until Step 8 (ADR-0083) | applicability-domain discipline for any future ML | keep unavailable/exploratory until validated providers (prompt §9) | — | P3 | pharm |

## Priorities summary

- **P1 (M1 vertical)**: #1 raw mzML import → #3–5 validated MZmine chain →
  #6 table adapter → #7/8 QC+normalization → #10 formula → #11 one permitted
  library search → #16 evidence-separated ranking.
- **Blocked-with-name**: #2 (conversion worker + license review), #3 (MZmine
  binary + XML batch + export schema), #19 (needs #1–5).
- **Reference families deliberately NOT cloned**: all proprietary engines in
  the prompt's §3 table are comparison targets; the platform reuses open
  engines (MZmine first) and implements the rest behind its own contracts.

## Inherited-code repairs recorded (prompt §3 requirement)

- `MZmineDetector._build_batch` CSV-vs-XML batch defect: ADR-0084 (unverified,
  fail-closed, no silent claims).
- Silent mock selection in metabolomics/networking/pharmacology +
  deferred startup validation: ADR-0083 (fixed this milestone).
- Provenance-default dishonesty (`experimental` for synthetic/undeclared):
  ADR-0082 (fixed).
- HMBC coordinate mis-pinning: ADR-0079 (fixed earlier this branch).
- Reader/report discrepancies (XDIM/FID): ADR-0075 lineage + P1D metadata
  sweep (done earlier this branch).
