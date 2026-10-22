# HANDOFF: Repository health audit (2026-10-22)

**Script:** `scripts/repo_health_check.sh` (created this pass from the
spec, then patched for findings F1–F5 — see §3; `chmod +x`)
**Runs:** three, all captured below. **Nothing was committed, pushed, stashed, or reset. One authorized two-sentence addition to `HANDOFF_chromatography_layers.md`; no other existing files modified.**

**Important environment note (affects how run 1 should be read):** the
original script invoked bare `python3`, which on this machine resolves to
Homebrew Python 3.14 and has **no pytest installed**. The project's own
interpreter is `.venv` (Python 3.12.14, pytest 9.1.1), created by `make
install`. The first full run therefore reported `pytest collection
FAILED` / `Full suite FAILED` with `No module named pytest`. Runs 1–3
quoted below were executed with `.venv/bin` on `PATH`. No tooling was
installed; the project venv already existed. This is now fixed inside the
script itself by the F2 resolver (see §3) — re-runs pick `.venv/bin/python`
automatically.

---

## 1. Git-state verdict (verbatim from run 2: `--skip-tests --skip-frontend`)

```
═══ 1. GIT STATE ═══
  Branch:  main
  HEAD:    4977a8a
  Remote:  https://github.com/phytospectra/phyto-companion.git
  ✓ Fetched origin
  Upstream: origin/main
  ! 1 unpushed commit(s):
      4977a8a ADR-0056: data governance foundation (tiers, ABS, curator promotion)
  ✓ No unpulled commits
  → 1 commit(s) NOT on origin/main
  ! 16 unstaged modification(s) — NOT committed
  ! 5 untracked file(s) — NOT tracked:
      HANDOFF_chromatography_layers.md
      docs/adr/INDEX.md
      scripts/check_adr_numbers.py
      scripts/repo_health_check.sh
      tests/test_docs/test_adr_numbers.py
  Recent commits:
    4977a8a ADR-0056: data governance foundation (tiers, ABS, curator promotion)
    f3a537a Ignore nested frontend repo and its bundle artifacts
    db0ce69 Add isolation record MVP: fractionation, TLC, yield as priors
    2cdedcf ADR-0054: add production data addendum (snapshot sizes and required full dumps)
    7cb8fab Refactor tool count tests to >= floor; document snapshot production requirement
```

**The three questions:**

- **(a) Commits on the branch not on origin:** exactly **1** — the script's
  own line `→ 1 commit(s) NOT on origin/main`.
- **(b) What is unpushed:** commit `4977a8a` "ADR-0056: data governance
  foundation (tiers, ABS, curator promotion)". That is the entire delta to
  `origin/main` (which sits at `f3a537a`).
- **(c) What is uncommitted:** 0 staged, **16 unstaged modifications**,
  **5 untracked files**. Unstaged (from `git status --short`, same set the
  script counts): `REVIEW_chromatography_layers.md`,
  `data/pharmacopoeial_markers.json`, `docs/DATA_SOURCES.md`,
  `docs/DECISIONS.md`, `services/calibration/app/main.py`,
  `services/elucidation/app/uv_reranker.py`,
  `services/orchestrator/app/layer_chain.py`,
  `services/orchestrator/app/main.py`, `services/taxon_prior/app/markers.py`,
  `shared/phyto_shared/data_governance.py`,
  `shared/phyto_shared/peak_purity.py`, `tests/conftest.py`,
  `tests/test_data_governance/test_curator_promotion.py`,
  `tests/test_data_governance/test_provenance_enforcement.py`,
  `tests/test_layer_chain.py`, `tests/test_orchestrator_chromatography.py`.
  Untracked: the five files listed verbatim above (this report and the
  audit script are themselves part of that set). 0 stashes (no stash
  warning emitted).

**Exit codes of the three runs (original, pre-patch script):** run 1
(full, venv on PATH) → **2**; run 2 (git-state only) → **2**; run 3
(hostile-env `--fast`) → **2**. All three shared the single
`.env.example` failure (see findings F1, now resolved). Run 1 as first
executed with bare `python3` also exited 2, with pytest collection /
full suite / frontend build failures (see findings F2, F3b).
**Post-patch exit codes are in §3.**

---

## 2. Full audit verdict (run 1, `.venv` on PATH — original script), section summaries

