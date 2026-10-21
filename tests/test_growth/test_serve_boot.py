"""Smoke test: every service's ``create_app()`` builds without raising.

The regression this guards is the ``make serve`` boot path: the orchestrator
imports the auth package as ``auth`` (ADR-0039), so a PYTHONPATH that does not
expose it fails at *import* time, before any endpoint runs.  Importing each
factory and calling it exercises the same wiring an operator's boot does, in
dry-run mode so no cloud client is constructed.
"""

from __future__ import annotations

import importlib

import pytest

# Every service that exposes a create_app factory.  ``auth`` is the package
# the others import; it is listed too so a break there is caught directly.
SERVICES = (
    "auth",
    "billing",
    "elucidation",
    "ingestion",
    "literature",
    "llm",
    "metabolomics",
    "networking",
    "nmr_search",
    "orchestrator",
    "pharmacology",
)


@pytest.fixture(autouse=True)
def _dry_run(monkeypatch):
    """Keep every factory on the local plane: no GCP, no network."""
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "0")


def test_every_service_create_app_boots():
    """Each factory imports and builds; one failure names every broken service.

    Also pins the import name: ``auth`` must resolve to services/auth/app,
    because that is what the other services import at module level.
    """
    failures: list[str] = []
    for service in SERVICES:
        try:
            module = importlib.import_module(f"{service}.main")
            app = module.create_app()
            assert any(
                getattr(route, "path", None) == "/health" for route in app.routes
            ), f"{service}: no /health route"
        except Exception as exc:  # noqa: BLE001 - report all, not the first
            failures.append(f"{service}: {type(exc).__name__}: {exc}")
    assert not failures, "services failed to boot:\n" + "\n".join(failures)

    auth = importlib.import_module("auth")
    assert auth.__file__ is not None
    assert "/services/auth/app/" in auth.__file__.replace("\\", "/")
