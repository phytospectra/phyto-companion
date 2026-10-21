"""Metabolomics API: every endpoint with mocked backends."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from metabolomics import main

    main.get_runtime.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.get_runtime.cache_clear()


def _table(client) -> dict:
    return client.post(
        "/metabolomics/detect",
        json={"mzml_filenames": [f"/d/{n}.mzML" for n in
                                ("control_1", "control_2", "control_3",
                                 "treated_1", "treated_2", "treated_3",
                                 "qc_1")]},
    ).json()["table"]


def _two_group_table(client) -> dict:
    """Six samples, two classes: the shape pls_da/RF/univariate require."""
    table = _table(client)
    table["samples"] = [s for s in table["samples"] if s["sample_id"] != "qc_1"]
    for feature in table["features"]:
        feature["intensities"].pop("qc_1", None)
    return table


LABELS = {
    "control_1": "control", "control_2": "control", "control_3": "control",
    "treated_1": "treated", "treated_2": "treated", "treated_3": "treated",
    "qc_1": "qc",
}

TWO_GROUP_LABELS = {
    "control_1": "control", "control_2": "control", "control_3": "control",
    "treated_1": "treated", "treated_2": "treated", "treated_3": "treated",
}


class TestHealth:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["service"] == "metabolomics"
        assert body["detector"] == "MockDetector"


class TestDetect:
    def test_returns_table_and_provenance(self, client):
        body = client.post(
            "/metabolomics/detect",
            json={"mzml_filenames": ["/d/control_1.mzML", "/d/treated_1.mzML"]},
        ).json()
        assert len(body["table"]["features"]) == 7
        assert len(body["provenance_manifest_id"]) == 64


class TestNormalize:
    def test_tic(self, client):
        body = client.post(
            "/metabolomics/normalize",
            json={"table": _table(client), "method": "tic"},
        ).json()
        assert any("tic" in note for note in body["table"]["provenance_notes"])

    def test_unknown_method_422(self, client):
        response = client.post(
            "/metabolomics/normalize",
            json={"table": _table(client), "method": "magic"},
        )
        assert response.status_code == 422


class TestQC:
    def test_report_and_alerts(self, client):
        body = client.post(
            "/metabolomics/qc",
            json={"table": _table(client), "qc_samples": ["qc_1"]},
        ).json()
        assert body["report"]["n_qc_samples"] == 1
        assert isinstance(body["alerts"], list)
        assert "suggest_rerun" in body


class TestStatistics:
    def test_all_methods(self, client):
        body = client.post(
            "/metabolomics/statistics",
            json={
                "table": _two_group_table(client),
                "labels": TWO_GROUP_LABELS,
                "methods": ["pca", "pls_da", "random_forest", "univariate"],
                "n_permutations": 5,
            },
        ).json()
        assert "pca" in body and "pls_da" in body
        assert "random_forest" in body and "univariate" in body
        assert "intersection" in body["biomarkers"]
        assert len(body["provenance_manifest_id"]) == 64

    def test_three_classes_rejected_for_supervised_methods(self, client):
        response = client.post(
            "/metabolomics/statistics",
            json={
                "table": _table(client),
                "labels": LABELS,
                "methods": ["pls_da"],
            },
        )
        assert response.status_code == 422

    def test_unknown_label_422(self, client):
        response = client.post(
            "/metabolomics/statistics",
            json={"table": _table(client), "labels": {"ghost": "control"}},
        )
        assert response.status_code == 422


class TestDifferential:
    def test_volcano_payload(self, client):
        body = client.post(
            "/metabolomics/differential",
            json={
                "table": _table(client),
                "groups": {
                    "control": ["control_1", "control_2", "control_3"],
                    "treated": ["treated_1", "treated_2", "treated_3"],
                },
                "reference_group": "control",
            },
        ).json()
        assert body["volcano"]["points"]
        assert body["result"]["comparisons"][0]["significant_features"]


class TestFusion:
    def test_mfa_and_permutation(self, client):
        import numpy as np

        rng = np.random.default_rng(0)
        labels = ["control"] * 8 + ["treated"] * 8
        block_a = (np.array([0] * 8 + [1] * 8)[:, None] * 2 + rng.normal(0, 0.3, (16, 4))).tolist()
        block_b = (np.array([0] * 8 + [1] * 8)[:, None] * 1 + rng.normal(0, 0.5, (16, 4))).tolist()
        body = client.post(
            "/metabolomics/fusion",
            json={
                "blocks": {"nmr": block_a, "ms": block_b},
                "labels": labels,
                "test": "all",
            },
        ).json()
        assert "mfa" in body and "consensus_pca" in body
        assert "permutation" in body


class TestEnrich:
    def test_enrichment(self, client):
        body = client.post(
            "/metabolomics/enrich",
            json={"feature_ids": ["FT5", "FT4"], "compound_map": {"FT5": "IK-A"}},
        ).json()
        enrichment = body["enrichment"]
        assert enrichment["pathways"]
        assert enrichment["test"]
        assert enrichment["background_size"] > 0
        assert len(body["provenance_manifest_id"]) == 64
