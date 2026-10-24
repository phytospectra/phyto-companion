#!/usr/bin/env bash
# scripts/repo_health_check.sh
# Read-only repository health audit. Never commits, pushes, or modifies files.
#
# Sections:
#   1. Git state        — branch, HEAD, remote, unpushed/unpulled/uncommitted
#   2. Repo hygiene     — committed bytecode/.env, large files, TODO markers
#   3. Dependency state — lockfile sync
#   4. Check scripts    — ADR check, ruff/flake8, mypy (if configured)
#   5. Tests            — collection count, per-feature counts, full run
#   6. Frontend         — typecheck, lint, tests, build (if present)
#   7. BE↔FE contract   — heuristic field-parity check
#   8. Summary
#
# Usage:
#   scripts/repo_health_check.sh [--fast] [--skip-tests] [--skip-frontend]
#
# Exit codes: 0 = all pass, 1 = warnings, 2 = failures

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAST=0; SKIP_TESTS=0; SKIP_FRONTEND=0
for arg in "$@"; do case "$arg" in
  --fast) FAST=1 ;;
  --skip-tests) SKIP_TESTS=1 ;;
  --skip-frontend) SKIP_FRONTEND=1 ;;
  -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *) echo "Unknown arg: $arg" >&2; exit 2 ;;
esac; done

# --- Python resolver (F2) -------------------------------------------------
# Prefer the project venv, then an activated venv, then whatever python3 is
# first on PATH.  Never install anything; fail loudly if no interpreter has
# pytest, because that is precisely the CI failure mode we must catch early.
PY=""
for cand in "$REPO_ROOT/.venv/bin/python" "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}" "$(command -v python3 2>/dev/null || true)"; do
  [ -n "$cand" ] && [ -x "$cand" ] || continue
  "$cand" -c 'import pytest' >/dev/null 2>&1 && { PY="$cand"; break; }
  [ -z "$PY" ] && PY="$cand"
done
if [ -z "$PY" ]; then
  echo "ERROR: no Python interpreter found." >&2
  echo "       Looked for: .venv/bin/python, \$VIRTUAL_ENV/bin/python, python3 on PATH." >&2
  echo "       Fix: run 'make install' to create .venv (installs pytest), then re-run." >&2
  exit 2
fi
if ! "$PY" -c 'import pytest' >/dev/null 2>&1; then
  if [ "$SKIP_TESTS" -eq 0 ]; then
    echo "ERROR: $PY has no pytest — the test section cannot run." >&2
    echo "       Fix: 'make install' (creates .venv with pytest), or activate a venv that has it." >&2
    echo "       For a git-state-only audit, re-run with --skip-tests." >&2
    exit 2
  fi
  echo "WARN: $PY has no pytest; continuing because --skip-tests was given." >&2
fi

declare -a PASS=() WARN=() FAIL=()
pass() { PASS+=("$1"); printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { WARN+=("$1"); printf '  \033[33m!\033[0m %s\n' "$1"; }
fail() { FAIL+=("$1"); printf '  \033[31m✗\033[0m %s\n' "$1"; }
hdr()  { printf '\n\033[1;36m═══ %s ═══\033[0m\n' "$1"; }

run_check() {
  local ok="$1" bad="$2"; shift 2
  if "$@" >/tmp/rhc_out 2>&1; then pass "$ok"; else fail "$bad"; tail -20 /tmp/rhc_out | sed 's/^/      /'; fi
}

hdr "0. ENVIRONMENT"
echo "  Repo root:  $REPO_ROOT"
echo "  Timestamp:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Python:     $("$PY" --version 2>&1 | awk '{print $2}')  ($PY)"
command -v node >/dev/null 2>&1 && echo "  Node:       $(node --version)"
command -v npm  >/dev/null 2>&1 && echo "  npm:        $(npm --version)"
command -v jq   >/dev/null 2>&1 && echo "  jq:         $(jq --version)" || warn "jq not installed — frontend script parsing will be limited"

hdr "1. GIT STATE"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")
HEAD=$(git rev-parse --short HEAD 2>/dev/null || echo "none")
REMOTE_URL=$(git remote get-url origin 2>/dev/null || echo "<none>")
echo "  Branch:  $BRANCH"
echo "  HEAD:    $HEAD"
echo "  Remote:  $REMOTE_URL"

if [ "$FAST" -eq 0 ] && [ "$REMOTE_URL" != "<none>" ]; then
  git fetch --quiet origin 2>/dev/null && pass "Fetched origin" || warn "Could not fetch origin (offline?)"
fi

UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")
if [ -z "$UPSTREAM" ]; then
  warn "No upstream tracking branch — nothing is pushed"
  echo "  → 0 commits on any remote (branch is local-only)"
else
  echo "  Upstream: $UPSTREAM"
  UNPUSHED=$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)
  UNPULLED=$(git rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)
  if [ "$UNPUSHED" -gt 0 ]; then
    warn "$UNPUSHED unpushed commit(s):"
    git log '@{u}..HEAD' --oneline | sed 's/^/      /'
  else pass "No unpushed commits"; fi
  if [ "$UNPULLED" -gt 0 ]; then warn "$UNPULLED unpulled commit(s) on origin"; else pass "No unpulled commits"; fi
  echo "  → $UNPUSHED commit(s) NOT on $UPSTREAM"
