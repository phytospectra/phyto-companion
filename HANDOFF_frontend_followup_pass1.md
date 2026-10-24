# HANDOFF — Frontend follow-up pass 1 (type-contract fixes + guardrails)

**Date:** 2026-10-22 · **Follows:** `HANDOFF_frontend_gap_audit.md` · **Repo:** phyto-companion

## 0. What pass 1 did (and did not do)

The gap audit's §3 "fix with the retyping pass" rows were executed, plus the
guardrail items that make the rest of the pass-2 work safe. **No page-level
feature work was done** — the isolation evidence section, chromatography chain
section, MSI badge, skip-reason surfacing, upload-schema extension, and
`RunState` re-derivation are all pass-2 (charter in §4). Read-only files were
edited only where the retyping pass required it.

### New/changed files

| File | Change |
|---|---|
| `frontend/src/lib/phyto/types.ts` | `CandidateRank.tied_candidates: string[]` (was `number`); `Candidate.formula: { counts: Record<string, number> }` (was `string`); `CandidateRank.margin: number \| null` (was `number`) |
| `frontend/src/lib/phyto/formatting.ts` | Added `formatMargin` (null→"—", ±∞→"∞", finite→"N%"), `formatFormula` (MolecularFormula→Hill-notation string, matching the backend `__str__`: C, H, then alphabetical), `formulaMatches` (search helper over the Hill string). `formatRankInterval` counts `tied_candidates.length` |
| `frontend/src/components/phyto/ResultsPage.tsx` | Margin/Coverage/Formula stats now use the formatters (was `(top.margin * 100).toFixed(0)` → `NaN%` on null) |
| `frontend/src/components/phyto/elucidation/ElucidationPage.tsx` | Same formatter wiring for its rank table/stats |
| `frontend/src/components/phyto/common/UncertaintyBadge.tsx` | Tie count from array length; small-margin warning only for a **finite** margin below 5% (null/∞ mean "no distinct competitor", not "small margin") |
| `frontend/tests/unit/contract-type-fixes.test.tsx` | 13 tests pinning all three type fixes incl. edge cases (empty tie list, Hill order, null/∞ margin, no-NaN assertions) |
| `frontend/tests/unit/result-panel.gap-audit.test.tsx` | Fixed a stray `//` comment that sat in JSX children position and rendered as literal text (snapshot mismatch); moved above the `render` call |
| `scripts/check_fe_contract.py` | NEW. BE↔FE contract check (see §2) |
| `scripts/repo_health_check.sh` | Section 7 now runs `check_fe_contract.py` first (old awk heuristic kept as fallback when the script is absent) |
| `scripts/fixtures/capture_rich_identify.py` | NEW. The gap-audit capture script, rescued from `/tmp`, with the regeneration recipe documented in its docstring |

## 1. Verification status

- `npx vitest run` — **all green**: 9 files, 38 tests (was 25 pre-audit, then
  25+4 audit, now 38 incl. the 13 type-fix tests; one pre-existing audit
  snapshot had to be regenerated after the JSX-comment fix).
- `tsc --noEmit` — clean after the retyping (the FE touched all three fields
  only through the new formatters).
- `scripts/check_fe_contract.py` — passes; negative-tested by temporarily
  regressing `tied_candidates` back to `number` (the pin caught it).
- Fixture regeneration (§3) — structurally identical modulo nondeterministic
  fields; checked-in fixture untouched (sha256 `4210d2a4…e86a31b`).

## 2. Contract check (`scripts/check_fe_contract.py`)

What it enforces (stdlib-only, no pydantic import — line-scans
`shared/phyto_shared/models.py` vs `frontend/src/lib/phyto/types.ts`):

