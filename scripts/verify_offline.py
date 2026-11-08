#!/usr/bin/env python
"""Offline quickstart verification for the real reference-HSQC workflow.

Runs WITHOUT any network, credentials, Firebase, or Firestore: it bootstraps
the service import layout (same aliasing the test suite uses), constructs the
app with the mock-token auth layer for local offline operation, and exercises
health, configuration fail-closed behavior, a real local scientific run on
EXPLICITLY SYNTHETIC contract libraries, and the human checkpoint approval
path. This is execution verification only -- never scientific validation.

Usage (inside the isolated checkout, after `make install && make stage-auth`):

    PYTHONPATH=shared:build/auth-path:. PHYTO_ENV=local \
    PHYTO_AUTH_ENABLED=0 PHYTO_DRY_RUN=1 PHYTO_SCIENTIFIC_EXECUTION=real \
    .venv/bin/python scripts/verify_offline.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bootstrap() -> None:
    sys.path.insert(0, str(ROOT / "shared"))
    sys.path.insert(0, str(ROOT))
    for alias, service in (
        ("orchestrator", "orchestrator"),
        ("auth", "auth"),
        ("elucidation", "elucidation"),
    ):
        if alias in sys.modules:
            continue
        directory = ROOT / "services" / service / "app"
        spec = importlib.util.spec_from_file_location(
            alias, directory / "__init__.py", submodule_search_locations=[str(directory)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[alias] = module
        spec.loader.exec_module(module)


def main() -> int:
    bootstrap()
    from fastapi.testclient import TestClient

    from auth.config import AuthConfig
    from auth.firebase_auth import MockFirebaseAuthClient
    from auth.install import TokenLayer, install_auth
    from auth.pubsub_publisher import LoggingPublisher
    from auth.token_balance import MockTokenBalanceClient
    from orchestrator import main as orchestrator_main
    from orchestrator.execution_config import ScientificExecutionConfig
    from phyto_shared.exceptions import InvalidInputError

    # 1. Fail-closed configuration contracts.
    for env in ({}, {"PHYTO_SCIENTIFIC_EXECUTION": "guessed"}):
        try:
            ScientificExecutionConfig.from_env(env)
        except InvalidInputError:
            pass
        else:
            raise SystemExit("missing/invalid scientific mode did not fail closed")
    try:
        ScientificExecutionConfig.from_env({
            "PHYTO_ENV": "cloud", "PHYTO_SCIENTIFIC_EXECUTION": "real", "PHYTO_DRY_RUN": "1",
        })
    except InvalidInputError:
        pass
    else:
        raise SystemExit("cloud real science accepted offline mock auth")

    # 2. Synthetic contract libraries for the real executor. Invented
    # coordinates; RDKit derives genuine identities for the two structures.
    from rdkit import Chem

    def inchikey(smiles: str) -> str:
        return Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))

    coordinates = [(1.0, 20.0), (2.0, 40.0), (3.0, 60.0), (4.0, 80.0)]
    references = [
        {
            "reference_id": "SYNTHETIC-ethanol",
            "candidate_id": "ethyl-alcohol",
            "inchikey": inchikey("CCO"),
            "source": "Invented numeric algorithm fixture; not measured chemistry",
            "solvent": "SYNTHETIC/no acquisition",
            "license": "Test-generated numeric values; no source dataset",
            "provenance": "experimental",  # schema-required curator assertion, not evidence
            "peaks": [{"h_ppm": h, "c_ppm": c} for h, c in coordinates],
        }
    ]
    with tempfile.TemporaryDirectory(prefix="phyto-offline-quickstart-") as temporary:
        directory = Path(temporary)
        structure_path = directory / "structures.csv"
        reference_path = directory / "references.json"
        structure_path.write_text(
            "name,smiles,provenance,source\n"
            "Ethyl Alcohol,CCO,synthetic,SYNTHETIC quickstart fixture\n"
            "Dimethyl Ether,COC,synthetic,SYNTHETIC quickstart fixture\n"
        )
        reference_path.write_text(json.dumps(references))

        os.environ["PHYTO_SCIENTIFIC_EXECUTION"] = "real"
        os.environ["PHYTO_STRUCTURE_DB_PATH"] = str(structure_path)
        os.environ["PHYTO_HSQC_REFERENCE_LIBRARY"] = str(reference_path)
        orchestrator_main.get_runtime.cache_clear()
        config = AuthConfig("offline-quickstart", None, dry_run=True,
                            default_free_tokens=500, enabled=False)
        layer = TokenLayer(
            MockFirebaseAuthClient(), MockTokenBalanceClient({"reviewer": 500}),
            LoggingPublisher(), entitlements=None,
        )
        orchestrator_main.install_auth = (
            lambda application, cfg: install_auth(application, cfg, layer=layer)
        )
        with TestClient(orchestrator_main.create_app(config)) as client:
            health = client.get("/health")
            assert health.status_code == 200
            body = health.json()
            assert body["scientific_execution"] == "real"
            assert body["scientific_ready"] is False
            assert body["execution_readiness"]["supported_workflows"] == [
                "reference_hsqc_identify"
            ]
            request = {
                "sample_id": "offline-quickstart-synthetic",
                "workflow": "reference_hsqc_identify",
                "modalities": ["hsqc"],
                "dry_run": False,
                "parameters": {
                    "formula": "C2H6O",
                    "peaks": [
                        {"peak_type": "2d", "h_ppm": h, "c_ppm": c,
                         "intensity": 1.0, "experiment_type": "HSQC"}
                        for h, c in reversed(coordinates)
                    ],
                    "h_tolerance_ppm": 0.05,
                    "c_tolerance_ppm": 0.5,
                    "max_results": 10,
                },
            }
            response = client.post("/analysis", json=request)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "awaiting_review"
            run_id = response.json()["run_id"]
            status = client.get(f"/runs/{run_id}")
            assert status.status_code == 200 and status.json()["status"] == "awaiting_review"
            unsupported = client.post("/analysis", json={**request, "workflow": "identify"})
            assert unsupported.status_code == 422
            from types import SimpleNamespace

            import auth.middleware as auth_middleware

            original = auth_middleware.cost_for_route
            auth_middleware.cost_for_route = (
                lambda method, path: SimpleNamespace(tokens=0)
            )
            try:
                approved = client.post(f"/runs/{run_id}/approve", json={
                    "checkpoint": "before_confirmed_claim", "actor": "reviewer",
                })
            finally:
                auth_middleware.cost_for_route = original
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == "complete"
            assert approved.json()["citation"] is None
            print("offline quickstart: health, fail-closed config, real synthetic "
                  "run, 422-unsupported workflow, human checkpoint completion OK")
    orchestrator_main.get_runtime.cache_clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
