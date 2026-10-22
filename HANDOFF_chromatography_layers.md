# Handoff: chromatography layers follow-up (post-review fixes)

**Date:** 2026-10-22 · **Full suite: 1281 passed, 0 failed.**
*Canonical repository audit (git state, hygiene, tests, frontend, CI policy, uncommitted-file inventory): `HANDOFF_repo_health.md`.*

## Repository state (read this first)

- Commit `4977a8a` ("ADR-0056: data governance foundation (tiers, ABS,
  curator promotion)") EXISTS on `main`, authored 2026-10-22 by Subhadip
  Banerjee, and is **unpushed**: `main` is ahead of `origin/main` by
  exactly 1 commit (`origin/main` is still at `f3a537a`).
- On top of `4977a8a` the working tree carries **uncommitted changes**:
  15 modified files (chromatography layer fixes, threshold-basis notes,
  the conftest env pin, review-report updates) plus untracked
  `docs/adr/`, `scripts/check_adr_numbers.py`, `tests/test_docs/` and
  this handoff note. Nothing is staged, nothing is committed, nothing is
  pushed. Do not assume the working tree matches `4977a8a` — it does
  not; the marker-miss semantics and the flake fix described below exist
  only as uncommitted edits.
- No commit or push has been made in this pass; that decision stays with
  the reviewer.

## Test-count reconciliation (the "1281" arithmetic, with evidence)

An earlier draft of this note floated "1148 + 34 = 1281"; that arithmetic
is wrong — 1148 + 34 is 1182, and 1148 appears in no measurement. The
verified chain, each number measured with `pytest --collect-only` (worktree
checkouts for the historical ones):

1. **Baseline** — parent commit `f3a537a`, clean worktree: **1114** tests.
2. **Commit `4977a8a` adds 160 tests** across 16 new files (verified by
   `git diff f3a537a 4977a8a -- tests/` plus per-file collection):
   - 93 chromatography-session tests (ADR-0061..0066): calibration 10,
     chromatography models 15, chrom rerankers 13, layer_chain 15,
     orchestrator_chromatography 5, peak_purity 6, pharmacopoeial
     markers 11, retention 6, uv_chromophore 12.
   - 34 isolation-MVP tests (ADR-0055, `test_isolation_mvp.py`).
   - 33 governance tests (ADR-0056, `tests/test_data_governance/`).
   → **1274** at the commit. The "governance measured 1274" figure from
   the prior session was this commit-level total; the isolation-MVP 34
   was already inside it, not on top of it.
3. **Untracked working-tree additions**: +2 (`tests/test_docs/
   test_adr_numbers.py`, the ADR-numbering CI test — untracked, so not in
   the commit).
4. **Uncommitted test edits**: +5 (layer_chain +2, curator_promotion +2,
   provenance_enforcement +1 — the marker-miss, rejected-promotion-audit
   and manifest-tamper tests).

1274 + 2 + 5 = **1281**, which is exactly what the full suite collects
and passes today. Note the review report's internal "1241 → 1247; 93 new
tests" line cannot be reconstructed from any single tree state — it was
measured on a working tree that already mixed in concurrent-session
files — and is superseded by this reconciliation.

This note covers the follow-up work done AFTER
`REVIEW_chromatography_layers.md` was written, in response to its review
items. Read that report first for what the six chromatography layers
(ADR-0061..0066) are and how they're wired.

## What was done in this pass

### 1. ADR numbering safety
`docs/adr/INDEX.md` is now the single registry of ADR numbers;
`scripts/check_adr_numbers.py` fails on a duplicate or missing number and
`tests/test_docs/test_adr_numbers.py` runs it in CI. Current state: check
passes, ADR-0061..0066 are the only new numbers.

### 2. Marker-miss semantics corrected (ADR-0066)
The first implementation treated a pharmacopoeial marker miss as strongly
as a hit. Fixed in `services/orchestrator/app/layer_chain.py` and
`services/taxon_prior/app/markers.py`: a miss now carries strength 0.0
with relation `pharmacopoeial_marker_miss` (vs. 0.05 support for a hit),
each marker item carries `expected` and the monograph reference in its
`source_spectrum`, and the chemotype caveat (markers are quality-control
expectations, NOT identity proof) is in the ADR and the evidence text.
Tests in `tests/test_layer_chain.py` and
`tests/test_pharmacopoeial_markers.py`.

