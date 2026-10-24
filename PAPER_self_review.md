# PAPER_self_review.md — pre-draft checklist

> This file tracks items that MUST be reflected in the paper draft before
> submission. The draft itself is written separately; this checklist is the
> contract between the repository's review artifacts and the manuscript.
> The rest of the self-review checklist will be added when the paper draft
> is written.

## Mock-data and substitution disclosure — required in limitations

Source: `REVIEW_metabolomics_pharmacology.md` (Step 6 owner acceptance,
ADR-0015–0018). The limitations section must state, in substance:

1. Metabolomics statistics (PCA, PLS-DA, random forest, univariate tests) run
   over MZmine-format fixture feature tables with documented ground truth;
   they are not validated on real biological samples.
2. Pathway enrichment runs over a mock KEGG-C-number pathway database; it is
   not validated against the full KEGG.
3. Multi-block fusion runs `pls_da_concat` plus `permutation_test_fusion` —
   DIABLO is not implemented (GPL-compatibility decision, ADR-0018).
4. OPLS-DA is not implemented (patent decision, ADR-0015); PLS-DA is the
   substitute.
5. The pharmacology knowledge graph uses a mock ChEMBL-style database;
   target prediction is Tanimoto-based ligand matching with a documented
   confidence mapping, not a trained model. Docking and ML target prediction
   are not implemented and fail closed by design.
6. ADMET prediction reports `method="unavailable"` — no fabrication.

Framing requirements (from the Step 6 review):

- The OPLS-DA refusal is stated as an **ethical position** ("we do not ship
  patented software to academic users"), not as a limitation.
- The PREDICTED vs REPORTED target distinction, fail-closed
  docking/ML/ADMET behavior, and the `InsufficientEvidenceError` rejection of
  ungrounded mechanism hypotheses are **foregrounded as contributions** in
  the architecture section, not buried as design choices.