*Diff-stat note: `git diff origin/main --stat` shows 61 files / 9289
insertions / 33 deletions (both unpushed commits combined), while the
`960cd34` squash alone shows 22 files / 2242 insertions / 454 deletions.
The 454 are line rewrites relative to `4977a8a`, mostly in files that
commit itself added; measured against `origin/main` those files are pure
additions, so cumulative deletions drop to 33.*

- **Hygiene (§2, original run):** ✓ no committed bytecode/OS artifacts;
  ✗ `.env.example` flagged as "Potential .env tracked" (false positive,
  resolved as F1); ✓ `.gitignore` present (91 lines); no large-file
  warning; "TODO/FIXME/XXX/HACK markers: 2727" in the original run
  (unstable count, resolved as F4 — post-patch value is 6).
- **Dependency (§3):** ✓ pyproject.toml present. No poetry/uv lockfile
  check ran (no `[tool.poetry]`, no `uv.lock` at root — script printed
  nothing further).
- **Check scripts (§4):** ✓ `check_adr_numbers.py passes`;
  ! "No ruff/flake8 available"; mypy not run (not installed / no
  `[tool.mypy]` block). Makefile targets: `help install stage-auth test
  dry-run serve`.
- **Tests (§5):** ✓ "pytest collection: -- Docs:
  https://docs.pytest.org/en/stable/how-to/capture-warnings.html" (the
  summary line the script grabbed is the warnings-docs footer — see F5;
  actual collection is 1281 tests); per-feature lines printed
  (isolation: 2, data_governance: 6, chromatography: 1, layer_chain: 1,
  auth: 7 — these count *files*, not tests, under pytest 9's
  per-file quiet format — see F6); ✓ "Full suite:" with an empty summary
  (see F5) — the suite itself passed.
- **Frontend (§6):** found at `frontend/`; ✓ node_modules installed;
  ✓ tsc --noEmit passes; ✓ lint passes; ! "No test script" in the
  original run (the script matched only `.scripts.test`; this package's
  script is named `test:unit` — resolved by F5, see §3);
  ✓ frontend build succeeds.
- **Contract (§7):** ✓ "FE type covers all BE fields (heuristic)".

**Run 2** (`--skip-tests --skip-frontend`): same §1/§2/§3/§4 results; exit 2.

**Run 3** (`PHYTO_ENV=cloud PHYTO_AUTH_ENABLED=1 … --fast`): identical
git/hygiene/dependency/check-script results; §5 "Tests pass (fast: -x)" ✓;
§6 tsc + lint ✓, build skipped by `--fast`; §7 ✓. **Same single
`.env.example` failure as baseline; no test failures — conftest pin
held.** Exit 2 (same `.env.example` failure).

---

## 3. Findings → resolved in this pass (F1–F5 patched; F3 verified not reproducible; F6–F8 remain open)

**Exit codes after patching:** full run **1**; `--skip-tests
--skip-frontend` **1**; hostile-env `--fast` **1**. All three have
**Failed: 0**. The exit code is 1 (not 0) solely because of the git-state
warnings (1 unpushed commit, 16 unstaged modifications, 6 untracked
files) and "No ruff/flake8 available" — fixing those would require
committing/pushing (forbidden this pass) or installing tooling
(forbidden per audit constraints). An empty-repo-state CI run would exit 0.

