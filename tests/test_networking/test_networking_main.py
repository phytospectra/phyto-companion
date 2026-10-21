"""FastAPI endpoint tests for the networking service (mock GNPS, no network)."""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from networking.gnps_result_parser import parse_graphml

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gnps"
MGF = (FIXTURES / "sample.mgf").read_bytes()
CLASSICAL = (FIXTURES / "classical_mn.graphml").read_text()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture()
def client():
    from networking import main

    main.get_runtime.cache_clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.get_runtime.cache_clear()


def _submit_ms2(client: TestClient) -> str:
    response = client.post(
        "/networks/ms2",
        json={"mgf_b64": _b64(MGF), "params": {}, "network_type": "classic"},
    )
    assert response.status_code == 200
    return response.json()["job_handle_id"]


def _hsqc_b64(client: TestClient) -> str:
    response = client.post(
        "/networks/hsqc",
        json={
            "spectra": [
                {"spectrum_id": "H1", "sample_id": "S", "peaks": [[1.0, 50.0]]},
                {"spectrum_id": "H2", "sample_id": "S", "peaks": [[1.01, 50.2]]},
            ],
            "distance_threshold": 0.5,
        },
    )
    assert response.status_code == 200
    return _b64(json.dumps(response.json()["network"]).encode())


class TestHealth:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "networking"
        assert body["gnps_client"] == "MockGNPSClient"


class TestMS2Endpoints:
    def test_submit_returns_completed_mock_job(self, client):
        body = client.post(
            "/networks/ms2",
            json={"mgf_b64": _b64(MGF), "params": {}, "network_type": "classic"},
        ).json()
        assert body["status"] == "completed"
        assert body["gnps_task_id"].startswith("MOCKTASK-")
        assert len(body["params_hash"]) == 64
        assert len(body["provenance_manifest_id"]) == 64

    def test_status_returns_parsed_results(self, client):
        job_id = _submit_ms2(client)
        body = client.get(f"/networks/ms2/{job_id}").json()
        assert body["status"] == "completed"
        assert body["n_nodes"] == 5
        assert body["n_edges"] == 2  # residue-delta heuristic links (1,2),(3,4)
        assert body["n_annotations"] == 2
        assert len(body["provenance_manifest_id"]) == 64

    def test_fbmn_requires_feature_table(self, client):
        response = client.post(
            "/networks/ms2",
            json={"mgf_b64": _b64(MGF), "params": {}, "network_type": "fbmn"},
        )
        assert response.status_code == 422

    def test_unknown_job_404(self, client):
        assert client.get("/networks/ms2/nope").status_code == 404


class TestHSQCEndpoint:
    def test_builds_network_synchronously(self, client):
        response = client.post(
            "/networks/hsqc",
            json={
                "spectra": [
                    {"spectrum_id": "H1", "sample_id": "S", "peaks": [[1.0, 50.0]]},
                    {"spectrum_id": "H2", "sample_id": "S", "peaks": [[1.01, 50.2]]},
                ],
                "distance_threshold": 0.5,
            },
        )
        body = response.json()
        assert response.status_code == 200
        assert len(body["network"]["edges"]) == 1
        assert len(body["provenance_manifest_id"]) == 64

    def test_empty_spectra_rejected(self, client):
        response = client.post(
            "/networks/hsqc", json={"spectra": [], "distance_threshold": 0.5}
        )
        assert response.status_code == 422


class TestFusedEndpoint:
    def test_fuse_with_one_link(self, client):
        job_id = _submit_ms2(client)
        response = client.post(
            "/networks/fused",
            json={
                "ms2_job_id": job_id,
                "hsqc_network_b64": _hsqc_b64(client),
                "compound_links": {"scan=1": "H1"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["overlap_report"]["both_modality_nodes"] == 1
        assert body["overlap_report"]["ms2_only_nodes"] == 4
        assert body["overlap_report"]["hsqc_only_nodes"] == 1
        assert len(body["provenance_manifest_id"]) == 64

    def test_unknown_ms2_job_404(self, client):
        response = client.post(
            "/networks/fused",
            json={"ms2_job_id": "nope", "hsqc_network_b64": _b64(b"{}")},
        )
        assert response.status_code == 404

    def test_bad_compound_link_422(self, client):
        job_id = _submit_ms2(client)
        response = client.post(
            "/networks/fused",
            json={
                "ms2_job_id": job_id,
                "hsqc_network_b64": _hsqc_b64(client),
                "compound_links": {"ghost-feature": "H1"},
            },
        )
        assert response.status_code == 422


class TestBioactivityEndpoint:
    def test_prioritizes_perfectly_tracking_feature(self, client):
        parsed = parse_graphml(CLASSICAL)
        fractions = ["F1", "F2", "F3", "F4", "F5", "F6"]
        activity = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        abundances = pd.DataFrame(
            {
                "scan=1": activity,
                "scan=2": [2.0, 2.0, 2.0, 2.0, 2.0, 2.0],
                "scan=3": [-a for a in activity],
                "scan=4": [1.0, 5.0, 2.0, 6.0, 3.0, 4.0],
                "scan=5": activity,
            },
            index=fractions,
        )
        bio = pd.DataFrame({"activity": activity}, index=fractions)
        response = client.post(
            "/networks/bioactivity",
            json={
                "network_b64": _b64(parsed.model_dump_json().encode()),
                "feature_abundances_b64": _b64(abundances.to_csv().encode()),
                "bioactivities_b64": _b64(bio.to_csv().encode()),
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["prioritized_nodes"][0] == "scan=1"
        assert "scan=3" in body["prioritized_nodes"]  # anticorrelated counts
        assert len(body["provenance_manifest_id"]) == 64


class TestExports:
    def test_cytoscape_export(self, client):
        parsed = parse_graphml(CLASSICAL)
        response = client.post(
            "/networks/export/cytoscape",
            json={
                "network_b64": _b64(parsed.model_dump_json().encode()),
                "network_type": "ms2",
            },
        )
        body = response.json()
        assert response.status_code == 200
        assert len(body["payload"]["elements"]["nodes"]) == 5
        assert len(body["provenance_manifest_id"]) == 64

    def test_graphml_export_valid_xml(self, client):
        parsed = parse_graphml(CLASSICAL)
        response = client.post(
            "/networks/export/graphml",
            json={
                "network_b64": _b64(parsed.model_dump_json().encode()),
                "network_type": "ms2",
            },
        )
        assert response.status_code == 200
        ET.fromstring(response.json()["payload"])

    def test_summary_endpoint(self, client):
        parsed = parse_graphml(CLASSICAL)
        response = client.post(
            "/networks/summary",
            json={
                "network_b64": _b64(parsed.model_dump_json().encode()),
                "network_type": "ms2",
            },
        )
        assert "5 nodes" in response.json()["payload"]["summary"]

    def test_invalid_base64_422(self, client):
        response = client.post(
            "/networks/export/cytoscape",
            json={"network_b64": "%%%not-base64%%%", "network_type": "ms2"},
        )
        assert response.status_code == 422
