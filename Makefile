.DEFAULT_GOAL := help
PY ?= python3
VENV := .venv
BIN := $(VENV)/bin
# The shared library resolves via PYTHONPATH (same mechanism pytest uses via
# the root pyproject's `pythonpath` setting).  Do NOT rely on `pip install
# -e`: uv's editable .pth was observed being silently skipped by site
# processing on this machine, so explicit is the only reliable option.
SHARED := PYTHONPATH=shared

.PHONY: help install test dry-run serve

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Create .venv and install dev/runtime dependencies
	uv venv --python 3.12 $(VENV) 2>/dev/null || $(PY) -m venv $(VENV)
	uv pip install --python $(BIN)/python "pydantic>=2" pytest fastapi uvicorn httpx

test: ## Run the local-plane test suite (mocks only, no cloud calls)
	$(BIN)/python -m pytest

dry-run: ## Alias for test: the whole suite runs in dry-run mode by design
	$(BIN)/python -m pytest

serve: ## Run the orchestrator API locally (mock tools, no cloud, port 8000)
	$(SHARED) $(BIN)/python -m uvicorn app.main:app --app-dir services/orchestrator --port 8000
