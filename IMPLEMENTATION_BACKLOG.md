# Implementation Backlog

Version: 1 — 2026-10-31. Derived from the enterprise handoff brief
(`../ENTERPRISE_AGENT_HANDOFF.md`), the 2026-10-31 status report, and Phase 0
verification. Ordered by priority; each item must end with its acceptance test
and evidence in `AGENT_STATUS.md`.

## Done

- **[P0-1A] Fail-closed runtime mode selection (ADR-0078)** — `ElucidationConfig`
  with startup validation; real mode requires `VERTEX_ENDPOINT_ID` +
  `GOOGLE_CLOUD_PROJECT` and refuses to start; unconfigured structure-DB
  capability answers 503; factory refuses mock fallback in real mode; health
  reports `predictor_mode`/`structure_db_available`. Evidence:
  `tests/test_elucidation/test_runtime_modes.py` (11), `test_shift_predictor.py`
  (16), full elucidation suite 156 passed.

## Next (Phase 1B and high-value verification)

1. **[P1B] Reconcile the §13 NMR reference case** — audit HMBC regression labels
   against actual coordinates (brief finding 4: e.g. `H-11a→C-4a` shares
   66.5 ppm with `H-11a→C-6`); resolve Word-report COSY/HMBC disagreement
   (finding 3); verify the manuscript's "FIDs corrupt" wording (finding 2).
   **Do not change expected numbers to make tests pass** — discrepancies are
   documented, then corrected with a dated ADR if the *source* was wrong.
2. **[P1C] LLM provider inventory** — extend finding 5 verification into a
   test asserting every provider-backed endpoint tags model id + version in its
   response provenance.
3. **[P1D] Stale metadata sweep** (finding 8) — README, paper metadata, release
   metadata vs. current repo state.

## Phase 2 (production vertical)

4. Wire a real structure-DB provider behind `_Runtime.db` (Step 6+); contract
   test + 503 removal when configured.
5. One end-to-end vertical: ingest → preprocess → elucidate → QA critic →
   report, with recorded provenance and labeled fixture output excluded from
   scientific claims.

## Phase 3 (enterprise security/ops)

6. Verify deployed auth is real (finding 6) — health endpoint evidence, not
   assumption; Stripe webhook contract test.
7. Frontend evidence-field contract tests (finding 7).

## Phase 4 (NMR reasoning agent)

8. 4A: agent with two explicit modes (evidence interpretation; bounded
   hypothesis search), LLM text may not rewrite observed values, scores, or
   provenance.
9. 4B: expert-reviewed dataset (gated: NMRGym email request, NMRArena access
   request; **no contact or upload without explicit user authorization**).
10. 4C: model comparison harness; 4D: fine-tuning decision; 4E: discovery
    extension.

## Phase 5 (release)

11. Release checklist — do not make the backend public merely because an old
    manuscript checklist requests it; repo-publication requires explicit
    authorization.

## Standing rules (from the brief)

- No silent mock fallback in production; missing required config = clear
  startup/capability failure; optional unavailable features explicitly
  unavailable.
- Never expose keys or unpublished datasets in logs, prompts, PRs, fixtures, or
  public artifacts.
- Separate measured evidence / retrieved references / deterministic inference /
  model predictions / LLM hypotheses.
- Fixture/demo output is labeled and excluded from scientific performance
  claims; record model/database identity and version in every relevant result.
- Keep local work lightweight: no big DB loads, GPU training, large spectral
  jobs, or credential-dependent production calls in ordinary unit tests.
- Never change expected numbers simply to make tests pass.
