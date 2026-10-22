# Review report: chromatographic and UV/PDA evidence layers

**Date:** 2026-10-22 · **Status: NOT COMMITTED, NOT PUSHED — for review.**
Full test suite: **1247 passed** (previously 1241; 93 new tests, 0 failures).
Decisions recorded as ADR-0061…ADR-0066 in `docs/DECISIONS.md`; data
provenance added as sections 9–10 of `docs/DATA_SOURCES.md` (renumbered from 8–9 on 2026-10-22 — see the addendum at the end of this file).
*Canonical repository audit (git state, hygiene, tests, frontend, CI policy, uncommitted-file inventory): `HANDOFF_repo_health.md` — supersedes this file's superseded "1247" counts.*

---

## 1. What was built

Six chromatographic evidence layers that run ADDITIVELY over the already-
ranked identify-workflow candidates. The candidate ranker's scoring is
untouched: every layer is a post-ranking reranker or evidence sticher, and
every score movement carries a named `EvidenceItem` in `evidence_chain` so
it is visible and undoable. The chain runs in pipeline order:

```
taxonomy prior → Level-1 home library → RT/RI → UV chromophore →
peak purity → pharmacopoeial markers
```

| Layer | ADR | What it does | Needs | Skips (visibly) when |
|---|---|---|---|---|
| Level-1 home library | 0061 | Matches observed peak against the user's own authentic standards (RT ±0.1 min, UV cosine > 0.95, MS2 cosine > 0.85); MSI Level-1 stamp; computes RT offset calibration | reference standards | none stored |
| RT prediction + reranking | 0063 | Predicts RT under the user's LC method (offset-calibrated), penalizes > tolerance (−0.25), rewards close (+0.15); NEVER excludes | lc_method + observed RT | either missing |
| RI normalization | 0064 | With ≥2 co-injected anchors, compares retention indices instead of RTs (portable across methods) | anchor series | <2 anchors (falls back to RT) |
| UV chromophore | 0062 | Classifies the PDA spectrum into 13 classes (Mabry/Harborne rules); +0.20 class match / −0.30 directional contradiction; <0.6 confidence = no-op | observed UV spectrum | missing or unclassifiable |
| Peak purity | 0065 | UV-drift + m/z-alignment score; **below 0.5 forces AMBIGUOUS** with a caveat, with provenance | purity inputs | missing |
| Pharmacopoeial markers | 0066 | Compares observed RT/UV against 26 species' monograph QC markers; weak +0.05 support, miss is evidence against the species claim | species + observed RT | no curated markers for the species |

### New files

**Shared domain (`shared/phyto_shared/`)**

- `chromatography.py` (modified, +25 lines) — `LCMethod`, `RetentionPrediction`, `ReferenceStandard`, `AnchorStandard`, `AnchorCalibration`, `uv_cosine`, `ms2_cosine`, `rt_to_ri`, `ri_to_rt`. No default LC method on purpose.
- `uv_chromophore.py` (new, ~470 lines) — rule-based classifier, 13 classes, confidence + diagnosis notes; `predict_class_from_smiles` structural counterpart.
- `peak_purity.py` (new) — 0-1 purity score; UV weighted 0.7, MS alignment 0.3; missing channels = 0.8 "unconfirmed".
- `models.py` — `CandidateRank` gains `level_1_match`, `rt_offset_applied`, `uv_class_observed`, `rt_delta`, `ri_used`, `rt_adjustment`, `purity_score`, `classification_chain`, `evidence_chain`.
- `schemas.py` — `SampleMetadata` gains `lc_method`, `reference_standards`, `anchor_series`, `observed_rt_min`, `observed_uv_spectrum` (all optional; absent metadata = skipped layer, never a guessed one).

**Services**

