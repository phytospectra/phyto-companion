# Review report: Step 6 — metabolomics and pharmacology services

**Date:** 2026-10-22 · **Status: review artifact — records owner acceptance; not yet committed.**

## 1. Scope

This document records the owner's review and acceptance of the Step 6
(metabolomics + pharmacology) design decisions recorded as **ADR-0015**
(OPLS-DA patent absence), **ADR-0016** (PREDICTED vs REPORTED target edge
discipline), **ADR-0017** (mechanism-hypothesis anti-overreach rules), and
**ADR-0018** (DIABLO GPL absence; documented mocks and fail-closed gaps) in
`docs/DECISIONS.md`. The review question was single: are the documented
substitutions and mock-data bases acceptable as the pilot-stage truth level?
The owner's answer, recorded here, is **yes**. No code or tests were changed
by this review — the substitutions are correct as implemented.

The adjacent chromatography-layers work (ADR-0061–0066) is reviewed separately
in `REVIEW_chromatography_layers.md`; the two documents do not overlap.

## 2. Substitution table

| Decision | What is absent or mocked | Documented substitute | ADR | Test evidence | Accepted | Rationale |
|---|---|---|---|---|---|---|
| No OPLS-DA | OPLS-DA statistic (patented, Sartorius/Trygg & Wold) | PLS-DA with R2Y, Q2, permutation p-values; `OPLS_DA_NOT_IMPLEMENTED` constant explains why | ADR-0015 | `tests/test_metabolomics/test_statistics.py::TestOPLSRegression::test_opls_da_not_in_public_api` | **yes** | Shipping patented software to academic users is a licensing liability the platform refuses on principle |
| No DIABLO | DIABLO multi-block fusion (mixOmics is GPL-3; no permissive Python port) | `pls_da_concat` + `permutation_test_fusion` (statistical "does fusion help" test); `DIABLO_UNAVAILABLE_REASON` | ADR-0018 | `tests/test_metabolomics/test_multi_block_fusion.py::TestDIABLOSubstitute::test_diablo_documented_as_unavailable` | **yes** | GPL vendoring would contaminate the Apache 2.0 release; the substitute preserves the multi-modal evidence claim |
| Mock feature detector | Real feature tables (MZmine export format supported; detector is mocked) | `MockDetector` with published ground truth (elevated / flat internal standard / noise / drift documented in docstring) | ADR-0018 | `tests/test_metabolomics/test_feature_detection.py::test_schema_and_ground_truth`, `::test_ground_truth_elevation`, `::test_drift_feature_increases_with_injection_order` | **yes** | Tests assert against documented truth, not circularly against the implementation |
| Mock pathway database | Full KEGG | Mock KEGG-C-number database with documented feature→compound map; enrichment reports `background_size` + exact test (hypergeometric, BH FDR) | ADR-0018 | `tests/test_metabolomics/test_pathway_enrichment.py::TestEnrichment::test_reports_background_and_test`, `::test_explicit_background_set_used` | **yes** | Honest pilot scope; the statistics are real even though the background is mock |
| PREDICTED vs REPORTED targets | Flat "target" edges (deliberately absent) | Two distinct edge types; predicted requires confidence+method, reported requires citation and rejects method | ADR-0016 | `tests/test_pharmacology/test_knowledge_graph.py::TestEdgeSeparation::test_predicted_and_reported_are_distinct_edge_types`, `::test_reported_rejects_method_field` | **yes** | A prediction must never inherit the authority of a literature report |
| Mechanism hypotheses | Ungrounded LLM narrative (deliberately absent) | `MechanismHypothesis` is `is_hypothesis=True` by construction, requires an `EvidenceGraph`, deterministic text validation, `InsufficientEvidenceError` on violation — rejected, not softened | ADR-0017 | `tests/test_pharmacology/test_network_pharmacology.py::TestMechanismHypothesis::test_llm_text_without_evidenced_target_is_rejected`, `::test_llm_overclaim_rejected`, `TestValidationRules::test_overclaim_detected` | **yes** | A fluent unsupported claim is worse than no claim |
| Docking / ML target prediction | Docking binary and trained ML model (not available locally) | Fail closed / return empty with an explicit gap, never a guess | ADR-0018 | `tests/test_pharmacology/test_target_prediction.py::TestDocking::test_missing_binary_fails_closed`, `TestML::test_returns_empty_not_guess` | **yes** | Explicit gaps are auditable; guesses are not |
| ADMET | ADMET model (not available) | `method="unavailable"` with mandated limitations text | ADR-0018 | `tests/test_pharmacology/test_admet_prediction.py::test_returns_unavailable_not_a_guess` | **yes** | No fabricated numbers, ever |
| Mock pharmacology knowledge base | ChEMBL itself | ChEMBL-style ligands, target–pathway links, PubMed-format citations (no citation-less predictions) | ADR-0018 | `tests/test_pharmacology/test_knowledge_graph.py`, `test_target_prediction.py::TestSimilarity::test_confidence_follows_documented_scale` | **yes** | Real-format citations keep the evidence plumbing exercisable end-to-end |

No gaps: every substitution listed in ADR-0015–0018 has at least one
committed regression or behavioral test pinning it. No test evidence was
absent at review time.

## 3. Acceptance

Accepted by the owner on [DATE TBD] as the pilot-stage truth level. The
substitutions are documented in ADR-0015, ADR-0016, ADR-0017, and ADR-0018.
The paper's limitations section will disclose the mock-data and substitution
scope (see §4).

## 4. Forward pointer — paper draft

**Mock-data and substitution disclosure is a mandatory item in the paper
draft's limitations section** (cross-reference: paper limitations section —
to be created when the draft is written; the required phrasings are tracked
in `PAPER_self_review.md`). Two framing decisions recorded at this review,
for the draft to honor:

1. The OPLS-DA refusal is an **ethical position** ("we do not ship patented
   software to academic users"), not a limitation.
2. The PREDICTED/REPORTED target distinction, fail-closed docking/ML/ADMET,
   and `InsufficientEvidenceError` rejection of ungrounded mechanism
   hypotheses are **contributions** (the pharmacology analog of the
   chromatography layers' skip-visibility discipline) and belong in the
   paper's architecture section, not buried as design choices.