1. **Field parity** for `Candidate` and `CandidateRank`. Currently:
   `Candidate` 7/8 mirrored (`evidence_graph` allowlisted — backend-optional,
   FE renders evidence at rank level); `CandidateRank` 9/24 mirrored — the 15
   deferred fields are the gap-audit §3 majors/minors and are the **pass-2
   charter** (see `ALLOWED_FE_GAPS` in the script). When a pass-2 page lands
   a field, move it out of the allowlist and the checker enforces it.
2. **Pinned type shapes** — the three pass-1 fixes are signature-pinned so a
   regression fails the health check:
   - `tied_candidates: string[]` (backend `list[str]` of candidate ids)
   - `formula: { counts: Record<string, number> }` (backend `MolecularFormula`)
   - `margin: number | null` (backend `float | None`, may be `inf`)

Wire-in: `scripts/repo_health_check.sh` section 7 runs it automatically (it
picks up `$PY`; section 4's `check_*.py` glob also runs it standalone).
Known remaining type mismatch NOT covered by this script (backend models
beyond `Candidate`/`CandidateRank` are unmapped): the pre-existing
`RunState` vs `RunStatusResponse` shape mismatch flagged in the gap audit
§3a — still pass-2.

## 3. Fixture regeneration (rescued capture script)

`scripts/fixtures/capture_rich_identify.py` reproduces
`frontend/src/__fixtures__/rich_identify_response.json`:

```bash
PYTHONPATH="shared:build/auth-path:services/orchestrator/app" \
  .venv/bin/python scripts/fixtures/capture_rich_identify.py \
  frontend/src/__fixtures__/rich_identify_response.json
```

Verified 2026-10-22: output is **structurally identical** to the checked-in
fixture except inherently nondeterministic fields (`run_id`,
`provenance_manifest_id`, task `created_at`, `wall_seconds`, manifest/citation
hashes). Two gotchas documented in the script docstring:

- **`services/orchestrator/app` must be on PYTHONPATH** — otherwise
  `_mock_apply_chromatography_layers` can't import `layer_chain` and silently
  degrades to "layer chain not staged in this plane" (all 5 layers skipped
  instead of 4 applied).
- **Byte-for-byte equality is not achievable** (ids/hashes/timings); compare
  with the deterministic-diff approach in §1 of the script docstring instead.

## 4. Pass-2 charter (next session's work, in priority order)

Everything below unblocks the user-facing features the gap audit §5 showed a
user never sees. Do NOT widen `ALLOWED_FE_GAPS` without landing the
corresponding UI.

### P0 — reachability & honesty
1. **Upload metadata form** (`frontend/src/lib/phyto/upload-schema.ts:12-75`,
   MetadataForm component): add `genus`, `family`, `extract_id`,
   `observed_rt_min`, `observed_uv_spectrum`, `lc_method`,
   `reference_standards`, `anchor_series` (backend `SampleMetadata`,
   `shared/phyto_shared/schemas.py:107-173`). Without this a UI user can
   never trigger the chromatography layers at all (audit "blocker for
   feature reachability").
2. **Real `POST /analysis` client wrapper** (`src/api/`): the one endpoint
   returning `isolation_layer`, `chromatography_layers.applied/skipped`,
   `top_candidate_msi_level`. MSW handler already exists (`handlers.ts:469`).
3. **Re-derive `RunState` from backend `RunStatusResponse`**
   (`services/orchestrator/app/main.py:140-169`) — pre-existing major
   mismatch; `AnalysisPage` runs on it today only because the MSW handler
   returns the old FE shape.

### P1 — result-panel evidence surfacing
4. **Isolation evidence section** in `EvidencePanel` (props:
   `isolation_evidence`, `isolation_bonus`) — ADR-0055.
5. **Chromatographic evidence chain section** ordered by
   `classification_chain`, rendering `evidence_chain` items with
   bonus/penalty signs, plus the skip-reason list (honest-reporting
   contract: a skipped layer must be visible, and "no Level-1" must never
   read as "no match").
6. **MSI attribution badge** (`top_candidate_msi_level`): render level 2 as
   attribution, not absence.
7. **Relation-string presentation layer**: map raw machine relations
   (audit §3c table) to human phrasing + per-layer grouping. Keep the raw
   string available (tooltip/aria) for provenance.

### P2 — deferred with documented reasons
8. `evidence_profile` (modality profile), taxon prior display, governance
   badges (`data_tier`/`abs_status`), citation section, summary extras
   (`pruned_tasks`, `evidence_gap`, …). Each corresponds to a row the audit
   marked minor/defer.

## 5. MSW coverage — what a page-level test can use today

MSW's node server is wired into **all** unit tests via `tests/setup.ts`
(`server.listen` with `onUnhandledFrame: "error"`), and handlers exist for 47
routes. But the four untested pages do **not** go through MSW — they call the
in-process `mockApi` (`src/lib/phyto/mock-api.ts`), which returns fixtures
directly and never issues an HTTP request. Only `src/api/*` (the real axios
client) hits MSW.

### Page → data-source → test-status table

| Page | File | Data source | MSW handlers involved | Unit test today |
|---|---|---|---|---|
| Network | `src/components/phyto/network/NetworkPage.tsx` | `mockApi.getNetwork` (in-process fixtures) | None reached. Handlers for `POST /networks/*`, `GET /networks/ms2/:job`, exports exist but unreachable from this page | **None** |
| Elucidation | `src/components/phyto/elucidation/ElucidationPage.tsx` | `mockApi.rankCandidates / generateNovel / localizeSite` | None reached (`POST /elucidate/*` handlers unreachable) | **None** |
| Pharmacology | `src/components/phyto/mechanism/PharmacologyPage.tsx` | `mockApi.hypothesizeMechanism / explainHypothesis` | None reached (`POST /pharmacology/*` handlers unreachable) | **None** |
| Billing | `src/components/phyto/billing/BillingPage.tsx` | `mockApi.topup / confirmTopup / listTransactions` | None reached (`GET/POST /billing/*` handlers unreachable) | **None** |
| Analysis | `src/components/phyto/analysis/AnalysisPage.tsx` | `runsApi` (real client) → **MSW** `POST /runs`, `GET /runs/:id`, checkpoint/pause/cancel | Yes — the only page fully exercising MSW | `AnalysisPage.test.tsx` (MSW-fed) |
| Upload | `UploadPage.tsx` | form-only (`upload-schema.ts`), no fetch | n/a | `UploadPage.test.tsx` |
| Login | `LoginPage.tsx` | firebase auth, not HTTP | n/a | `LoginPage.test.tsx` |
| Results | `ResultsPage.tsx` | imports `MOCK_*` fixtures directly | n/a | covered indirectly by gap-audit tests |
| Settings | `SettingsPage.tsx` | local display prefs only, no API | n/a | None |

### Consequences for pass 2

- A page test for Network/Elucidation/Pharmacology/Billing written against
  MSW will **fail on unhandled-request** until those pages are migrated from
  `mockApi` to the real `src/api` client (`onUnhandledFrame: "error"` makes
  the mismatch loud). Migration order should follow the pass-2 charter:
  analysis-side pages first (P0.2), then the read-only pages.
- Conversely, testing those four pages as-is only exercises
  `mockApi`+`fixtures.ts` — it cannot catch contract drift. `check_fe_contract.py`
  plus the `rich_identify_response.json` fixture are the drift guard until
  the migration happens.
- `resetMockState()` (`handlers.ts:87`) resets MSW-side in-memory state
  between tests; the `mockApi` side has its own idempotency cache that is
  NOT reset by it — relevant when writing tests that re-run metered calls.

## 6. Stopping point

All pass-1 items done and verified; nothing left running or half-applied.
Pass 2 should start with the charter §4 P0 items, keeping
`scripts/check_fe_contract.py` green and moving allowlisted fields into real
FE types as their UI lands.