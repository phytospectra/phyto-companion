.DEFAULT_GOAL := help
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
# The shared library resolves via PYTHONPATH (same mechanism pytest uses via
# the root pyproject's `pythonpath` setting).  Do NOT rely on `pip install
# -e`: uv's editable .pth was observed being silently skipped by site
# processing on this machine, so explicit is the only reliable option.
#
# The auth service's code lives in services/auth/app, and every other service
# imports it as `auth` (see ADR-0039).  Adding services/auth itself would
# expose `app` -- colliding with the running service's own package -- so the
# package is staged in build/auth-path as `auth`, exactly as the Dockerfiles
# stage it as /srv/auth.  `build/` is already git-ignored.
# CI installs dependencies into the system interpreter; fall back to PATH
# binaries when the local .venv is absent.
PY_BIN := $(if $(wildcard $(BIN)/python),$(BIN)/python,$(PY))
RUFF := $(if $(wildcard $(BIN)/ruff),$(BIN)/ruff,ruff)

AUTH_PATH := build/auth-path
PYTHONPATH_WITH_AUTH := PYTHONPATH=shared:$(AUTH_PATH):.
SHARED := $(PYTHONPATH_WITH_AUTH)

.PHONY: help install test dry-run lint serve stage-auth

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Create .venv and install dev/runtime dependencies
	uv venv --python 3.12 $(VENV) 2>/dev/null || $(PY) -m venv $(VENV)
	uv pip install --python $(BIN)/python "pydantic>=2" pytest fastapi uvicorn httpx "stripe>=9.0" rdkit numpy scipy pandas networkx scikit-learn matplotlib python-docx ruff

stage-auth: ## Expose services/auth/app as the importable `auth` package
	@mkdir -p $(AUTH_PATH)
	@ln -sfn ../../services/auth/app $(AUTH_PATH)/auth

test: ## Run the local-plane test suite (mocks only, no cloud calls)
	$(PY_BIN) -m pytest

lint: ## Ruff error-class gate (F + E9), including benchmark scripts/notebooks
	$(RUFF) check shared services scripts tests benchmark

dry-run: ## Alias for test: the whole suite runs in dry-run mode by design
	$(PY_BIN) -m pytest

serve: stage-auth ## Run the orchestrator API locally (mock tools, no cloud, port 8000)
	$(SHARED) $(BIN)/python -m uvicorn app.main:app --app-dir services/orchestrator --port 8000
