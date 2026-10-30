# Capability Register

Version: 1 — 2026-10-31 (base SHA `bff329a`, + ADR-0078 fail-closed runtime, uncommitted at time of writing).
Owner: implementation agent (see `AGENT_STATUS.md`). Update this table whenever a
capability's mode changes. Mode vocabulary: **real** ( talks to a live provider when
configured), **mock** (deterministic fixture, labeled), **partial**, **unavailable**.

| Capability | API/UI entrypoint | Implementation | Dependencies | Mode | Local evidence | Live evidence | Scientific validation | Owner | Next acceptance test |
|---|---|---|---|---|---|---|---|---|---|
| Bruker NMR ingestion (DTYPA/XDIM) | `services/ingestion` `/ingest` | `nmr_parser.py` | none | real | `tests/test_single_modality.py` | none recorded | case-study regression reads real archives | platform | round-trip on a 3rd archive |
| NMR preprocessing + consistency gate | `services/nmr_preproc` | `pipeline.py`, `consistency.py`, `twod.py` | none | real | `tests/test_metabolomics/` (18 case-study regression tests, CI-gated) | none recorded | §13 case-study suite (readers, lipid mask, MSI levels pinned) | platform | held-out sample from archive 2 |
| Shift prediction (candidate ranking) | `services/elucidation` `/elucidate/candidates` | `shift_predictor.py` + `runtime_config.py` (ADR-0078) | Vertex AI endpoint + GCP project in real mode | **mock (local) / real (cloud, fail-closed)** | `tests/test_elucidation/test_runtime_modes.py` (11), `test_shift_predictor.py` (16) | none — real mode never exercised | MockShiftPredictor pinned vs §13 expected shifts | elucidation | one recorded live Vertex prediction with model/version in provenance |
| Candidate retrieval (structure DB) | `services/elucidation` `/elucidate/candidates` | `_Runtime.db` | none configured | **unavailable in real mode** (raises 503); mock fixture (9 mols) in dry-run | `test_runtime_modes.py::TestHealth` | none | n/a (fixture only, excluded from scientific claims) | elucidation | Step 6+ provider wiring + contract test |
| Novel-structure proposal (site localization, Hungarian matching) | `/elucidate/novel` | `novel_structure_generator.py`, `site_localization.py` | shift predictor | partial (deterministic only) | `tests/test_elucidation/` (156 total) | none | §13 novel-derivative case | elucidation | regression on §13 derivative |
| QA critic (LLM judge vs §13 rubric) | `services/orchestrator` `qa_critic_agent.py` | `llm_client.py` tiers | LLM provider (Vertex/OpenRouter) | real (requires provider) / dry-run ladder | `tests/test_orchestrator.py`, `tests/test_llm/` | none recorded | rubric packaged from §13; see ADR for voice rule R7 | orchestrator | recorded judge run with model id + version |
| LLM completion service | `services/llm` `/complete` | `clients.py` (Vertex + OpenRouter), `grounding.py`, `tiers.py` | provider keys | real (fail-closed on missing key) | `tests/test_llm/` | none recorded | grounding/citation extraction unit tests | llm | provider tag/version surfaced in every response (verify) |
| Literature search (NP-MRD / BMRB / nmrshiftdb2, species search) | `services/nmr_search`, `services/literature` | `clients.py`, `species_search.py` | external HTTP APIs | real (network) / degraded offline | `tests/test_nmr_search/`, `tests/test_real_npmrd_fixtures.py`, `tests/test_literature/` | none recorded | fixture-verified parsers | search | recorded live query with timestamp |
| Calibration (MSI Level 1) | `services/calibration` | `main.py` | none | real | `tests/test_calibration_service.py` | none recorded | pinned calibration curves | calibration | — |
| LC-MS preprocessing | `services/lcms_preproc` | `adapter.py` | none | real | `tests/test_chromatography_models.py` etc. | none recorded | pinned models | lcms | — |
| Metabolomics statistics suite | `services/metabolomics` | `differential_analysis.py`, `opls_regression.py`, … | none | real | `tests/test_metabolomics/` | none recorded | pinned statistics | metabo | — |
| Pharmacology (ADMET, network pharmacology) | `services/pharmacology` | `admet_prediction.py`, … | none | partial (rule/KB-based) | `tests/test_pharmacology/` | none recorded | pinned outputs | pharm | — |
| Auth (Firebase) | `services/auth` middleware | `install.py` gates on dry_run/enabled | Firebase creds, Firestore | **real (cloud) / disabled (local)** | `tests/test_auth/` (local plane never constructs clients) | none recorded; brief finding 6 says production once ran mock auth | n/a | auth | verify deployed config health shows `auth_enabled` |
| Billing (Stripe) | `services/auth/billing_routes.py` | `entitlements.py`, `token_balance.py` | Stripe (manual setup) | partial — brief finding 6: incomplete Stripe | `tests/test_billing/` | none recorded | n/a | billing | webhook contract test |
| Orchestrator (planner, sandbox, rate limit, audit log) | `services/orchestrator` | `planner_agent.py`, `sandbox.py`, … | llm service | real/partial | `tests/test_orchestrator*.py`, `test_layer_chain.py` | none recorded | tool-tier violation tests | orchestrator | — |
| Frontend evidence contract | `services/frontend` | UI fields | backend endpoints | partial — brief finding 7: evidence-field gaps | none recorded | none recorded | n/a | frontend | field-by-field contract test vs API schemas |
| ADR governance | `docs/DECISIONS.md` + `docs/adr/INDEX.md` | `scripts/check_adr_numbers.py` | none | real | pytest `test_docs/` green (ADR-0078 OK) | n/a | n/a | platform | — |

## LLM provider inventory (correction to the survey's "only the QA judge calls an LLM")

Provider-backed completion paths exist in **three** places: (1) orchestrator QA
critic judge, (2) `services/llm` `/complete` endpoint (Vertex tier-1/2 +
OpenRouter tier-3), (3) any orchestrator planner/agent calls routed through
`llm_client.py`. The claim "only the QA judge calls an LLM" is true only of the
elucidation service itself. See `NMR_RESEARCH_REGISTER.md`.
