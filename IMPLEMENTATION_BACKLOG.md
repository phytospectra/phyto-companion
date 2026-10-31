# Implementation Backlog

Version: 2 — 2026-10-31. Derived from the enterprise handoff brief
(`../ENTERPRISE_AGENT_HANDOFF.md`), the 2026-10-31 status report, Phase 0
verification, and the multi-vendor LC–MS/NMR discovery prompt
(`../MULTIVENDOR_LCMS_NMR_DISCOVERY_PROMPT.md`). Ordered by priority; each item
must end with its acceptance test and evidence in `AGENT_STATUS.md`.

## Done

- **[P0-1A] Fail-closed runtime mode selection, elucidation (ADR-0078)** —
  `ElucidationConfig`, startup validation, 503 on unconfigured capabilities,
  health reports mode. Evidence: 11 + 16 tests, suite 156.
- **[P1B] §13 HMBC coordinate reconciliation (ADR-0079)** — `H-11a→C-4a` and
  `H-6eq→C-4a` re-pinned at the measured 157.2 carbon with new frozen baselines;
  duplicated 66.5 row removed. 18/18 regression tests. Findings 2/3 verified
  (manuscript FID paragraph rewritten; superseded-report banner placed).
- **[P1C] LLM judge model-identity provenance (ADR-0080)** — every `llm_judge`
  verdict carries the judge's identity; `services/llm` already compliant.
- **[P1D] Stale metadata sweep (findings 2, 3, 8)** — manuscript FID paragraph,
  Word-report superseded banner, README Vertex row. Remaining finding-8 item:
  paper's archived-DOI placeholder stays until a release exists (owner-gated).
- **[Phase 2] FileStructureDB provider + vertical (ADR-0081)** — curated CSV
  library, fail-closed loading, end-to-end vertical on a 2-molecule library.
- **[Phase 3 first slice] Auth 401 contract (finding 6) + FE `msi_level`
  mirror (finding 7)** — 33 service-health tests + structural guard; FE types/
  fixtures/contract pinned; backend 1556, ruff clean; vitest not runnable in
  this environment (worker-spawn limitation, `HANDOFF_repo_health.md`).
- **[ADR-0082] Honest provenance defaults** — mock-library candidates `synthetic`
  (were `experimental`); undeclared FileStructureDB rows `structure_only`.
- **[ADR-0083] Fail-closed startup validation extended** — metabolomics
  (`MZMINE_HOME`), networking (authorized GNPS route), pharmacology
  (`NEO4J_URI`; real mode explicitly unavailable until Step 8). `create_app`
  validates eagerly in all four services with real dependencies. Full suite
  1591, ruff clean.
- **[ADR-0085] MZmine batch contract implemented and validated to the CLI login gate** — the 11-step XML batch generator is pinned to MZmine 4.11/4.10.90/4.10.6 and derived from upstream CI fixtures; every 4.x CLI needs an authenticated user (`MZMINE_USER_FILE`), so the first feature-table run is blocked on the user creating an `.mzuser4` profile (agent cannot do this)
  until validated against a real installation (register row says so).

## Next (M0/M1 of the multi-vendor prompt — priority order)

1. **[M0-a] M0 deliverables** (this branch): `ANALYTICAL_CAPABILITY_MATRIX.md`
   (feature-level rows vs the reference families), `FORMAT_SUPPORT_MATRIX.md`
   (vendor/format/version/fixture/status), `DATABASE_CONNECTOR_REGISTRY.md` +
   machine-readable `data/connector_registry.json` (rights/access per source),
   runtime trace, canonical schema proposals, this backlog. Status:
   **created on the agent branch** — see the M0 section of `AGENT_STATUS.md`.
2. **[M0-b] Reference-dataset shortlist** — identify redistributable open
   mzML fixtures (e.g. from MassIVE/MetaboLights with verified terms) and
   owner-provided private test datasets **without publishing them**; record
   the access route and license for each. Gate: rights must be verified per
   dataset before download; nothing unpublished leaves the workspace.
3. **[M1-a] MZmine live-integration contract** (implemented; blocked on the MZmine user profile):
   install/locate a real MZmine (`MZMINE_HOME`), verify its version, emit the
   XML batch from `DetectParams`, run one small real mzML file, capture the
   actual CSV-export schema, and pin a live integration test skipped when
   `MZMINE_HOME` is absent. Acceptance: the live test passes once, recorded
   with version + config + output contract; CI stays green without the binary.
4. **[M1-b] Vendor-conversion path survey** — pick the first validated
   vendor-conversion route (ProteoWizard/msconvert worker vs vendor export)
   for one vendor family; qualification per §4 of the prompt (fixtures,
   negative cases, conversion-loss report). Worker isolation rules per the
   enterprise brief.
5. **[P2-remainder] Real-mode vertical on a real exported feature table** —
   run lcms_preproc → statistics on a licensed real table, recorded with
   provenance; fixture outputs stay excluded from scientific claims.

## Phase 3 remainder (enterprise security/ops)

6. Verify deployed auth is real (finding 6) — needs live environment access;
   Stripe webhook signature contract test.
7. Frontend vitest run on a working worker-spawn machine; then FE evidence
   badges per §10 of the multi-vendor prompt (imported/processed,
   predicted/experimental, candidate/identified, real/demo).

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
- Never expose keys or unpublished datasets in logs, prompts, PRs, fixtures,
  or public artifacts.
- Separate measured evidence / retrieved references / deterministic inference /
  model predictions / LLM hypotheses.
- Fixture/demo output is labeled and excluded from scientific performance
  claims; record model/database identity and version in every relevant result.
- Keep local work lightweight: no big DB loads, GPU training, large spectral
  jobs, or credential-dependent production calls in ordinary unit tests.
- Never change expected numbers simply to make tests pass.
