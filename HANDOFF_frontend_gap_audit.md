# Handoff: Frontend gap audit vs. post-creation backend features

**Date:** 2026-10-22 · **Read-only audit — nothing fixed, nothing committed, nothing pushed.**

Audit question: the backend gained the isolation MVP (ADR-0055), data governance (ADR-0056), six chromatography evidence layers (ADR-0061–0066), `msi_level` chain-summary reporting, layer skip reasons, `SampleMetadata` extensions, and governance fields — **after** `frontend/src` was last meaningfully touched. Does the frontend handle any of it?

**TL;DR: No.** The frontend compiles and passes its 21 (now 25 with the 4 audit tests) tests, but it renders none of the new backend features: no isolation evidence, no chromatography layers, no MSI level, no skip reasons, no governance. The result panel is fed from hard-coded mock fixtures (`frontend/src/lib/phyto/fixtures.ts`), not from run payloads. The evidence-panel *graph widget* can display the new relations — but only when someone hand-converts the payload; nothing does this in production code.

---

## 1. Baseline

The frontend is a **nested git repo** (`frontend/.git` exists; the parent repo ignores it — commit `f3a537a` "Ignore nested frontend repo and its bundle artifacts"). The parent repo has **no tracked `frontend/` history** (`git log -- frontend/` is empty).

| | |
|---|---|
| **Last meaningful frontend commit** | `8adca9a105a2b8addb3350a609fe6fd63590e27d` — "feat(analysis): TanStack Query run polling, checkpoint gating, pause/cancel" — **2026-10-22 03:18:38 +07:00** (last commit touching `frontend/src/`; it is also `frontend/HEAD`, clean worktree) |
| **Backend commits AFTER that timestamp** | `475253d` (taxonomy-aware prior + literature reranking, 12:04), `7cb8fab` (tool-count tests, 12:29), `2cdedcf` (ADR-0054 addendum, 12:31), **`db0ce69` isolation MVP (12:41)**, **`4977a8a` ADR-0056 governance (14:30)**, **`da8fe33` chromatography layers ADR-0061–0066 + repo audit tool (16:26)** |

Everything in the audit scope (isolation MVP, governance, chromatography layers, msi_level, skip reasons, SampleMetadata extensions) landed in `db0ce69`, `4977a8a`, `da8fe33` — after the frontend snapshot. The frontend **is** tracked in its own nested repo, so the audit proceeded (per instruction).

## 2. Evidence artifacts (new, untracked)

| Artifact | What it is |
|---|---|
| `frontend/src/__fixtures__/rich_identify_response.json` (42 KB) | Captured from the real backend local plane via a throwaway script (`/tmp/capture_rich_identify.py`, not in the repo): rich identify dry-run through `TestClient(create_app())` with every layer's metadata (LC method, reference standard, observed RT 9.9 min, flavonol PDA trace, purity 0.95), a seeded isolation record (`EXT-2024-042`, fractions + TLC + yield + ABS `clear`), and taxon metadata. Contains the `POST /analysis` response (summary with `isolation_layer`, `chromatography_layers` incl. `top_candidate_msi_level` and skip reasons), `GET /runs/{id}`, the citation, all task outputs, and the **real** `layer_chain.apply_chromatography_layers` output over the elucidate ranks (the full `CandidateRank` field surface). Note: in the dry-run the layers task runs *before* elucidate and receives 0 ranks — the fixture's `layer_chain_over_ranks` section supplies the rank-level data. |
| `frontend/tests/unit/result-panel.gap-audit.test.tsx` (4 tests) | Audit artifact — **keep it**. Renders the production `ResultsPage` (as shipped) and an `EvidencePanel` hand-fed the rich payload's top rank, snapshots both, and snapshots a feature-visibility matrix. |
| `frontend/tests/unit/__snapshots__/result-panel.gap-audit.test.tsx.snap` | The rendered evidence. |

