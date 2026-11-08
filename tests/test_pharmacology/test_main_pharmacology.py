"""Pharmacology API: endpoints, disclaimer on EVERY response, fail-closed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

Q = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
Q_SMILES = "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O"
K = "VNEYFRNUTPLQCC-UHFFFAOYSA-N"

DISCLAIMER = (
    "These are computational hypotheses, not experimental conclusions. "
    "Experimental validation is required."
)


@pytest.fixture()
def client():
    from pharmacology import main

    main.get_runtime.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.get_runtime.cache_clear()


def _predictions_payload():
    return {
        "compound_iks": [Q, K],
        "predictions": [
            {
                "compound_ik": Q, "target_id": "P35354",
                "target_name": "COX-2", "method": "similarity",
                "confidence": 0.72, "confidence_basis": "Tanimoto 0.85",
                "citation": "PMID:11914285",
            },
            {
                "compound_ik": Q, "target_id": "P23219",
                "target_name": "COX-1", "method": "similarity",
                "confidence": 0.61, "confidence_basis": "Tanimoto 0.80",
                "citation": "PMID:1675914",
            },
            {
                "compound_ik": K, "target_id": "P35354",
                "target_name": "COX-2", "method": "similarity",
                "confidence": 0.35, "confidence_basis": "Tanimoto 0.62",
                "citation": "PMID:11914285",
            },
        ],
    }


class TestHealth:
    def test_health(self, client):
        assert client.get("/health").json()["service"] == "pharmacology"


class TestTargets:
    def test_predictions_with_methods_and_gaps(self, client):
        body = client.post(
            "/pharmacology/targets",
            json={"compound_iks": [Q], "compound_smiles": {Q: Q_SMILES}},
        ).json()
        assert body["predictions"]
        assert all(p["method"] for p in body["predictions"])
        assert body["disclaimer"] == DISCLAIMER
        assert any("docking" in gap for gap in body["gaps"])

    def test_provenance_present(self, client):
        body = client.post(
            "/pharmacology/targets", json={"compound_iks": [Q]}
        ).json()
        assert len(body["provenance_manifest_id"]) == 64


class TestNetwork:
    def test_network_and_hubs(self, client):
        body = client.post("/pharmacology/network", json=_predictions_payload()).json()
        assert body["n_nodes"] >= 4
        assert body["hubs"]
        assert body["disclaimer"] == DISCLAIMER

    def test_modules_reported(self, client):
        body = client.post("/pharmacology/network", json=_predictions_payload()).json()
        assert body["modules"]


class TestHypothesis:
    def test_hypothesis_endpoint(self, client):
        payload = _predictions_payload()
        payload["compound"] = Q
        body = client.post("/pharmacology/hypothesis", json=payload).json()
        assert body["hypothesis"]["is_hypothesis"] is True
        assert body["hypothesis"]["evidence"]["evidence"]
        assert body["disclaimer"] == DISCLAIMER

    def test_compound_without_evidence_422(self, client):
        payload = _predictions_payload()
        payload["compound"] = "UNKNOWN-IK"
        response = client.post("/pharmacology/hypothesis", json=payload)
        assert response.status_code == 422


class TestSynergy:
    def test_synergy_with_ci(self, client):
        payload = _predictions_payload()
        payload["effects"] = [0.5, 2.0, 3.0]
        payload["single_effects"] = [[0.5, 10.0], [0.5, 10.0]]
        body = client.post("/pharmacology/synergy", json=payload).json()
        assert body["result"]["hypotheses"]
        assert body["combination_index"] == pytest.approx(0.5)
        assert body["disclaimer"] == DISCLAIMER

    def test_single_compound_422(self, client):
        payload = _predictions_payload()
        payload["compound_iks"] = [Q]
        response = client.post("/pharmacology/synergy", json=payload)
        assert response.status_code == 422


class TestKnowledge:
    def test_import_then_query(self, client):
        body = client.post(
            "/pharmacology/knowledge",
            json={"compound_ik": Q, "import_sources": ["chembl", "kegg"]},
        ).json()
        context = body["context"]
        assert context["n_reported_targets"] == 2
        assert context["pathways"]
        assert body["disclaimer"] == DISCLAIMER

    def test_unknown_source_422(self, client):
        response = client.post(
            "/pharmacology/knowledge",
            json={"compound_ik": Q, "import_sources": ["magic"]},
        )
        assert response.status_code == 422


class TestLiterature:
    def test_query(self, client):
        body = client.post(
            "/pharmacology/literature", json={"query": "flavonoids COX-2"}
        ).json()
        assert body["hits"]
        assert body["disclaimer"] == DISCLAIMER

    def test_needs_query_or_compound(self, client):
        assert client.post("/pharmacology/literature", json={}).status_code == 422


class TestADMET:
    def test_unavailable_and_honest(self, client):
        body = client.post(
            "/pharmacology/admet", json={"smiles": "CCO"}
        ).json()
        assert body["result"]["method"] == "unavailable"
        assert body["result"]["known_limitations"]
        assert body["disclaimer"] == DISCLAIMER


class TestDisclaimerEverywhere:
    def test_every_pharmacology_response_has_disclaimer(self, client):
        responses = [
            client.post("/pharmacology/targets", json={"compound_iks": [Q]}),
            client.post("/pharmacology/network", json=_predictions_payload()),
            client.post("/pharmacology/synergy", json=_predictions_payload()),
            client.post(
                "/pharmacology/knowledge", json={"compound_ik": Q}
            ),
            client.post("/pharmacology/literature", json={"query": "quercetin"}),
            client.post("/pharmacology/admet", json={"smiles": "CCO"}),
        ]
        for response in responses:
            assert response.status_code == 200, response.text
            assert response.json()["disclaimer"] == DISCLAIMER