fi

STAGED=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
UNSTAGED=$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')
UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
[ "$STAGED"   -gt 0 ] && warn "$STAGED staged file(s) — NOT committed"
[ "$UNSTAGED" -gt 0 ] && warn "$UNSTAGED unstaged modification(s) — NOT committed"
if [ "$UNTRACKED" -gt 0 ]; then
  warn "$UNTRACKED untracked file(s) — NOT tracked:"
  git ls-files --others --exclude-standard | head -20 | sed 's/^/      /'
fi
[ "$STAGED" -eq 0 ] && [ "$UNSTAGED" -eq 0 ] && [ "$UNTRACKED" -eq 0 ] && pass "Working tree clean"
STASH=$(git stash list 2>/dev/null | wc -l | tr -d ' ')
[ "$STASH" -gt 0 ] && warn "$STASH stash(es) present"
echo "  Recent commits:"
git log --oneline -5 2>/dev/null | sed 's/^/    /'

hdr "2. REPO HYGIENE"
if git ls-files | grep -qE '(__pycache__|\.pyc$|\.pyo$|\.DS_Store)'; then
  fail "Committed bytecode/OS artifacts:"; git ls-files | grep -E '(__pycache__|\.pyc$|\.pyo$|\.DS_Store)' | head -10 | sed 's/^/      /'
else pass "No committed bytecode/OS artifacts"; fi
if git ls-files | grep -qE '(^|/)\.env($|\.(local|production|development|staging|test|prod|dev)(\.|$))'; then
  fail "Potential .env file tracked:"; git ls-files | grep -E '(^|/)\.env($|\.(local|production|development|staging|test|prod|dev)(\.|$))' | sed 's/^/      /'
else pass "No .env files tracked"; fi
if [ -f ".gitignore" ]; then
  SIZE=$(wc -l < .gitignore | tr -d ' ')
  [ "$SIZE" -ge 5 ] && pass ".gitignore present ($SIZE lines)" || warn ".gitignore suspiciously short ($SIZE lines)"
else warn "No .gitignore"; fi
BIG=$(git ls-files -z | xargs -0 -n1 sh -c 'f="$0"; [ -f "$f" ] && [ "$(wc -c <"$f")" -gt 5000000 ] && echo "$f"' 2>/dev/null | head -10)
[ -n "$BIG" ] && { warn "Large tracked files (>5 MB):"; echo "$BIG" | sed 's/^/      /'; }
TODOS=$(git grep -nwE 'TODO|FIXME|XXX|HACK' -- '*.py' '*.ts' '*.tsx' '*.js' '*.md' 2>/dev/null | wc -l | tr -d ' ')
echo "  TODO/FIXME/XXX/HACK markers: $TODOS"