All 25 frontend tests pass (21 pre-existing + 4 audit). Backend `tests/test_orchestrator_chromatography.py`: 5 passed (used as the capture harness reference).

## 3. Gap table

Legend: **In FE type?** = field exists in `frontend/src/api/types.ts` or `frontend/src/lib/phyto/types.ts` (no zod schema for API responses; `upload-schema.ts` is form-only). **Rendered?** = appears in the production result panel / evidence panel DOM. **Breaks?** = does the field arriving cause errors or silently wrong UI?

### 3a. Response-model fields absent from FE types

| Backend feature | API field / endpoint | In FE type? | Rendered? | Edge-case handled? | Breaks? | Severity | Suggested action |
|---|---|---|---|---|---|---|---|
| Isolation MVP evidence | `CandidateRank.isolation_evidence` (list[EvidenceItem]), `isolation_bonus` — `shared/phyto_shared/models.py:458-471` | No | No | n/a | No (ignored) | **major** | fix-now: add to `CandidateRank` type + isolation evidence section in EvidencePanel |
| Chromatography layers, per-rank | `level_1_match`, `rt_offset_applied`, `uv_evidence` (UVClass), `uv_bonus`, `rt_prediction`, `rt_bonus`, `ri_used`, `purity_score`, `classification_chain`, `evidence_chain` — `models.py:476-529` | No | No | n/a | No | **major** | fix-now: type + a "chromatographic evidence chain" section (ordered by `classification_chain`) |
| Modality profile | `CandidateRank.evidence_profile` (evaluated/unavailable/families/is_single_family/gap) — `models.py:380-411` | No | No | n/a | No | minor | defer (pre-frontend-baseline feature, but also untyped) |
| Taxon prior | `taxon_prior_match`, `taxon_prior_provenance` — `models.py:445-457` | No | No | n/a | No | minor | defer with isolation section work |
| Governance on results | `data_tier`/`abs_status` on Spectrum + ProvenanceManifest (`models.py:183-197`, `models.py:668-677`); `IsolationRecord.collection_country/collection_permit/abs_status` (`shared/phyto_shared/isolation.py:223-240`); `DataTier`/`ABSStatus` literals `data_governance.py:55,65` | No | No | n/a | No | minor | defer: display-only provenance metadata |
| Chain summary MSI level | `summary.chromatography_layers.top_candidate_msi_level` (int 1|2) — `services/orchestrator/app/main.py:549-575` | No | No | Level-2 fallback semantics ("no Level-1 ≠ no match") undocumented in UI | No | **major** | fix-now: MSI badge on top candidate; must render level 2 as attribution, not absence |
| Layer skip reasons | `summary.chromatography_layers.skipped[] {layer, reason}`, `summary.isolation_layer` string — `main.py:531-547, 549-575`; `LayerChainResult.skipped_layers` (`services/orchestrator/app/layer_chain.py:44-50`) | No | No | Skipped-layer strings ("layer skipped: no metadata (…)") never surfaced | No | **major** | fix-now: honest-reporting requirement — skips are meant to be visible |
| Layers applied list | `chromatography_layers.applied[]` | No | No | n/a | No | major | fix-now (same section) |
| Run summary extras | `summary.pruned_tasks`, `evidence_gap`, `modalities`, `cost_estimate_usd`, `wall_seconds`, `task_counts` — `main.py:626-650` | No | No | n/a | No | minor | defer (mostly analysis-page material) |
| **`GET /runs/{run_id}` shape mismatch** | Backend `RunStatusResponse` (`main.py:140-169`: `tasks: TaskSummary[]`, `pruned_tasks`, `evidence_gap`, `modalities`, `citation`, **no `audit_tail`, no `model_version`, no `cost_so_far`, no `artifacts`, no `verdict`, no `task_graph`) vs FE `RunState` (`api/types.ts:115-127`) | Yes — but **wrong** | n/a | n/a | **Yes — pre-existing, not caused by the new features: the FE `RunState` mirrors an older contract, not `RunStatusResponse`** | **major (pre-existing)** | fix-now during any typing work: re-derive `RunState` from `RunStatusResponse` |
| `SampleMetadata` extensions | `observed_rt_min`, `observed_uv_spectrum`, `lc_method`, `reference_standards`, `anchor_series`, `extract_id` — `shared/phyto_shared/schemas.py:107-173` (request side) | No (upload zod has species/plant_part/solvent only — `frontend/src/lib/phyto/upload-schema.ts:12-75`; no genus/family/extract_id/RT/UV/LC) | n/a (input side) | Users cannot supply the metadata that unlocks every chromatography layer | Layers silently unavailable to UI users | **blocker for feature reachability** | fix-now: extend MetadataForm + upload schema |
| FE `CandidateRank.margin` typed as `number` | Backend `margin: float | None` and can be `inf` (`models.py:427-429`); FE `margin: number` (`api/types.ts:57`) and `ResultsPage` does `(top.margin * 100).toFixed(0)` — `ResultsPage.tsx:102` | Yes (narrower) | Yes | `null`/`Infinity` would render `NaN%` / `Infinity%` | Potential, not exercised by fixture (margin is finite there) | minor | fix with the retyping pass |
| FE `tied_candidates: number` | Backend `tied_candidates: list[str]` (`models.py:431-433`) | Yes — **wrong type** | No | n/a | Type-level only | minor | fix with retyping |
| FE `Candidate.formula: string` | Backend `formula: MolecularFormula` (object with `counts`) (`models.py:365-371`) | Yes — **wrong type** | Yes (`{top.candidate.formula}` renders `[object Object]` if backend shape ever flows through) | Mock data uses strings so it doesn't fire today | Latent | minor | fix with retyping |