| # | Section | Disposition | Description | Patch applied (one line) | Verification evidence |
|---|---------|-------------|-------------|--------------------------|----------------------|
| F1 | 2. Hygiene | ✅ **Resolved** | "Potential .env file tracked: `.env.example`" — false positive; the file is a deliberate placeholder template with `!.env.example` in `.gitignore` | Regex replaced with `(^|/)\.env($|\.(local\|production\|development\|staging\|test\|prod\|dev)(\.|$))` — matches `.env`, `.env.local`, `.env.production`, `.env.development`; skips `.env.example/.template/.sample` | `git ls-files \| grep -E '<new-regex>'` prints nothing on the current tree; sanity check still matches the four intended names |
| F2 | 5. Tests | ✅ **Resolved** | Bare `python3` hit Homebrew 3.14 (no pytest) → collection/full-suite failures | PY resolver added at the top: `.venv/bin/python` → `$VIRTUAL_ENV/bin/python` → `python3`; if the chosen interpreter lacks pytest, exits 2 before any test section with an actionable `make install` message; all `python3` uses now `$PY` | Full run §0 now prints `Python: 3.12.14 (…/.venv/bin/python)`; suite runs without PATH juggling |
| F3 | 5. Tests | ✅ **Resolved** | Doubly-quiet pytest: `pyproject.toml` already sets `addopts = "-q"`, so the script's extra `-q` suppressed the "N passed" summary and mangled collect-only tails | Removed `-q` from both pytest calls (collection and full run) | Full run now prints `✓ pytest collection: 1281 tests collected in 2.40s` and `✓ Full suite: 1281 passed, 12 warnings in 5.69s` |
| F4 | 2. Hygiene | ✅ **Resolved** | TODO-marker count unstable (2752/2727/6): BSD `grep -r` descends `.venv/` and `frontend/.next/`, varying with build artifacts present | Scan replaced with `git grep -nwE 'TODO\|FIXME\|XXX\|HACK' -- '*.py' '*.ts' '*.tsx' '*.js' '*.md'`, which respects `.gitignore` | Count is **6** on the current tree, all in `docs/DECISIONS.md` |
| F5 | 6. Frontend | ✅ **Resolved** | Script matched only `.scripts.test`, so the package's `test:unit` (vitest) never ran; likewise single-name matching for typecheck/lint/build | `first_script()` helper probes aliases in priority order: tests `test, test:unit, test:run, test:ci`; typecheck `typecheck, type-check, type_check, tsc`; lint `lint, lint:ci, eslint`; build `build, build:prod` | Full run now prints `✓ frontend tests pass (test:unit)` — vitest ran from the script, 21/21 passing (7 files) |
| F3b | 6. Frontend | ⚪ **Not reproducible on clean tree — was a concurrent-session artifact** | First pass saw `frontend build FAILED`: `ENOTEMPTY: directory not empty, rmdir 'frontend/.next/standalone/node_modules/next'` | None (not a repo defect) | Time-boxed clean-tree test: `rm -rf frontend/.next && cd frontend && npm run build` → **exit 0**, build completes (route table printed, no errors) |
| F6 | 5. Tests | ! open (cosmetic) | Per-feature counts count *files*, not tests (pytest 9's quiet per-file collect format): prints isolation: 41, data_governance: 36, chromatography: 15, layer_chain: 17, auth: 78 — these are file-grep artifacts; true per-test counts are in §4 | None this pass (out of the F1–F5 scope) | If the script wants test counts, sum the per-file numbers from `pytest --collect-only` instead of grepping path matches |
| F7 | 4. Checks | ! open | "No ruff/flake8 available" | Neither linter installed on this machine or in the venv; `make install` doesn't add them | If lint is wanted in CI, add ruff to dev deps and CI config — separate decision; nothing installed per constraints |
| F8 | 0. Environment | ! open | jq absence warning | jq 1.7.1 is present here, so this no longer fires on this machine; kept for machines without it | Informational only |

---

### Post-patch re-runs (all three modes)

- **Full** — exit 1 (0 failures): `Passed: 15 / Warned: 4 / Failed: 0`.
  Section 5 now shows `✓ pytest collection: 1281 tests collected in 2.40s`
  and `✓ Full suite: 1281 passed, 12 warnings in 5.69s`; section 6 shows
  `✓ frontend tests pass (test:unit)` alongside tsc/lint/build ✓.
- **`--skip-tests --skip-frontend`** — exit 1 (0 failures):
  `Passed: 8 / Warned: 6 / Failed: 0` (the extra 2 warnings are the two
  skip flags themselves).
- **Hostile env (`PHYTO_ENV=cloud PHYTO_AUTH_ENABLED=1 --fast`)** — exit 1
  (0 failures): `Passed: 13 / Warned: 4 / Failed: 0`;
  `✓ Tests pass (fast: -x)`. Same single warning set as baseline — no test
  failures; **conftest pin held**.
- The remaining exit-1s are by design: the script exits 1 on any warning,
  and the 4 residual warnings (1 unpushed commit, 16 unstaged, 6
  untracked, no ruff/flake8) are true statements about the tree that
  cannot be cleared without committing/pushing (forbidden) or installing
  tooling (forbidden).

### Open findings F6–F8, enumerated for action

| # | Finding | Severity | Owner | Recommended action | Can it be closed in this pass? |
|---|---------|----------|-------|--------------------|-------------------------------|
| F6 | Per-feature test counts in §5 count *files*, not tests (pytest 9's quiet per-file collect format) — printed values (isolation: 41, data_governance: 36, chromatography: 15, layer_chain: 17, auth: 78) are grep artifacts, so §5 cannot be quoted for reconciliation without the independent count in §4 | Low (cosmetic, misleading if quoted) | Next script-maintainer pass | Replace the per-feature `grep -cE "test_${feat}\|${feat}_test"` path match with summing per-file counts from `pytest --collect-only` output | **No** — this closure pass is handoff/doc edits only; the script was already patched once for F1–F5 and re-touching it needs its own verification cycle |
| F7 | "No ruff/flake8 available" — no Python linter is installed locally or in `.venv`, and `make install` doesn't add one, so nothing gates style in CI today | Medium (real CI gap) | Repo maintainer (user decision) | Add ruff to dev dependencies (pyproject + `make install`) and a lint step to CI alongside this script | **No** — requires installing tooling / modifying pyproject and Makefile, both forbidden by the audit constraints |
| F8 | jq-absence warning (informational; did not fire on this machine — jq 1.7.1 present) | Low (informational) | Next script-maintainer pass | Keep as-is; it correctly degrades gracefully on machines without jq. Optionally stop depending on jq for script probing (pure-bash alternative) in the same pass as F6 | **No** — no defect to fix; a jq-optional rewrite is out of scope for a closure pass |

### CI gate policy

When wired into CI, this script should gate on exit ≥ 2 (failures only);
warnings are advisory because a clean tree is unreachable while there is
unpushed/uncommitted work.

### Uncommitted file inventory (captured 2026-10-22), grouped by likely origin

This is the decision input for "commit now vs review first". Counts match
the script's git-state section (16 unstaged, 6 untracked).

**Unstaged modifications** (`git diff --name-only`):

- *Layer-chain / chromatography layers (ADR-0061..0066 work):*
  - `services/orchestrator/app/layer_chain.py` (+72/−) — marker-miss semantics fix (ADR-0066): a marker miss is no longer weighted like a hit.
  - `services/taxon_prior/app/markers.py` (+23) — exports `MARKER_SUPPORT_BONUS`/`MARKER_MISS_PENALTY` with threshold-basis notes.
  - `services/calibration/app/main.py` (+8) — threshold-basis docstrings (RT ±0.1 min, UV/MS2 cosines), asserted-provisional.
  - `services/elucidation/app/uv_reranker.py` (+11/−) — UV reranker threshold-basis notes.
  - `services/orchestrator/app/main.py` (+10) — chain summary surfaces `top_candidate_msi_level` so "no Level-1" can't read as "no match".
  - `shared/phyto_shared/peak_purity.py` (+9) — co-elution threshold 0.5 documented asserted-provisional (ADR-0065).
  - `data/pharmacopoeial_markers.json` (+1177/−) — marker data curation: `_meta` provenance block, reference gradient, RT/UV windows per ADR-0066.
  - `tests/test_layer_chain.py` (+91) — marker-miss test additions (the +2 collected tests).
  - `tests/test_orchestrator_chromatography.py` (+4) — small assertion/coverage bump.
- *Threshold-basis documentation (the five-item pass's item 3):*
  - `docs/DECISIONS.md` (+7) — threshold-basis paragraphs for level-1 gates, layer-chain weights and the 0.5 forced-AMBIGUOUS constant.
- *Data-governance (ADR-0056) follow-up tests:*
  - `tests/test_data_governance/test_curator_promotion.py` (+83) — rejected-promotion-audit tests (+2 collected).
  - `tests/test_data_governance/test_provenance_enforcement.py` (+46/−) — manifest-tamper test (+1 collected).
  - `shared/phyto_shared/data_governance.py` (+2/−) — one-line tweak accompanying the above.
- *Auth-flake fix (the five-item pass's item 4):*
  - `tests/conftest.py` (+43) — collection-time env pin (`PHYTO_ENV=local` before service-module import); validated by the hostile-env run.
- *ADR / review doc edits:*
  - `REVIEW_chromatography_layers.md` (+118/−) — auth-flake wording fix, DATA_SOURCES renumber reference, end-of-file addendum.
  - `docs/DATA_SOURCES.md` (+4/−) — sections renumbered 8–9 → 9–10, new §9/§10 lineage documentation.

**Untracked files** (`git ls-files --others --exclude-standard`):

- *This pass (the audit):*
  - `scripts/repo_health_check.sh` — the audit script itself (patched F1–F5).
  - `HANDOFF_repo_health.md` — this report.
  - `HANDOFF_chromatography_layers.md` — prior pass's follow-up note + this pass's two-sentence reconciliation addendum.
- *ADR-index work (the five-item pass's item 1):*
  - `docs/adr/INDEX.md` — single registry of ADR numbers.
  - `scripts/check_adr_numbers.py` — duplicate/missing-number check (passes).
  - `tests/test_docs/test_adr_numbers.py` — CI test wrapping the check (+2 collected).

Nothing pending traces to the isolation-MVP stream — those files were
committed in `db0ce69` and `4977a8a` and are clean.

## 4. Reconciliation of test counts

Measured on this tree (independent `pytest --collect-only`, project venv,
pytest 9.1.1):

- **Total: 1281 collected; 1281 passed, 12 warnings, 0 failed** (full run,
  exit 0, ~6 s).
- Per feature (per-file collection, tests not files):
  - **isolation: 34** — `tests/test_elucidation/test_isolation_mvp.py` (34). Matches the prior session's claim exactly.
  - **data_governance: 36** — all five files in `tests/test_data_governance/` (curator_promotion 9, isolation_validator 7, tier_abs_compatibility 6, provenance_enforcement 6, source_tier_whitelist 4, data_sources_completeness 4). The commit-level claim was 33; the working tree adds +3 uncommitted test edits (curator_promotion +2, provenance_enforcement +1).
  - **chromatography-session (ADR-0061..0066) files: 95** — calibration 10, chromatography_models 15, chrom_rerankers 13, layer_chain 17, orchestrator_chromatography 5, peak_purity 6, pharmacopoeial_markers 11, retention 6, uv_chromophore 12. The commit-level claim was 93; the worktree adds +2 uncommitted layer_chain tests.
  - layer_chain alone: 17 (= 15 at commit + 2 uncommitted).

**Verdict: the numbers reconcile.** 1114 (baseline `f3a537a`) + 160 at
`4977a8a` (93 chromatography + 34 isolation + 33 governance) + 2 untracked
(`test_adr_numbers.py`) + 5 uncommitted edits (layer_chain +2,
curator_promotion +2, provenance_enforcement +1) = **1281**, exactly what
this tree collects and passes. The three claimed totals line up as:
1247 ≙ intermediate session state, 1274 ≙ commit `4977a8a`, 1281 ≙ current
worktree. (The standalone "~93" chromatography figure is the commit-level
count of the nine ADR-0061..0066 files; on the worktree they hold 95
because of the two uncommitted layer_chain tests. The "governance 33" is
commit-level; worktree shows 36.)

---

## 5. Frontend status

A frontend **was found** at `frontend/` (first candidate path checked).
- **node_modules:** present ✓
- **typecheck:** ✓ `tsc --noEmit passes` (via the fallback branch — package.json has no `typecheck` script)
- **lint:** ✓ `eslint .` passes
- **tests:** the original run warned "No test script" because the script
  matched only the name `test`. Resolved by F5: the patched script probes
  `test, test:unit, test:run, test:ci` and now runs `test:unit` itself —
  the full run prints `✓ frontend tests pass (test:unit)` (vitest, 7
  test files, 21 tests, all passing, exit 0).
- **build:** ✓ succeeded on run 1's re-execution (failed once with ENOTEMPTY on the very first pass — finding F3, transient).

## 6. Contract check (section 7, verbatim)

```
═══ 7. FRONTEND ↔ BACKEND CONTRACT (heuristic) ═══
  BE model: shared/phyto_shared/models.py
  FE type:  frontend/src/lib/phyto/types.ts
  ✓ FE type covers all BE fields (heuristic)
```

No MISSING fields were reported, so no per-field verdicts are required.
Both files exist at the paths the heuristic located.

## 7. Explicit statement on repository state

Commit `4977a8a` ("ADR-0056: data governance foundation (tiers, ABS,
curator promotion)") **is on `main` and unpushed** — it is the only commit
on the branch that `origin/main` does not have. On top of it sits an
**uncommitted layer**: 16 unstaged modifications and 6 untracked files.
The untracked set includes `scripts/repo_health_check.sh` and
`HANDOFF_repo_health.md`; the unstaged set includes the one authorized
two-sentence addition to `HANDOFF_chromatography_layers.md` plus the
patched audit script. **Nothing was committed or pushed in this pass.**
Beyond the audit script and the two handoff files,
**no other existing files were modified** — the 15 remaining unstaged
modifications predate this pass (they are the prior sessions'
marker-miss, threshold-basis, auth-flake and governance work).
