"""Test bootstrap: registers each service's ``app/`` package under a unique
import alias so tests can say ``import orchestrator.main`` while the
deployable package name stays ``app`` inside its container.

Add one ``_alias_package`` line per service in Steps 4-6.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


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