hdr "3. DEPENDENCY STATE"
if [ -f pyproject.toml ]; then
  pass "pyproject.toml present"
  if grep -q '\[tool.poetry\]' pyproject.toml && command -v poetry >/dev/null 2>&1; then
    run_check "poetry.lock in sync" "poetry.lock OUT OF SYNC" poetry check --lock
  elif command -v uv >/dev/null 2>&1 && [ -f uv.lock ]; then
    run_check "uv.lock in sync" "uv.lock OUT OF SYNC" uv lock --check
  fi
else warn "No pyproject.toml at repo root"; fi

hdr "4. CHECK SCRIPTS"
for f in scripts/check_*.py; do
  [ -e "$f" ] || continue
  run_check "$(basename "$f") passes" "$(basename "$f") FAILED" "$PY" "$f"
done
if command -v ruff >/dev/null 2>&1; then
  run_check "ruff check clean" "ruff check FAILED" ruff check .
elif command -v flake8 >/dev/null 2>&1; then
  run_check "flake8 clean" "flake8 FAILED" flake8 .
else warn "No ruff/flake8 available"; fi
if command -v mypy >/dev/null 2>&1 && grep -q '\[tool.mypy\]' pyproject.toml 2>/dev/null; then
  run_check "mypy clean" "mypy has errors" mypy . --ignore-missing-imports
fi
[ -f Makefile ] && echo "  Makefile targets: $(grep -E '^[a-zA-Z_-]+:' Makefile | cut -d: -f1 | tr '\n' ' ')"

hdr "5. TESTS"
if [ "$SKIP_TESTS" -eq 1 ]; then warn "Tests skipped (--skip-tests)"
elif [ ! -d tests ]; then fail "No tests/ directory"
else
  if COLL=$("$PY" -m pytest --collect-only -q 2>&1 | tail -1); then pass "pytest collection: $COLL"; else fail "pytest collection FAILED"; fi
  # F6: count actual tests per feature from the collection list, not files.
  # pytest 9 quiet collect output ends each file's block with "<N> tests" or
  # "no tests ran"; the lines before it are test node ids.
  for feat in isolation data_governance chromatography layer_chain auth metabolomics; do
    N=$("$PY" -m pytest --collect-only -q 2>/dev/null | grep -cE "test_${feat}|${feat}_test" || true)
    [ "$N" -gt 0 ] && echo "    ${feat}: $N test(s)"
  done
  if [ "$FAST" -eq 1 ]; then
    run_check "Tests pass (fast: -x)" "Tests FAILED (fast)" "$PY" -m pytest -q -x
  else
    if "$PY" -m pytest -q >/tmp/rhc_test 2>&1; then
      SUMMARY=$(grep -E '[0-9]+ passed' /tmp/rhc_test | tail -1)
      pass "Full suite: $SUMMARY"
    else
      fail "Full suite FAILED"; tail -40 /tmp/rhc_test | sed 's/^/      /'
    fi
  fi
fi

hdr "6. FRONTEND"
FRONTEND=""
for cand in frontend web ui apps/web apps/client client; do
  [ -f "$cand/package.json" ] && { FRONTEND="$cand"; break; }
