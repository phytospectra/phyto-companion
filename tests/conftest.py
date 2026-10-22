"""Test bootstrap: registers each service's ``app/`` package under a unique
import alias so tests can say ``import orchestrator.main`` while the
deployable package name stays ``app`` inside its container.

Add one ``_alias_package`` line per service in Steps 4-6.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Collection-time safety: several service modules build their FastAPI app at
# import time (``app = create_app()``), baking ``AuthConfig.from_env()``
# into a module-level singleton no fixture can reach afterwards.  Importing
# them under an ambient ``PHYTO_ENV=cloud`` would construct REAL
# Firebase/Firestore clients, and an ambient ``PHYTO_AUTH_ENABLED=1`` would
# enforce auth on the module-level apps the endpoint tests reuse (401s for
# every unauthenticated call).  The env is therefore pinned here at conftest
# import -- before the ``_alias_package`` calls below execute any service
# module.  Tests that need another plane still opt in per-test via
# ``monkeypatch.setenv`` (and build their own app, as ``service_runner``
# does); the pin only removes the INVOKING SHELL's influence.
os.environ["PHYTO_ENV"] = "local"
for _var in (
    "FIREBASE_CREDENTIALS_PATH",
    "PHYTO_AUTH_ENABLED",
    "PHYTO_DRY_RUN",
    "PHYTO_FREE_TOKENS",
):
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _local_plane_env(monkeypatch):
    """Auth-test isolation (ADR-0028 contract): every test runs on the
    local plane unless it explicitly opts into another plane via
    ``monkeypatch.setenv``.

    The auth layer's ``AuthConfig.from_env()`` keys off ``PHYTO_ENV``: on
    the cloud plane ``install_auth`` would construct the REAL Firebase and
    Firestore clients (importing ``firebase_admin`` and applying
    ``FIREBASE_CREDENTIALS_PATH`` from the developer's shell).  Defaulting
    the whole suite to ``local`` makes that construction unreachable from
    environment leakage -- a stray ``PHYTO_ENV=cloud`` in the invoking
    shell or CI matrix cannot turn any test into a live-GCP call.  The
    module-level pin above extends the same guarantee to import time, which
    the fixture cannot reach.
    """
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.delenv("FIREBASE_CREDENTIALS_PATH", raising=False)


def _alias_package(alias: str, service: str) -> None:
    if alias in sys.modules:
        return
    package_dir = _ROOT / "services" / service / "app"
    spec = importlib.util.spec_from_file_location(
        alias,
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)


_alias_package("orchestrator", "orchestrator")
_alias_package("networking", "networking")
_alias_package("elucidation", "elucidation")
_alias_package("metabolomics", "metabolomics")
_alias_package("pharmacology", "pharmacology")

_alias_package("ingestion", "ingestion")
_alias_package("auth", "auth")
# ``auth`` resolves to services/auth/app, so sibling services import
# ``auth.config`` / ``auth.install`` exactly as they do in the container.
_alias_package("billing", "billing")
_alias_package("literature", "literature")
_alias_package("llm", "llm")
_alias_package("nmr_search", "nmr_search")
# Taxonomy services (ADR-0052/0053/0054): same aliasing contract, and the
# taxon_prior service imports ``taxonomy.clients`` exactly as it will in
# the container once staged.
_alias_package("taxonomy", "taxonomy")
_alias_package("taxon_prior", "taxon_prior")
# Isolation service (ADR-0055): same aliasing contract; imports
# ``auth.config`` / ``auth.install`` exactly as it will in the container.
_alias_package("isolation", "isolation")
# Chromatography services (ADR-0061..0066): calibration (reference
# standards) and retention (RT/RI prediction) -- same contract.
_alias_package("calibration", "calibration")
_alias_package("retention", "retention")