### 3b. Endpoints

Frontend API client surface: `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/checkpoint|pause|cancel` (`frontend/src/api/runs.ts:44-64`) + MSW mock handlers for `/analysis`, `/tools`, `/billing/*`, `/networks/*`, `/elucidate/*` (`frontend/src/mocks/handlers.ts`).

| Backend route (services/) | Called by FE? | Note | Severity |
|---|---|---|---|
| `POST /analysis` — `services/orchestrator/app/main.py:596` | Mock handler only (`handlers.ts:469`); **no real client wrapper** | The one endpoint that returns the new summary fields | major |
| 6 isolation endpoints: `POST /isolation/records` (`main.py:204`), `GET /isolation/records` (`:226`), `GET /isolation/records/{id}` (`:232`), `PUT /isolation/records/{id}` (`:241`), `POST /isolation/records/{id}/compounds` (`:277`), `GET /isolation/records/{id}/compounds/{compound_id}/evidence` (`:377`) — `services/isolation/app/main.py` | **No** | No UI exists to create/view bench isolation records; `CompoundEvidenceSummary` (`isolation/app/main.py:357-373`) is documented as "the payload the frontend evidence panel renders" — nothing renders it | **major** |
| `GET /runs/{id}/citation`, `POST /runs/{id}/share`, `DELETE /runs/{id}/share`, `GET /share/{token}` — `main.py:748-860` | No (mock handler for citation only) | Citation exists in FE types but no UI section | minor |
| All other service routes (metabolomics, nmr_search, literature, llm, retention, calibration, networking, pharmacology, taxon_prior, taxonomy, elucidation, auth/billing/workspace) | Mostly mock-handler only | Frontend is phase-1; out of audit scope | — |

### 3c. EvidenceItem relation names (what the UI might need to render)

Complete distinct set emitted in `services/` + `shared/` (grep `relation=` / `relation =`):

