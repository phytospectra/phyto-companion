# Capability Register

Version: 2 — 2026-10-31 (base SHA `c2558a8` + ADR-0082/0083/0084 work on the
agent branch; supersedes v1, which was based on `bff329a`). Owner: implementation
agent (see `AGENT_STATUS.md`). Update this table whenever a capability's mode
changes. Mode vocabulary: **real** (talks to a live provider when configured),
**mock** (deterministic fixture, labeled), **partial**, **unavailable**,
**blocked** (cannot be made real without an external dependency, named).

Evidence tiers (do not merge them): **local tests** = offline pytest evidence;
**live integration** = recorded run against the real external system;
**scientific validation** = measured comparison against reference data with
frozen tolerances; **deployment** = the capability verified in a real
deployment. A capability claims only the tiers it has evidence for.

| Capability | API/UI entrypoint | Implementation | Dependencies | Mode | Local evidence | Live evidence | Scientific validation | Owner | Next acceptance test |
|---|---|---|---|---|---|---|---|---|---|
| Bruker NMR ingestion (DTYPA/XDIM) | `services/ingestion` `/ingest` | `nmr_parser.py` | none | real | `tests/test_single_modality.py` | none recorded | case-study regression reads real archives | platform | round-trip on a 3rd archive |
| NMR preprocessing + consistency gate | `services/nmr_preproc` | `pipeline.py`, `consistency.py`, `twod.py` | none | real | 18 case-study regression tests, CI-gated | none recorded | §13 case-study suite (readers, lipid mask, MSI levels pinned; ADR-0079 coordinates) | platform | held-out sample from archive 2 |
| Shift prediction (candidate ranking) | `services/elucidation` `/elucidate/candidates` | `shift_predictor.py` + `runtime_config.py` (ADR-0078) | Vertex AI endpoint + GCP project in real mode | mock (local) / real (cloud, fail-closed) | `test_runtime_modes.py` (11), `test_shift_predictor.py` (16) | none — real mode never exercised | MockShiftPredictor pinned vs §13 expected shifts | elucidation | one recorded live Vertex prediction with model/version in provenance |
| Candidate retrieval (structure DB) | `services/elucidation` `/elucidate/candidates` | `FileStructureDB` (ADR-0081) + mock fixture | curated CSV via `PHYTO_STRUCTURE_DB_PATH`; none in real mode without it | real (with CSV, either plane) / 503 (unconfigured real mode) / mock (dry-run) | `test_structure_db_provider.py` (13), Phase-2 vertical | none | n/a — fixture labeled `synthetic` (ADR-0082); FileStructureDB provenance per row, undeclared = `structure_only` | elucidation | load the owner's home library CSV; recorded live retrieval |
| Novel-structure proposal (site localization, Hungarian matching) | `/elucidate/novel` | `novel_structure_generator.py`, `site_localization.py` | shift predictor | partial (deterministic only) | elucidation suite (171 total) | none | §13 novel-derivative case | elucidation | regression on §13 derivative |
| QA critic (LLM judge vs §13 rubric) | `services/orchestrator` `qa_critic_agent.py` | `llm_client.py` tiers | LLM provider (Vertex/OpenRouter) | real (requires provider) / dry-run ladder | `tests/test_orchestrator.py` (68) | none recorded | rubric packaged from §13; verdicts carry `model_identity` (ADR-0080) | orchestrator | recorded judge run with model id + version |
| LLM completion service | `services/llm` `/complete` | `clients.py` (Vertex + OpenRouter), `grounding.py`, `tiers.py` | provider keys | real (fail-closed on missing key) | `tests/test_llm/` | none recorded | grounding/citation extraction unit tests | llm | recorded live completion with served model id |
| Literature search (NP-MRD / BMRB / nmrshiftdb2, species search) | `services/nmr_search`, `services/literature` | `clients.py`, `species_search.py` | external HTTP APIs | real (network) / degraded offline | fixture-verified parsers | none recorded | fixture-verified parsers | search | recorded live query with timestamp |
| Calibration (MSI Level 1) | `services/calibration` | `main.py` | none | real | `tests/test_calibration_service.py` | none recorded | pinned calibration curves | calibration | — |
| LC–MS feature-table adapter (processed tables ONLY — not raw processing) | `services/lcms_preproc` | `adapter.py` | none | real (for exported tables) | `tests/test_chromatography_models.py` etc. | none recorded | pinned models; header dialects (MZmine 3 / XCMS / MS-DIAL / long) | lcms | round-trip on a real exported table with preserved units |
| Raw LC–MS processing (detection/alignment/gap-fill) | `services/metabolomics` `/metabolomics/detect` | `MZmineDetector` (ADR-0084) | MZMINE_HOME + real MZmine binary + XML batch + verified export schema | **blocked** — batch contract unverified (CSV ≠ MZmine XML batch); real mode fails closed at startup (ADR-0083) | startup-validation tests only (`test_detector_runtime_modes.py`); no live batch ever run | none | none — must not be claimed | lcms | live MZmine run: XML batch, version recorded, export schema verified against upstream source, small mzML integration test |
| Mock feature detection (local plane) | `/metabolomics/detect` | `MockDetector` | none | mock (labeled) | ground-truth tests in `test_feature_detection.py` | n/a | n/a — synthetic by construction, excluded from scientific claims | metabo | — |
| Metabolomics statistics suite | `services/metabolomics` | `differential_analysis.py`, `opls_regression.py`, … | none | real (on whatever table it is given — input mode is the caller's responsibility) | `tests/test_metabolomics/` | none recorded | pinned statistics | metabo | acceptance on a real exported feature table |
| GNPS networking (MS2 molecular networks) | `services/networking` `/networks/ms2` | `gnps_client.py` + `GNPSJobManager` | authorized GNPS route: `GNPS_API_KEY` or explicit `PHYTO_ALLOW_PUBLIC_GNPS=1` | real (fail-closed, ADR-0083) / mock (dry-run) | `test_gnps_runtime_modes.py` (13), mock-parser fixture tests | none — no real job ever submitted | fixture-verified GraphML parsing only | networking | one recorded real FBMN job on a PUBLIC dataset (no unpublished data), results parsed and reconciled |
| HSQC networking + fused network | `/networks/hsqc`, `/networks/fused` | `nmr_networking.py`, `fused_network.py` | none (synchronous) | real | `tests/test_networking/` | n/a (in-process) | case-study HSQC network | networking | cross-modal link validation (§8 of the multi-vendor prompt) |
| Pharmacology (ADMET, network pharmacology, knowledge graph) | `services/pharmacology` | `admet_prediction.py`, `knowledge_graph.py` | `NEO4J_URI` in real mode; Neo4j backend itself is Step-8 wiring | partial (rule/KB-based) / **real mode explicitly unavailable until Step 8** (ADR-0083) / networkx mock (dry-run) | `test_kg_runtime_modes.py` (11) | none | pinned outputs; DISCLAIMER travels on every response | pharm | Step 8 Neo4j wiring or documented decision to keep dry-run-only |
| Auth (Firebase) | `services/auth` middleware | `install.py` gates on dry_run/enabled | Firebase creds, Firestore | real (cloud) / disabled (local) | `tests/test_auth/` (33 incl. 401 contract for all 7 previously-unguarded services + structural guard) | none recorded; brief finding 6 says production once ran mock auth | n/a | auth | verify deployed `/health` shows `auth_enabled` (needs live environment) |
| Billing (Stripe) | `services/auth/billing_routes.py` | `entitlements.py`, `token_balance.py` | Stripe (manual setup) | partial — brief finding 6: incomplete Stripe | `tests/test_billing/` | none recorded | n/a | billing | webhook signature contract test |
| Orchestrator (planner, sandbox, rate limit, audit log) | `services/orchestrator` | `planner_agent.py`, `sandbox.py`, … | llm service | real/partial | `tests/test_orchestrator*.py`, `test_layer_chain.py` | none recorded | tool-tier violation tests | orchestrator | — |
| Frontend evidence contract | `frontend` (nested repo `d7d7a49`) | UI fields | backend endpoints | partial — `msi_level` mirrored; vitest not runnable in this environment | `tsc --noEmit` clean; `check_fe_contract.py` OK | none | n/a | frontend | vitest run on a machine with a working worker-spawn path |
| ADR governance | `docs/DECISIONS.md` + `docs/adr/INDEX.md` | `scripts/check_adr_numbers.py` | none | real | numbering check OK (ADR-0084 latest) | n/a | n/a | platform | — |

## LLM provider inventory (correction to the survey's "only the QA judge calls an LLM")

Provider-backed completion paths exist in **three** places: (1) orchestrator QA
critic judge, (2) `services/llm` `/complete` endpoint (Vertex tier-1/2 +
OpenRouter tier-3), (3) any orchestrator planner/agent calls routed through
`llm_client.py`. The claim "only the QA judge calls an LLM" is true only of the
elucidation service itself. See `NMR_RESEARCH_REGISTER.md`.

## Startup-validation status (ADR-0078 + ADR-0083)

All four services with real/cloud dependencies now validate at `create_app`
(import time for the container entrypoint), not lazily at first request:
elucidation (`ElucidationConfig`, ADR-0078), metabolomics
(`MetabolomicsConfig`), networking (`NetworkingConfig`), pharmacology
(`PharmacologyConfig`). Remaining lazy-but-harmless runtimes: services whose
only runtime object is deterministic/local (lcms_preproc, calibration, …) have
nothing to fail closed on.