done
# F5: packages name their scripts inconsistently; probe the common aliases in
# priority order and use whichever exists.
first_script() {
  local k
  for k in "$@"; do
    jq -e --arg k "$k" '.scripts[$k]' package.json >/dev/null 2>&1 && { echo "$k"; return 0; }
  done
  return 1
}
if [ "$SKIP_FRONTEND" -eq 1 ]; then warn "Frontend skipped (--skip-frontend)"
elif [ -z "$FRONTEND" ]; then warn "No frontend found (checked: frontend, web, ui, apps/web, apps/client, client)"
else
  echo "  Frontend: $FRONTEND/"
  cd "$FRONTEND" || exit 1
  if command -v jq >/dev/null 2>&1; then
    echo "  package.json scripts:"; jq -r '.scripts // {} | to_entries[] | "    \(.key): \(.value)"' package.json 2>/dev/null
  fi
  [ -d node_modules ] && pass "node_modules installed" || warn "node_modules missing — run 'npm ci'"
  TC_SCRIPT=$(first_script typecheck type-check type_check tsc || true)
  LINT_SCRIPT=$(first_script lint lint:ci eslint || true)
  TEST_SCRIPT=$(first_script test test:unit test:run test:ci || true)
  BUILD_SCRIPT=$(first_script build build:prod || true)
  if [ -n "$TC_SCRIPT" ]; then
    run_check "typecheck passes ($TC_SCRIPT)" "typecheck FAILED ($TC_SCRIPT)" npm run --silent "$TC_SCRIPT"
  elif [ -f tsconfig.json ] && command -v npx >/dev/null 2>&1; then
    run_check "tsc --noEmit passes" "tsc --noEmit FAILED" npx --no-install tsc --noEmit
  else warn "No typecheck script / tsconfig.json"; fi
  if [ -n "$LINT_SCRIPT" ]; then
    run_check "lint passes ($LINT_SCRIPT)" "lint FAILED ($LINT_SCRIPT)" npm run --silent "$LINT_SCRIPT"
  else warn "No lint script"; fi
  if [ -n "$TEST_SCRIPT" ]; then
    run_check "frontend tests pass ($TEST_SCRIPT)" "frontend tests FAILED ($TEST_SCRIPT)" env CI=1 npm run --silent "$TEST_SCRIPT"
  else warn "No test script"; fi
  if [ "$FAST" -eq 0 ] && [ -n "$BUILD_SCRIPT" ]; then
    run_check "frontend build succeeds ($BUILD_SCRIPT)" "frontend build FAILED ($BUILD_SCRIPT)" npm run --silent "$BUILD_SCRIPT"
  fi
  cd "$REPO_ROOT" || exit 1
fi

hdr "7. FRONTEND ↔ BACKEND CONTRACT (check_fe_contract.py)"
if [ -n "$FRONTEND" ] && [ -f "$REPO_ROOT/scripts/check_fe_contract.py" ]; then
  run_check "FE contract types match BE surface" "FE contract check FAILED" \
    "$PY" "$REPO_ROOT/scripts/check_fe_contract.py"
elif [ -n "$FRONTEND" ]; then
  BE=$(grep -rl 'class CandidateRank' --include='*.py' shared services 2>/dev/null | head -1)
  FE=$(grep -rl 'CandidateRank' --include='*.ts' --include='*.tsx' "$FRONTEND/src" 2>/dev/null | grep -iE '(type|schema|api)' | head -1)
  if [ -n "$BE" ] && [ -n "$FE" ]; then
    echo "  BE model: $BE"
    echo "  FE type:  $FE"
    BE_F=$(awk '/^class CandidateRank/,/^class [A-Z]/' "$BE" | grep -oE '^[[:space:]]+[a-z_][a-z0-9_]*:' | tr -d ' :' | sort -u)
    FE_F=$(awk '/[^a-zA-Z]CandidateRank/,/^[}]/' "$FE" | grep -oE '^[[:space:]]+[a-z_][a-z0-9_]*\??:' | tr -d ' ?:' | sort -u)
    MISSING=$(comm -23 <(echo "$BE_F") <(echo "$FE_F") 2>/dev/null || true)
    if [ -z "$MISSING" ]; then pass "FE type covers all BE fields (heuristic)"
    else warn "FE type may miss BE fields:"; echo "$MISSING" | sed 's/^/      /'; echo "      (verify manually — heuristic only)"; fi
  else warn "Could not locate CandidateRank in both BE and FE"; fi
fi

hdr "SUMMARY"
printf '\n  Passed: %d\n  Warned: %d\n  Failed: %d\n' "${#PASS[@]}" "${#WARN[@]}" "${#FAIL[@]}"
if [ "${#FAIL[@]}" -gt 0 ]; then
  printf '\n  \033[31mFAILURES:\033[0m\n'; for f in "${FAIL[@]}"; do printf '    ✗ %s\n' "$f"; done; exit 2
elif [ "${#WARN[@]}" -gt 0 ]; then
  printf '\n  \033[33mWARNINGS:\033[0m\n'; for w in "${WARN[@]}"; do printf '    ! %s\n' "$w"; done; exit 1
else printf '\n  \033[32mAll checks passed.\033[0m\n'; exit 0
fi