### 3. Threshold basis documented
The assertive-but-provisional thresholds now each say in code what they
are: ADR-0061 Level-1 tolerances (±0.1 min RT, UV cosine > 0.95, MS2
cosine > 0.85) in `services/calibration/app/main.py`, ADR-0062 confidence
gate (0.6) in `services/elucidation/app/uv_reranker.py`, ADR-0065 purity
threshold (0.5) in `shared/phyto_shared/peak_purity.py`. All marked
"asserted, provisional — recalibrate against labelled data". Level-2/3
fallback reporting was added so a run whose Level-1 match fails still
says which lower-confidence level it fell back to.

### 4. Auth test flakiness — root-caused and fixed (was misdiagnosed)
The earlier flake in `tests/test_auth` was NOT stale module caches. Real
cause: **ambient environment leakage into import-time app construction.**
Several service modules execute `app = create_app()` at module import,
which calls `AuthConfig.from_env()` ONCE and bakes the result into a
module-level singleton. Pytest fixtures run per-test, AFTER collection —
so no fixture could protect import-time code. Consequences when the
invoking shell carried hostile values:

- `PHYTO_ENV=cloud` → collection itself crashed with 8 errors (real
  Firestore/Firebase clients constructed at import, neither package
  installed locally).
- `PHYTO_AUTH_ENABLED=1` (+ `FIREBASE_CREDENTIALS_PATH`) → 116 endpoint
  tests failed with 401, because the module-level apps the endpoint tests
  reuse had enforcement baked in.

Fix in `tests/conftest.py`: the auth-relevant variables (`PHYTO_ENV`,
`FIREBASE_CREDENTIALS_PATH`, `PHYTO_AUTH_ENABLED`, `PHYTO_DRY_RUN`,
`PHYTO_FREE_TOKENS`) are pinned at conftest-import time, BEFORE the
`_alias_package` calls execute any service module. The per-test
`_local_plane_env` fixture remains for everything fixture-scoped; tests
that deliberately need another plane opt in via `monkeypatch.setenv` and
build their own app (as `tests/test_auth/service_runner.py` does).

Verified: 1281 passed under `PHYTO_ENV=cloud`; under
`PHYTO_AUTH_ENABLED=1` + `FIREBASE_CREDENTIALS_PATH`; under all four
variables set to hostile values simultaneously; and on a clean shell.
Also verified with `__pycache__` purged and with auth tests moved
first/last, so it is not order-dependent either.

### 5. Marker-miss evidence contract (confirmed, where it lives)

The miss/match/penalty diagnostic relations in `evidence_graph` follow
the contract established by the chromatography session at
`services/orchestrator/app/layer_chain.py:312` (the `_miss_item`
definition, with the house-convention comment at line 313: strength
carries the magnitude, the relation name carries the direction). Concretely:

- `evidence_graph.evidence` carries the diagnostic relations:
  `pharmacopoeial_marker_match` (strength 0.3),
  `pharmacopoeial_marker_miss` (strength 0.0, unflagged marker),
  `pharmacopoeial_marker_miss_penalty` (strength 0.10, `expected`
  marker only — magnitude strictly greater than the 0.05 support bonus,
  asserted at `services/orchestrator/app/layer_chain.py:357`).
- `evidence_chain` keeps only the layer tag:
  relation `pharmacopoeial_markers` (layer_chain.py:381), one item per
  hit, strength echoing the per-item outcome.
- The relation vocabulary originates in `rt_reranker.py`'s
  "strength = |delta|, direction = relation name" convention and is
  extended, not replaced, by the marker layer.

