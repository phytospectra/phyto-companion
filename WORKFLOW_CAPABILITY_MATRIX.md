# Workflow capability matrix — 2026-11-09

Honest, code-grounded status of every user-facing `POST /analysis` workflow in
the orchestrator, plus the separately routed real capabilities that exist
outside that surface. This matrix is the reference for frontend availability
labels, release notes, and manuscript claims. Statuses:

- **REAL** — executes against real local providers; no synthetic science on
  the execution path (still not scientifically *validated*; see last column).
- **MOCK-ONLY** — runs only under `PHYTO_SCIENTIFIC_EXECUTION=mock` with
  synthetic, fixture-grade outputs; must be labelled offline demonstration.
- **UNSUPPORTED (real)** — rejected with HTTP 422 before any provider call
  when the real executor is configured.
- Validation status is separate from implementation status and is never
  implied by a green test suite.

## `POST /analysis` workflows

| Workflow | Real mode | Mock mode | Real dependencies that would be required | Mock components used in mock mode | Modality gate | Scientific validation |
|---|---|---|---|---|---|---|
| `reference_hsqc_identify` | **REAL**: `FileStructureDB` (RDKit) formula retrieval + `match_hsqc` (SciPy) against a curator-supplied measured-reference JSON; mandatory `before_confirmed_claim` checkpoint; `evidence_assessment` abstention/ambiguity signals. Refused (422) in mock mode — no honest offline implementation. | **Refused (422)**, never fabricated | Already satisfied: curated structure CSV + reference JSON via `PHYTO_STRUCTURE_DB_PATH` / `PHYTO_HSQC_REFERENCE_LIBRARY`. No bundled public library — curation is a release prerequisite. | none | HSQC | **None.** 100 software contract tests (`tests/test_real_orchestrator.py`) prove determinism, fail-closed behavior, schema/identity enforcement and audit; they are not accuracy evidence. |
| `single_compound` | UNSUPPORTED (422 before provider) | MOCK-ONLY: synthetic SIRIUS formula, synthetic shift prediction, fixture Hungarian match, synthetic LLM explanation | SIRIUS integration, real ¹H/¹³C shift predictor (Vertex endpoint), real LLM provider with budget | `sirius.predict_formula`, `shift_mpnn.predict`, `hungarian.match`, `llm.explain` — all deterministic fakes | any of 1H/13C/HSQC/MS2 | None claimed |
| `identify` | UNSUPPORTED (422) | MOCK-ONLY: synthetic ranking incl. synthetic taxonomy prior / isolation / chromatography / metabolomics layers when metadata supplied | Real candidate ranking over a curated library, real taxonomy (GBIF/ITIS), taxon-prior DBs (LOTUS/KNApSAcK/NPSS), literature (PubMed), LLM | `elucidation.rank_candidates`, `taxonomy.*`, `taxon_prior.build`, `literature.*`, `isolation.load_record`, `chromatography.apply_layers`, `metabolomics.*`, `llm.explain` — all fakes | any of 1H/13C/HSQC/MS2 | None claimed |
| `novelty_scan` | UNSUPPORTED (422) | MOCK-ONLY: novelty generation is a no-op bookkeeping node; experiment proposal is a canned LLM text | No real implementation exists at any layer; would require structure enumeration service + reviewed evidence rules | `sirius.predict_formula`, `shift_mpnn.predict`, `llm.propose_experiments` | any of 1H/13C/HSQC/MS2 | None claimed |
| `build_network` | UNSUPPORTED (422) | MOCK-ONLY: fake GNPS job id and canned network | GNPS2 API access (key or explicit public allowance), real FBMN submission, real HSQC networking | `gnps.submit_job`, `gnps.fetch_results`, `nmr_networking.build` | HSQC or MS2 | None claimed |
| `mechanism_hypothesis` | UNSUPPORTED (422) | MOCK-ONLY: fake NMR DB hits, fake PubMed hits, canned hypothesis text; QA gate runs rule-based checks over the canned text | Real NMR peak search (NP-MRD/BMRB/nmrshiftdb2 rights+access), real literature, real LLM with budget | `nmr_search.peaks`, `literature.search`, `llm.hypothesize`, `qa.verify_grounding` | HSQC | None claimed |
| `compare_extracts` | UNSUPPORTED (422) | MOCK-ONLY: ANOVA/FDR over fixture arrays, not user feature tables | Real feature-table ingestion and statistics wiring | `io.parse_peak_csv`, `stats.anova`, `stats.fdr_correction` | none (instrument-agnostic) | None claimed |

Enforcement points: real-mode workflow rejection happens in
`services/orchestrator/app/main.py` (`submit_analysis`) **before** the run is
stored or any provider call is made; the engine additionally preflights every
planned tool against the executor's supported set; `MockToolExecutor.preflight`
refuses `reference_hsqc.rank` in mock mode. Both directions are covered by
`tests/test_real_orchestrator.py` (`test_unsupported_real_workflow_422_before_provider`,
`test_health_offline_science_and_auth_modes_are_independent`).

## Real capabilities outside the `/analysis` workflow surface

These are separate service routes with their own acceptance evidence; they do
not make the seven workflows above real:

| Capability | Route / layer | Status | Evidence |
|---|---|---|---|
| LC–MS feature detection (MZmine 4.11 pinned) | `POST /metabolomics/detect` | REAL, measured acceptance runs passed (M1-a: 1,388 features on MSV000090047 tetracycline; M1-b: vendor-native ThermoRawFileParser chain, 1,926 features) | `data/validated_runs/M1a`, `M1b` run summaries; ADR-0086/0087. Precursor-mass+RT matches are **pending MS/MS confirmation**, not identifications. |
| MS/MS library comparison | offline assessment scripts | REAL once-over on the two acceptance cases vs MassBank references (partial-to-moderate agreement, MSI ≤2 territory) | `data/validated_runs/msms_assessment/` reports; confirmation pending |
| Elucidation candidate retrieval | `/elucidate/candidates` (elucidation service) | REAL against a configured curated CSV library (`PHYTO_STRUCTURE_DB_PATH`); shift prediction stays mock unless a Vertex endpoint is configured (fail-closed otherwise) | ADR-0081/0078; vertical test `tests/test_elucidation/test_elucidation_main.py` |
| Taxonomy / taxon prior / literature / GNPS / Neo4j KG | individual services | Real clients exist behind fail-closed config gates; unconfigured ⇒ explicit unavailability (503), never mock science | ADR-0083; runtime-mode tests per service |

## Rules for claims

1. Never describe a MOCK-ONLY workflow as executed, validated, or available in
   real mode; frontend labels must say offline/demo.
2. `reference_hsqc_identify` output is dereplication *support* with mandatory
   human review; `evidence_assessment` dispositions are engineering defaults,
   not validated decision rules.
3. No accuracy numbers may be quoted for any workflow; the scientific
   benchmark denominator remains zero (see `benchmark/blinded_pilot/PROTOCOL.md`).
4. M1-a/M1-b are precursor-mass + retention-time matches **pending MS/MS
   confirmation**, never identifications.
