# ADR Index

Generated 2026-10-22 from a `grep` of `ADR-0[0-9][0-9]` across `docs/`,
`shared/`, `services/`, `tests/` and `scripts/` — not from memory. The
authoritative full text of every numbered decision remains in
`docs/DECISIONS.md`; this file is the machine-checked index.

> **Reservation mechanism (decision).** Option **(b): single log with
> reserved-ranges notes.** `docs/DECISIONS.md` stays the one append-only
> ADR log; this index is derived, never hand-edited past the gap table
> below, and re-verified by `scripts/check_adr_numbers.py` (wired into the
> pytest suite as `tests/test_docs/test_adr_numbers.py`). Rationale: the
> log's preamble already declares it append-only, dozens of code comments
> and tests cite its entries, and splitting 56 written entries into
> per-number files would rewrite every existing cross-reference for no
> behavioural gain. The check enforces: (1) no duplicate ADR-number
> definitions, (2) every definition is in this index, (3) every number
> cited in code or docs is either defined or explicitly reserved, and
> (4) every defined ADR is cited somewhere outside its own definition.

| ADR | Title | Status | Primary citing file |
|---|---|---|---|
| ADR-0001 | Project location inside the existing research directory | accepted | `docs/DECISIONS.md` |
| ADR-0002 | License not yet chosen | accepted | `docs/DECISIONS.md` |
| ADR-0003 | Solvent reference table includes all commonly observed carbons, beyond the minimum specified | accepted | `shared/phyto_shared/constants.py` |
| ADR-0004 | Step 2 API interpretations where the spec allowed latitude | accepted | `shared/phyto_shared/models.py` |
| ADR-0005 | `ranking_interval` ranks higher scores as better (spec formula corrected) | accepted | `docs/DECISIONS.md` |
| ADR-0006 | Novelty is a separate axis from confidence | accepted | `shared/phyto_shared/constants.py` |
| ADR-0007 | Explicit dual ranking/margin APIs; no single-convention function | accepted | `services/networking/app/fused_network.py` |
| ADR-0008 | `CandidateRank` carries an explicit `ranking_convention` | accepted | `services/elucidation/app/uv_reranker.py` |
| ADR-0009 | Checkpoint approval doubles as Tier-3 (LLM) authorization | accepted | `docs/DECISIONS.md` |
| ADR-0010 | Step 4 (networking service) design decisions | accepted | `docs/DECISIONS.md` |
| ADR-0011 | Synthetic GNPS fixtures are a known risk; Modified Hungarian decay/derivation documented | accepted | `services/networking/app/nmr_networking.py` |
| ADR-0012 | Cross-modal annotations never chain | accepted | `docs/DECISIONS.md` |
| ADR-0013 | Step 5 (elucidation service) design decisions | accepted | `docs/DECISIONS.md` |
| ADR-0014 | MS2 scoring is reference-spectrum cosine; precursor m/z is a filter | accepted | `services/elucidation/app/candidate_ranker.py` |
| ADR-0015 | OPLS-DA is not implemented (patented) | accepted | `services/metabolomics/app/statistics.py` |
| ADR-0016 | Knowledge graph separates PREDICTED_TARGET from REPORTED_TARGET | accepted | `services/pharmacology/app/knowledge_graph.py` |
| ADR-0017 | Mechanism hypotheses are always labelled, evidenced, and validated | accepted | `docs/DECISIONS.md` |
| ADR-0018 | Step 6 design decisions (fusion substitute, mocks, and gaps) | accepted | `services/elucidation/app/shift_predictor.py` |
| ADR-0019 | (reserved, never written — see DECISIONS.md numbering note) | reserved (gap) | `docs/DECISIONS.md` |
| ADR-0023 | (reserved, never written — see DECISIONS.md numbering note) | reserved (gap) | `docs/DECISIONS.md` |
| ADR-0024 | Public data integration (NP-MRD archive + SUMMIT fixtures) | accepted | `docs/DATA_SOURCES.md` |
| ADR-0025 | SUMMIT reference-implementation artifacts are excluded from the platform | accepted | `docs/DATA_SOURCES.md` |
| ADR-0026 | NP-MRD CC-BY-NC commercial-use prohibition and tier separation | accepted | `services/nmr_search/app/clients.py` |
| ADR-0027 | Benchmark scope caveat -- SUMMIT v4 numbers are a reproduction, not independent validation | accepted | `scripts/analyze_benchmark_context.py` |
| ADR-0028 | Token metering integrated before the frontend to avoid retrofit | accepted | `services/auth/app/__init__.py` |
| ADR-0029 | RAG architecture -- the LLM never answers without retrieved context | accepted | `services/llm/app/main.py` |
| ADR-0030 | Literature source priority and deduplication by DOI then PMID | accepted | `services/literature/app/router.py` |
| ADR-0031 | NMR structure search integrated as a first-class Tier-1 service | accepted | `services/nmr_search/app/clients.py` |
| ADR-0032 | API keys live in Google Secret Manager, never in the image or repo | accepted | `shared/phyto_shared/share.py` |
| ADR-0033 | Token budget -- free tier 50, tiered route prices, refunds on failure | accepted | `tests/test_billing/test_token_pricing.py` |
| ADR-0034 | Hybrid LLM provider strategy -- Vertex AI primary, OpenRouter fallback | accepted | `services/llm/app/clients.py` |
| ADR-0035 | Public product name is Phytospectra; internal name stays phyto-companion | accepted | `docs/DATA_SOURCES.md` |
| ADR-0036 | Benchmark scope caveat -- 92.3% is a reproduction and the localisation baseline is inflated by small candidate sets | accepted | `scripts/analyze_benchmark_context.py` |
| ADR-0037 | Small-pool failures are a molecule-size effect, not rare-formula prediction error | accepted | `scripts/analyze_benchmark_context.py` |
| ADR-0038 | Token system integration -- synchronous reservation plus asynchronous audit | accepted | `tests/test_billing/test_usage_events.py` |
| ADR-0039 | Services install AuthMiddleware via create_app factory | accepted | `tests/test_growth/test_serve_boot.py` |
| ADR-0040 | Mock NMR fixtures are representative, not verified | accepted | `docs/DECISIONS.md` |
| ADR-0041 | (skipped, never written — see ADR-0043 section note) | reserved (gap) | `docs/DECISIONS.md` |
| ADR-0042 | (skipped, never written — see ADR-0043 section note) | reserved (gap) | `docs/DECISIONS.md` |
| ADR-0043 | Every completed analysis mints a citable identifier | accepted | `services/orchestrator/app/main.py` |
| ADR-0044 | Shareable read-only run URLs (with per-IP rate limiting) | accepted | `services/auth/app/middleware.py` |
| ADR-0045 | The guided first story and the monthly allowance | accepted | `services/auth/app/entitlements.py` |
| ADR-0046 | Distilled model routing for the free tier, and a read-through query cache | accepted | `services/auth/app/middleware.py` |
| ADR-0047 | Plan tiers in code, prices in Stripe, and pooled workspaces | accepted | `services/auth/app/billing_routes.py` |
| ADR-0048 | Cohort analytics measure profitability, not just growth | accepted | `services/auth/app/main.py` |
| ADR-0049 | Rate limiting is enforced per IP on unauthenticated share reads | accepted | `tests/test_growth/test_rate_limit.py` |
| ADR-0050 | Single-modality analyses are first-class, and say what they cannot establish | accepted | `services/elucidation/app/candidate_ranker.py` |
| ADR-0051 | Commit discipline for multi-chat work | accepted | `docs/GROWTH_REVIEW.md` |
| ADR-0052 | Taxonomy-aware reranking of structural candidates | accepted | `services/elucidation/app/taxonomy_reranker.py` |
| ADR-0053 | Taxonomy resolution source — GBIF primary, ITIS fallback | accepted | `services/orchestrator/app/tool_registry.py` |
| ADR-0054 | Taxon prior databases — LOTUS, KNApSAcK, NPASS, with per-candidate provenance | accepted | `services/orchestrator/app/tool_registry.py` |
| ADR-0055 | Minimum viable isolation record | accepted | `services/elucidation/app/isolation_reranker.py` |
| ADR-0056 | Public-by-default data tiering with proprietary escalation | accepted | `services/auth/app/roles.py` |
| ADR-0061 | Home-library reference standards — Level-1 matching and RT offset calibration | accepted | `services/calibration/app/main.py` |
| ADR-0062 | UV chromophore classification from PDA spectra | accepted | `services/elucidation/app/uv_reranker.py` |
| ADR-0063 | Retention-time prediction — method-scoped, soft by design | accepted | `services/elucidation/app/rt_reranker.py` |
| ADR-0064 | Retention-index normalization via co-injected anchor series | accepted | `services/elucidation/app/rt_reranker.py` |
| ADR-0065 | Peak purity — co-elution detection forces AMBIGUOUS below threshold | accepted | `shared/phyto_shared/models.py` |
| ADR-0066 | Pharmacopoeial marker prior — species authenticity as expectations, not proof | accepted | `services/orchestrator/app/layer_chain.py` |
| ADR-0068 | NMR preprocessing provenance — every parameter recorded as evidence, implemented in-house | accepted | `shared/phyto_shared/metabolomics.py` |
| ADR-0069 | LC-MS preprocessing adapter policy — adapter only, never a reimplementation | accepted | `services/lcms_preproc/app/main.py` |
| ADR-0070 | O-PLS regression for continuous Y and VIP as first-class evidence | accepted | `shared/phyto_shared/metabolomics.py` |
| ADR-0071 | STOCSY as an evidence layer — correlations are hypotheses, never assignments | accepted | `services/metabolomics/app/stocsy.py` |
| ADR-0072 | Analytical reporting checklist as evidence, with visible gaps | accepted | `shared/phyto_shared/metabolomics.py` |
| ADR-0073 | In-house spectral deconvolution and qNMR region integration as Tier-1 tools | accepted | `services/nmr_preproc/app/deconvolution.py` |
| ADR-0074 | Custom equations via a whitelisted AST evaluator — no eval, ever | accepted | `services/nmr_preproc/app/equations.py` |
| ADR-0075 | Bruker readers honour DTYPA/XDIM, cross-artefact consistency is enforced (R7), MSI levels follow Sumner 2007 | accepted | `services/nmr_preproc/app/consistency.py` |
| ADR-0076 | Lipid-region exclusion as a recorded curation step in the 13C assignment path | accepted | `shared/phyto_shared/lipid_regions.py` |
| ADR-0077 | Web app only — desktop app removed; all six hostnames on .com and .org serve the web app (www.phytospectra.com canonical) | accepted | `docs/DEPLOYMENT_GCP.md` |
| ADR-0077 | Web app only — desktop app removed; all six hostnames on .com and .org serve the web app (www.phytospectra.com canonical) | accepted | `docs/DEPLOYMENT_GCP.md` |
| ADR-0078 | Runtime mode selection is explicit and fail-closed — no silent mock in real mode (`ElucidationConfig`, 503 on unconfigured capabilities) | accepted | `services/elucidation/app/runtime_config.py` |
| ADR-0079 | §13 HMBC C-4a correlations reconciled at 157.2 ppm (were mis-pinned at 66.5 = C-6); measured baselines | accepted | `tests/test_metabolomics/test_case_study_regression.py` |
| ADR-0080 | LLM judge verdicts carry the judge's model identity (`CheckResult.model_identity`, `LLMClient.model_identity`) | accepted | `services/orchestrator/app/qa_critic_agent.py` |
| ADR-0081 | Candidate retrieval is a configurable capability — FileStructureDB CSV library, fail-closed loading | accepted | `services/elucidation/app/structure_db.py` |
