"""Service endpoints (ADR-0068/0069): the HTTP contract returns APPLIED
with the serialised preprocessing result, or SKIPPED with the named
reason -- never a guess, never an empty success."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from nmr_preproc.main import create_app as create_nmr_app
from lcms_preproc.main import create_app as create_lcms_app

CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"

MZMINE_CSV = (
    "row ID,row m/z,row retention time,C04_Rep1,C04_Rep2,C08_Rep1,C08_Rep2\n"
    "1,104.1070,1.12,1200.0,1234.5,2210.0,2301.0\n"
    "2,146.0863,2.45,90.0,99.1,410.0,395.0\n"
    "3,190.0500,3.02,51.0,55.2,58.0,52.0\n"
)


class TestNmrPreprocEndpoint:
    def test_health(self) -> None:
        client = TestClient(create_nmr_app())
        assert client.get("/health").status_code == 200

    def test_applied_returns_matrix_and_record(self) -> None:
        dirs = sorted(
            str(p)
            for p in (CURCUMA / "Curcuma_longa_NMR_dataset").iterdir()
            if p.is_dir()
        )
        client = TestClient(create_nmr_app())
        response = client.post(
            "/nmr-preproc/preprocess", json={"experiment_dirs": dirs}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert len(body["sample_ids"]) == 20
        assert body["record"]["modality"] == "nmr"
        assert len(body["ppm"]) == len(body["matrix"][0])

    def test_skipped_names_the_reason(self) -> None:
        client = TestClient(create_nmr_app())
        response = client.post(
            "/nmr-preproc/preprocess", json={"experiment_dirs": ["/nope"]}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "skipped"
        assert "not a directory" in body["reason"]


class TestLcmsPreprocEndpoint:
    def test_health(self) -> None:
        client = TestClient(create_lcms_app())
        assert client.get("/health").status_code == 200

    def test_applied_returns_adapted_table(self) -> None:
        client = TestClient(create_lcms_app())
        response = client.post(
            "/lcms-preproc/ingest", json={"csv_text": MZMINE_CSV, "normalisation": "none"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert len(body["feature_ids"]) == 3
        assert body["sample_ids"] == ["C04_Rep1", "C04_Rep2", "C08_Rep1", "C08_Rep2"]
        assert body["record"]["modality"] == "lcms"

    def test_unrecognised_export_skips_with_reason(self) -> None:
        client = TestClient(create_lcms_app())
        response = client.post(
            "/lcms-preproc/ingest",
            json={"csv_text": "a,b\n1,2\n", "normalisation": "none"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "skipped"
        assert body["reason"]