- `services/calibration/` (new service) — home-library store keyed (user, method_id); `POST /calibrate/match`; protocol-based persistence (in-process local, Firestore cloud); **method isolation is structural**.
- `services/retention/` (new service) — `predict_rt` with per-prediction `method_id`; pilot = hand-curated 20-compound lookup + logP baseline with wide interval; METLIN SMRT lineage documented.
- `services/elucidation/app/uv_reranker.py` (new) — +0.20 / −0.30, convention-aware, compatibility whitelist.
- `services/elucidation/app/rt_reranker.py` (new) — soft ±0.25/+0.15; RI path replaces RT path when anchors exist.
- `services/taxon_prior/app/markers.py` (new) — marker loading/evaluation with grace bands (RT +0.5 min, UV ±15 nm).
- `services/orchestrator/app/layer_chain.py` (new) — `apply_chromatography_layers`: the six-layer executor with per-layer skip reporting.
- `services/orchestrator` — registry spec for `chromatography.apply_layers`, executor wiring, planner task `apply-chromatography-layers` (downstream of `elucidate`), dry-run summary reporting applied/skipped layers.

**Data**

- `data/pharmacopoeial_markers.json` (new) — 26 species, per-entry pharmacopoeial reference; relative to the documented `REFERENCE_GRADIENT` (30-min 5→95% C18).

**Decisions & data docs**

- `docs/DECISIONS.md` — ADR-0061…ADR-0066 appended (context / decision / alternatives / consequences, house format).
- `docs/DATA_SOURCES.md` — §8 pharmacopoeial marker data (licence-verify-at-refresh note); §9 METLIN SMRT lineage (not committed; pilot stand-ins documented).

### ADR numbering reconciliation

The shared modules originally cited the pre-integration draft numbers
(0055–0060). All code references now use the final numbering (0061–0066)
and agree with DECISIONS.md; the fixed files were re-tested (33 passed).

## 2. Tests

93 new tests across 9 files (full suite 1247 passed, 0 failed):

| File | Tests | Covers |
|---|---|---|
| `tests/test_chromatography_models.py` | 15 | LCMethod validation, gradient sanity, RI/RT round-trips, cosine similarities |
| `tests/test_uv_chromophore.py` | 12 | class profiles, confidence gating, SMILES prediction |
| `tests/test_peak_purity.py` | 6 | score weights, MS alignment, caveat |
| `tests/test_calibration_service.py` | 10 | Level-1 tolerances, per-user/per-method isolation, RT offset |
| `tests/test_retention_service.py` | 6 | method scoping, lookup vs baseline, intervals |
| `tests/test_elucidation/test_chrom_rerankers.py` | 13 | UV/RT/RI rerankers: bonuses, penalties, conventions, no-ops |
| `tests/test_pharmacopoeial_markers.py` | 11 | loading, invalid-entry dropping, RT/UV checks, grace bands |
| `tests/test_layer_chain.py` | 15 | chain order, skip reporting, purity override, marker evidence |
| `tests/test_orchestrator_chromatography.py` | 5 | planner wiring, dry-run summary, no-metadata path |

One test-order flakiness was observed earlier in `tests/test_auth`. The
original diagnosis ("module caches mid-edit") was wrong: the real cause
was ambient environment leakage. Several service modules build their
FastAPI app at import time (`app = create_app()`), baking
`AuthConfig.from_env()` into a module-level singleton; no fixture can
reach that, because pytest fixtures run after collection. A stray
`PHYTO_ENV=cloud` in the invoking shell therefore crashed collection with
8 errors (real Firestore/Firebase clients constructed at import), and a
stray `PHYTO_AUTH_ENABLED=1` made 116 endpoint tests fail with 401s.
Fixed by pinning the auth-relevant environment variables at conftest
import time (`tests/conftest.py`), before the service-package aliasing
executes any service module; the per-test `_local_plane_env` fixture
still handles everything fixture-scoped. Verified: the full suite passes
with `PHYTO_ENV=cloud`, with `PHYTO_AUTH_ENABLED=1` +
`FIREBASE_CREDENTIALS_PATH`, and with all four variables combined set to
hostile values (1281 passed in every case).