| Relation | Emitted by | Rendered by FE? |
|---|---|---|
| `matched:{modality}` / `matched:mock` | `candidate_ranker.py:473` | Only in EvidencePanel graph *widget* (generic node claim text) |
| `unexplained_observed_peak`, `unexplained_predicted_peak`, `formula_mismatch`, `ms2_scoring_disabled` | `candidate_ranker.py` | Panel has dedicated "Unmatched observed/predicted" sub-sections, but they're hand-fed props, never fed from payload |
| `taxon_prior`, `reported_in_species_paper` | `taxonomy_reranker.py` | No |
| `isolation_polarity`, `isolation_tlc_class`, `isolation_flash_uv` | `isolation_reranker.py:294-350` | No |
| `uv`, `uv_chromophore_match`, `uv_chromophore_mismatch`, `uv_chromophore_neutral` | `uv_reranker.py:124-130` | No |
| `rt_prediction_close`, `rt_prediction_far`, `rt_prediction_neutral` | `rt_reranker.py:71-75` | No |
| `level_1_reference` | `layer_chain.py` | No |
| `pharmacopoeial_marker_match`, `pharmacopoeial_marker_miss`, `pharmacopoeial_marker_miss_penalty`, `pharmacopoeial_markers` | `layer_chain.py` | No |
| `peak_purity_override` | `layer_chain.py` | No |
| `cross_modal_propagation` | `networking/fused_network.py` | Panel has a "Cross-modal propagation" section (hand-fed prop) |
| `site_improvement` | `site_localization.py` | No |
| `supports_novelty_indirectly` | `evidence_graph.py:137` | No |
| `feature_signature_match`, `similarity_to_known_ligand`, `targets_via_{method}` | `pharmacology/target_prediction.py`, `network_pharmacology.py:237` | No |

The FE `EvidenceEdge.relation` type only allows `"supports" | "contradicts" | "gap"` (`api/types.ts:28-32`), and `EvidenceItem.relation` is an unconstrained string on the backend — the rich-payload relations flow through fine **only** because the audit test maps them into node claims. No production code path does that.

## 4. The snapshot

Four snapshots in `frontend/tests/unit/__snapshots__/result-panel.gap-audit.test.tsx.snap`:

1. **`matches the production ResultsPage snapshot (mock-fed, as shipped) 1`** (~950 lines, div tree): page header "Assembled report · run run_4a7e9c2b", export buttons, sample-summary card (hard-coded *Camellia sinensis / leaf / EtOH 7:3*), top-candidate card (mock quercetin; Score/Coverage/Margin stats; gaps list), mechanism card, recommended experiments, provenance manifest (mock audit tail). **No reference anywhere to isolation, layers, MSI, purity, UV, RT, taxon, or governance** — confirmed by the matrix below.
2. **`renders the rich payload's top-rank evidence through the existing EvidencePanel 1`**: hand-fed rich payload through `EvidencePanel`. It renders and looks right: title "isoquercitrin — evidence", subtitle `score 0.6175 · chain rt → uv → purity → pharmacopoeial_markers`, TENTATIVE VerdictBadge, "Evidence graph with 10 claims and 9 relations", "Supporting features (9)" with each relation's raw claim text visible in the graph node labels, e.g.:
   - `rt_prediction_far: predicted 3.00 min vs observed 9.90 min (source retention_model, strength 0.25)`
   - `uv_chromophore_match: observed flavonol (confidence 0.95); candidate classes: flavonol, flavone`
   - `pharmacopoeial_marker_match: marker curcumin: RT in range=False, UV in range=True (expected RT 14.5-15.8 min)`
   - `isolation_polarity: elutes in F2, F3 … -> polar; candidate predicted polar -- consistent with`
   i.e., the relations arrive as **raw machine strings** ("isolation_polarity: …", "matched:mock: …") rather than human phrasing — because the panel just prints `relation: spectral_feature`.
3. **`backend-feature-visibility-matrix 1`** — the audit matrix, all 12 features `rendered: false` (reproduced in §5).
4. **`summary-lines-absence 1`** — same ResultsPage tree; asserts `isolation_layer` / `top_candidate_msi_level` strings appear nowhere in it.

