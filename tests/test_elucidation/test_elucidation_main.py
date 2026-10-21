"""FastAPI endpoint tests for the elucidation service (mocked tools)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from rdkit import Chem

from test_candidate_ranker import PHENOL_OBSERVED

ETHANOL_IK = Chem.MolToInchiKey(Chem.MolFromSmiles("CCO"))
BENZENE_IK = Chem.MolToInchiKey(Chem.MolFromSmiles("c1ccccc1"))


@pytest.fixture()
def client():
    from elucidation import main

    main.get_runtime.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.get_runtime.cache_clear()


class TestHealth:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["predictor"] == "MockShiftPredictor"
        assert body["db_size"] >= 8


class TestCandidates:
    def test_ranking_endpoint(self, client):
        response = client.post(
            "/elucidate/candidates",
            json={
                "observed": PHENOL_OBSERVED.model_dump(),
                "formula": "C6H6O",
            },
        )
        assert response.status_code == 200
        body = response.json()
        names = [rank["candidate"]["name"] for rank in body["ranks"]]
        assert names[0] == "phenol"
        assert all(
            rank["ranking_convention"] == "lower_is_better" for rank in body["ranks"]
        )
        assert len(body["provenance_manifest_id"]) == 64

    def test_unknown_formula_returns_empty(self, client):
        response = client.post(
            "/elucidate/candidates",
            json={"observed": {"h_1d": [7.0]}, "formula": "C50H50"},
        )
        assert response.status_code == 200
        assert response.json()["ranks"] == []

    def test_bad_formula_422(self, client):
        response = client.post(
            "/elucidate/candidates",
            json={"observed": {"h_1d": [7.0]}, "formula": "not-a-formula"},
        )
        assert response.status_code == 422


class TestNovel:
    def test_generate_and_rank(self, client):
        # catechol + OH -> hydroxyquinol (C6H6O3): NOT in the mock DB, so
        # it survives the "already known" filter (unlike benzene->phenol)
        catechol_ik = Chem.MolToInchiKey(Chem.MolFromSmiles("Oc1ccccc1O"))
        response = client.post(
            "/elucidate/novel",
            json={
                "target_formula": "C6H6O3",
                "parent_ik": catechol_ik,
                "observed": {"h_1d": [6.5, 6.7, 6.9]},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["novel_candidates"]
        assert body["novel_candidates"][0]["candidate"]["provenance"] == "predicted"
        assert len(body["provenance_manifest_id"]) == 64

    def test_unreachable_target_422(self, client):
        response = client.post(
            "/elucidate/novel",
            json={
                "target_formula": "C7H20N3",
                "parent_ik": BENZENE_IK,
                "observed": {"h_1d": [7.0]},
            },
        )
        assert response.status_code == 422

    def test_unknown_parent_404(self, client):
        response = client.post(
            "/elucidate/novel",
            json={
                "target_formula": "C6H6O",
                "parent_ik": "XXXXXXXXXXXXXX-XXXXXXXXXX-X",
                "observed": {"h_1d": [7.0]},
            },
        )
        assert response.status_code == 404


class TestLocalize:
    def test_localize_production_mode(self, client):
        response = client.post(
            "/elucidate/localize",
            json={
                "parent_ik": ETHANOL_IK,
                "novel_smiles": "OCCO",
                "observed": {
                    "h_1d": [3.70, 3.70, 4.80, 4.80],
                    "c_1d": [62.0, 62.0],
                    "formula": "C2H6O2",
                },
            },
        )
        assert response.status_code == 200
        localization = response.json()["localization"]
        assert localization["edit_atom_index"] == 0
        assert localization["rank_of_true_site"] is None
        assert len(response.json()["provenance_manifest_id"]) == 64

    def test_localize_benchmark_mode(self, client):
        response = client.post(
            "/elucidate/localize",
            json={
                "parent_ik": ETHANOL_IK,
                "novel_smiles": "OCCO",
                "observed": {
                    "h_1d": [3.70, 3.70, 4.80, 4.80],
                    "c_1d": [62.0, 62.0],
                    "formula": "C2H6O2",
                },
                "true_edit_site": 0,
            },
        )
        localization = response.json()["localization"]
        assert localization["rank_of_true_site"] == 1

    def test_unparseable_smiles_422(self, client):
        response = client.post(
            "/elucidate/localize",
            json={
                "parent_ik": ETHANOL_IK,
                "novel_smiles": "not-smiles",
                "observed": {"h_1d": [1.0]},
            },
        )
        assert response.status_code == 422


class TestEvidence:
    def test_evidence_endpoint(self, client):
        ranked = client.post(
            "/elucidate/candidates",
            json={
                "observed": PHENOL_OBSERVED.model_dump(),
                "formula": "C6H6O",
            },
        ).json()["ranks"][0]
        response = client.post(
            "/elucidate/evidence",
            json={"rank": ranked, "observed": PHENOL_OBSERVED.model_dump()},
        )
        assert response.status_code == 200
        graph = response.json()["evidence_graph"]
        assert "claim_nodes" in graph
        assert "match_support" in graph["claim_nodes"]
        assert len(response.json()["provenance_manifest_id"]) == 64