## 3. Design invariants worth checking in review

1. **Additivity.** No reranker touches `candidate_ranker`'s scoring; every
   adjustment is a separate, named field + `EvidenceItem`. Grep the three
   rerankers for `score` writes to verify.
2. **Skipped ≠ silent.** Every layer that cannot run reports
   `"layer skipped: <reason>"` in the summary. A skipped layer never folds
   into the ranking.
3. **Purity forces AMBIGUOUS.** <0.5 flips the verdict on every rank with a
   caveat appended to the evidence graph — an action with provenance, not a
   hidden penalty.
4. **Markers are expectations.** +0.05 max support; a miss is evidence
   against the species claim, never proof of identity.
5. **Method scoping.** Standards and predictions are keyed per (user,
   method); no cross-method leakage by construction.

## 4. Known limitations (honesty items, deliberately not hidden)

- The RT lookup is 20 literature-typical compounds; predictions are
  indicative until a fine-tuned model replaces it (lineage documented in
  ADR-0063 and DATA_SOURCES.md §10).
- The UV classifier is calibrated on flavonoid/coumarin literature;
  alkaloid classes are coarse, and "terpene-like" really means "no useful
  UV chromophore".
- Marker RT windows assume the reference gradient; other methods get only
  the ±0.5 min grace band.
- Pharmacopoeial monographs are copyrighted: the committed file stores
  factual marker data with per-entry attribution; re-verify licences at
  each refresh (DATA_SOURCES.md §9).

## 5. Not done (out of scope / deferred)

- Purity deconvolution (spectral unmixing, MS/MS apex matching) behind the
  same score field — deferred (ADR-0065).
- Fine-tuned RT model — data sparsity (ADR-0063).
- Pharmacopoeial marker data expansion beyond the 26 species — data
  refresh, not a code change.

---

## Addendum (2026-10-22, later session): ADR-0056 data governance foundation