## 5. "What a user actually sees"

A user completing a rich identify run — species + extract + LC method + standards + UV + RT + purity all supplied, isolation record on file, all four chromatography layers applied, isolation reranker applied, taxon prior built — opens the result panel and sees **none of it**:

- **Visible:** the sample summary (from mocks, not their run), the top candidate's score/coverage/margin/verdict, the spectrum viewer, gaps, the (mock) mechanism hypothesis, recommended experiments, and a provenance manifest listing mock audit events. Everything shown is `frontend/src/lib/phyto/fixtures.ts` content, not the run's data.
- **Absent:** the isolation evidence (3 EvidenceItems: polarity tier consistency, TLC class match, flash-UV λmax) and `isolation_bonus` that moved their candidate's score by **+0.4625**; the entire chromatography chain (`classification_chain: rt → uv → purity → pharmacopoeial_markers`, RT prediction −0.25, UV bonus +0.2, marker evidence ×3); the MSI attribution level; the purity score that gates AMBIGUOUS; every layer skip reason. The score the user sees may silently include priors they cannot see or undo — the exact "explainable, undoable priors" contract ADR-0055/0061–0066 promise the report will honor.
- **Raw/confusing if wired naively:** even after someone feeds the payload to `EvidencePanel`, relations render as machine strings (`rt_prediction_far: predicted 3.00 min …`), `matched:mock` fixture noise included, with no grouping by layer, no bonus/penalty sign display, and no Level-1 vs Level-2 distinction.
- **Reachability blocker:** the upload form collects none of the chromatography metadata (`observed_rt_min`, UV trace, LC method, standards, anchors) nor `extract_id`, so a UI user can never trigger the layers at all; the run summary strings (`isolation_layer: "applied"`, `chromatography_layers: {applied, skipped, top_candidate_msi_level}`) are returned by `POST /analysis` but no real client wrapper for `POST /analysis` even exists in `src/api/`.

## 6. Known-deferred list (not new discoveries)

- **Isolation evidence section in the frontend** — ADR-0055 explicitly defers the full isolation graph ("sub-fractionation chains as first-class edges, TLC image upload, cross-lab comparison … deferred until this MVP is validated", `shared/phyto_shared/isolation.py:31-32`); the isolation service already models `CompoundEvidenceSummary` as "the payload the frontend evidence panel renders alongside UV/RT/taxon evidence" (`services/isolation/app/main.py:352-357`) — that payload's consumer remains unwritten.
- **Marker data display** — the marker-miss semantics and threshold-basis notes are documented in `REVIEW_chromatography_layers.md` / `HANDOFF_chromatography_layers.md` as backend-side follow-ups; the report-line rendering (`summary.chromatography_layers` including "no Level-1 must never read as no match", `main.py:556-562`) is a documented reporting contract the FE has never picked up.
- The frontend itself declares its phase-1 scope (mock-fed screens; `ResultsPage` consumes fixtures by design, `ResultsPage.tsx:13,22-23`), so "panel renders mocks" is a known phase boundary, not a regression.

## 7. Audit limitations

- `ResultsPage` takes no data props, so the rich payload could not be piped into the production component without modifying it (forbidden). The audit therefore renders it as-shipped (snapshot 1 + matrix) and separately hand-feeds `EvidencePanel` (snapshot 2) to prove the widget-side renderability and expose the raw-string problem.
- The dry-run pipeline's layers task receives 0 ranks (runs before elucidate); the fixture's `layer_chain_over_ranks` section runs the identical backend function over the elucidate ranks to expose the full per-rank field surface. The orchestrator summary fields are genuine `POST /analysis` output.
- The `GET /runs/{run_id}` shape mismatch (FE `RunState` vs backend `RunStatusResponse`) predates the audited commits; it is flagged as pre-existing, not attributed to the isolation/chromatography work.

**Stop.** No fixes applied; fix scope is a separate decision.