**Consumers that need review if these relations change:** the
`evidence_graph` renderer/audit path in `services/orchestrator/app/main.py`
and any downstream UI reading `relation` strings; `tests/test_layer_chain.py`
(17 tests) and `tests/test_pharmacopoeial_markers.py` (11 tests) pin the
current vocabulary. `services/taxon_prior/app/markers.py` defines the
constants (`MARKER_SUPPORT_BONUS` 0.05, `MARKER_MISS_PENALTY` 0.10,
`MARKER_MISS_PENALTY_UNFLAGGED` 0.0) that layer_chain imports — the two
modules must move together.

### 6. Files changed in this pass (all uncommitted)

- `tests/conftest.py` — import-time env pin (the flake fix).
- `services/orchestrator/app/layer_chain.py`,
  `services/taxon_prior/app/markers.py` — marker-miss semantics.
- `services/calibration/app/main.py`,
  `services/elucidation/app/uv_reranker.py`,
  `shared/phyto_shared/peak_purity.py` — threshold-basis comments.
- `data/pharmacopoeial_markers.json` — marker entries with expected flags.
- `tests/test_layer_chain.py`, `tests/test_orchestrator_chromatography.py`,
  `tests/test_data_governance/test_curator_promotion.py`,
  `tests/test_data_governance/test_provenance_enforcement.py`,
  `tests/test_docs/` — new/updated tests.
- `docs/adr/` (new), `scripts/check_adr_numbers.py` (new),
  `docs/DECISIONS.md`, `docs/DATA_SOURCES.md` — numbering registry,
  threshold-basis ADR sections, DATA_SOURCES renumbering.
- `REVIEW_chromatography_layers.md` — updated (flake diagnosis corrected,
  addendum extended).

### 7. ADR-0041 / ADR-0042 status (closure item)

**Resolution: option (c) — already in place, verified, no change made.**
Both numbers are marked in `docs/adr/INDEX.md` (lines 60–61) as
"skipped, never written — see ADR-0043 section note", status
`reserved (gap)`, and the numbering note under ADR-0043 in
`docs/DECISIONS.md` (revised 2026-10-22) records that the entries begin
at 0043 because 0041/0042 were never written and the append-only log
does not reuse numbers. Why (c) and not (a) or (b): nothing to define —
a repo-wide grep for `ADR-0041|ADR-0042` finds no citation in any code,
test or doc outside the index/DECISIONS gap markers themselves, so there
are no citations to correct (option b's subject) and no recoverable
decision to reconstruct (option a's subject). The state is enforced,
not just documented: `scripts/check_adr_numbers.py` rule 3 fails if any
number cited in code or docs is neither defined nor carries the
`reserved (gap)` marker, and the check passes today.

## What the next session should know

1. **Nothing is committed.** `git status` shows the modified set above on
   top of `4977a8a`. The changes are internally consistent and the suite
   is green; they are left uncommitted because the review report says
   "for review" — commit decision belongs to the reviewer.
2. **Run the suite with `make test`** (or plain `.venv/bin/python -m
   pytest`); the `auth` package needs `make stage-auth` once per clone
   (a symlink into `build/auth-path`, which is git-ignored).
3. **Known limitation kept deliberately:** the RT lookup table is 20
   literature-typical compounds and the pharmacopoeial markers cover 26
   species — both are data-refresh items, not code items.
4. **The ABS country list** in `docs/DATA_GOVERNANCE.md` §3 is
   agent-generated and unverified (see the report's addendum); the ToS
   needs a lawyer regardless.
5. If a similar import-time singleton shows up in a new service, the
   conftest env pin already covers it — but the cleaner long-term fix is
   for services to build the app lazily (FastAPI factory pattern) rather
   than `app = create_app()` at module level. That refactor is NOT done
   here (it touches every service) and is a reasonable next step.

## Test-count reconciliation — independent confirmation (2026-10-22, repo health audit)

`scripts/repo_health_check.sh` ran the audit pass on this tree and the
per-feature collection it measured independently confirms the arithmetic
above: total 1281 (all passing), isolation 34, data_governance 36 on the
worktree (= 33 at `4977a8a` + 3 uncommitted test edits), and the
chromatography-session files at 95 (= 93 at the commit + 2 uncommitted
layer_chain tests) — so 1114 + 160 + 2 + 5 = 1281 reconciles exactly.