**Full suite: 1281 passed, 0 failed** (up from 1247; the governance
increment adds 36 tests, see below). Committed as `4977a8a` ("ADR-0056:
data governance foundation (tiers, ABS, curator promotion)"); a follow-up
staged (uncommitted) change set adds the traceability tests, the
DATA_SOURCES renumbering, the auth-test-isolation hardening and the
threshold-basis notes.

### What was built (data governance, ADR-0056)

- `shared/phyto_shared/data_governance.py` — `DataTier` (public CC0 /
  proprietary / training_only), `ABSStatus` (clear / pending / exempt /
  unknown), `is_tier_compatible_with_abs`, `is_source_allowed_in_tier`,
  `NON_COMMERCIAL_SOURCES` (np_mrd, np_mrd_fixtures, nmrshiftdb2),
  `SOURCE_DATASET_IDS`. Everything defaults to the most restrictive
  setting: nothing becomes public or licensable by accident.
- Governance labels on every contribution: `IsolationRecord`
  (collection_country ISO-3166, collection_permit, abs_status),
  `IsolationCompound.data_tier`, `Spectrum` /
  `SpectrumUploadRequest` / ingestion `PeakCSVRequest` (data_tier,
  abs_status, source_dataset), `ProvenanceManifest` (data_tier,
  abs_status, source_datasets — hashed into manifest_hash).
- Enforcement: `ProvenanceBuilder.build()` rejects proprietary-tier (and
  cloud commercial-plane) analyses whose sources are non-commercial
  (NP-MRD CC-BY-NC in particular), logging the offending source + tier.
  The isolation service's write endpoints enforce the tier/ABS matrix
  with 422 + fixed messages, curator/admin-only promotion via a
  server-side role store (`services/auth/app/roles.py`), and every
  accepted AND rejected tier change lands in the hash-chained audit log.
- Docs: `docs/DATA_GOVERNANCE.md` (technical + legal-requirements
  document; the §5 checklist is the lawyer's brief for the ToS — the ToS
  itself is NOT written and needs a lawyer), `docs/DATA_SOURCES.md`
  governance records for every source, ADR-0056 appended to
  `docs/DECISIONS.md`.

### Tests added (36 in tests/test_data_governance/)

| File | Tests | Covers |
|---|---|---|
| `test_tier_abs_compatibility.py` | 6 | tier/ABS matrix, purity, unknown tier raises |
| `test_source_tier_whitelist.py` | 4 | NP-MRD never proprietary; whitelist ↔ doc consistency |
| `test_provenance_enforcement.py` | 6 | pass/fail cases, cloud plane, tier-label tamper detection |
| `test_isolation_validator.py` | 7 | each 422 rejection (exact messages), happy paths per tier |
| `test_curator_promotion.py` | 9 | role store + endpoint pipeline + rejected-promotion audit logging |
| `test_data_sources_completeness.py` | 4 | CI check: every source string in code has a DATA_SOURCES.md row |

Also added by a concurrent session (not re-counted here): the ADR
numbering check (`docs/adr/INDEX.md`, `scripts/check_adr_numbers.py`,
`tests/test_docs/test_adr_numbers.py`).

### Changes after the `4977a8a` commit (staged / unstaged, for review)

- `tests/test_data_governance/test_curator_promotion.py` — +2 tests:
  rejected promotions are audited with `accepted: false` and the audit
  chain verifies.
- `tests/test_data_governance/test_provenance_enforcement.py` — +1 test:
  tampering with `data_tier` / `abs_status` / `source_datasets` on a
  stored manifest breaks `manifest_hash` and `verify_manifest` raises.
- `docs/DATA_SOURCES.md` — fixed the duplicate "## 8." heading (sections
  renumbered 8 → 8/9, old 9 → 10); cross-references in this file and in
  `data_governance.py` updated to match.
- `tests/conftest.py` — auth-test isolation: an autouse fixture pins
  `PHYTO_ENV=local` and clears `FIREBASE_CREDENTIALS_PATH` for every
  test unless it opts out via `monkeypatch.setenv`, so no test can
  construct the real Firebase/Firestore clients through shell/CI env
  leakage (verified: `AuthConfig.from_env()` on a clean env resolves to
  `MockFirebaseAuthClient`). A second, deeper layer was added after the
  401-flake was root-caused (see §2's flake paragraph): service modules
  build `app = create_app()` at IMPORT time, so module-level code runs
  before any fixture; the conftest now also pins the auth-relevant env
  (`PHYTO_ENV`, `FIREBASE_CREDENTIALS_PATH`, `PHYTO_AUTH_ENABLED`,
  `PHYTO_DRY_RUN`, `PHYTO_FREE_TOKENS`) at conftest-import time, before
  the service-package aliasing executes. Verified against hostile
  ambient values: `PHYTO_ENV=cloud`, `PHYTO_AUTH_ENABLED=1` +
  `FIREBASE_CREDENTIALS_PATH`, and all four combined — 1281 passed in
  every case.
- Threshold-basis notes (asserted, provisional — recalibrate against
  labelled data): ADR-0061 tolerances in `services/calibration/app/main.py`,
  ADR-0062 confidence gate in `services/elucidation/app/uv_reranker.py`,
  ADR-0065 purity threshold in `shared/phyto_shared/peak_purity.py`.

### ABS country list provenance

The eight ABS countries in `docs/DATA_GOVERNANCE.md` §3 (Thailand, India,
Brazil, South Africa, Peru, Malaysia, Indonesia, Vietnam) were
agent-generated from general knowledge of Nagoya-Protocol implementations
and are NOT verified against primary sources. They are a scoping aid for
the lawyer, not legal advice; each entry needs verification against the
current national law before the ToS relies on it.
