"""qNMR service endpoints (ADR-0073/0074): the HTTP contract returns
APPLIED with serialised fits/integrals/results, or SKIPPED with the
named reason -- never a guess.  The Curcuma fixture provides one
real-data smoke test: the curcuminoid aromatic region of CL_D00_S1
must come back with a region record whose status is one of the three
declared states (fitting quality on real data is asserted in the
module tests against synthetic ground truth, not here)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from nmr_preproc.main import create_app

CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"


def _synthetic_spectrum() -> dict:
    """Isolated Lorentzian singlet at 7.20 ppm, area pi*h*w/2."""
    ppm = np.linspace(7.4, 7.0, 4001).tolist()
    gamma2 = (0.003 / 2.0) ** 2
    intensity = [
        1.0 * gamma2 / ((x - 7.20) ** 2 + gamma2) for x in ppm
    ]
    return {"ppm": ppm, "intensity": intensity}


class TestDeconvolveEndpoint:
    def test_inline_spectrum_fits(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/deconvolve",
            json={
                "spectrum": _synthetic_spectrum(),
                "regions": [[7.15, 7.25]],
                "params": {"model": "lorentzian", "sf_mhz": 500.0},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert body["model_id"] == "deconv-0.1.0"
        fit = body["fits"][0]
        assert fit["status"] == "fitted"
        assert len(fit["components"]) == 1
        comp = fit["components"][0]
        assert comp["center_ppm"] == 7.20 or abs(comp["center_ppm"] - 7.20) < 0.002
        expected_area = math.pi * 1.0 * 0.003 / 2.0
        assert abs(comp["area"] - expected_area) / expected_area < 0.03

    def test_bad_directory_skips_with_reason(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/deconvolve",
            json={
                "spectrum": {"experiment_dir": "/nonexistent/exp"},
                "regions": [[7.0, 8.0]],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "skipped"
        assert "not a directory" in body["reason"]

    def test_neither_dir_nor_arrays_skips(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/deconvolve",
            json={"spectrum": {}, "regions": [[7.0, 8.0]]},
        )
        body = response.json()
        assert body["status"] == "skipped"
        assert "experiment_dir or both ppm and intensity" in body["reason"]

    def test_curcuma_real_data_smoke(self) -> None:
        """CL_D00_S1 raw FID through the minimal chain; the curcuminoid
        aromatic region must produce a region record in a declared state."""
        exp = str(CURCUMA / "Curcuma_longa_NMR_dataset" / "CL_D00_S1")
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/deconvolve",
            json={
                "spectrum": {"experiment_dir": exp},
                "regions": [[5.5, 6.5]],
                "params": {"snr_threshold": 10.0},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        fit = body["fits"][0]
        assert fit["status"] in {"fitted", "failed", "skipped"}
        assert fit["n_points"] > 100
        assert isinstance(fit["components"], list)


class TestIntegrateEndpoint:
    def test_region_integral_returned(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/integrate",
            json={
                "spectrum": _synthetic_spectrum(),
                "regions": [[6.9, 7.5]],
                "baseline": "none",
            },
        )
        body = response.json()
        assert body["status"] == "applied"
        truth = math.pi * 1.0 * 0.003 / 2.0
        assert abs(body["integrals"][0]["area"] - truth) / truth < 0.01

    def test_bad_baseline_option_skips(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/integrate",
            json={
                "spectrum": _synthetic_spectrum(),
                "regions": [[6.9, 7.5]],
                "baseline": "polynomial",
            },
        )
        body = response.json()
        assert body["status"] == "skipped"
        assert "unknown baseline" in body["reason"]


class TestQuantifyEndpoint:
    def test_internal_standard_math(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/quantify",
            json={
                "standard": {
                    "name": "maleate", "n_protons": 2, "integral": 100.0,
                    "moles_nmol": 100.0,
                },
                "analytes": [
                    {"name": "flavone-A", "n_protons": 3, "integral": 150.0,
                     "molecular_weight": 300.0}
                ],
                "sample_mass_mg": 100.0,
            },
        )
        body = response.json()
        assert body["status"] == "applied"
        analyte = body["result"]["analytes"][0]
        assert analyte["moles_nmol"] == 100.0 or abs(
            analyte["moles_nmol"] - 100.0
        ) < 1e-6
        assert abs(analyte["mass_mg"] - 0.03) < 1e-6
        assert "n(flavone-A)" in analyte["equation"]

    def test_zero_integral_skips_with_reason(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/quantify",
            json={
                "standard": {"name": "s", "n_protons": 2, "integral": 100.0,
                             "moles_nmol": 10.0},
                "analytes": [{"name": "x", "n_protons": 2, "integral": 0.0}],
            },
        )
        body = response.json()
        assert body["status"] == "skipped"
        assert "positive" in body["reason"]


class TestEquationsEndpoint:
    def test_equation_and_specs_in_one_call(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/equations/evaluate",
            json={
                "equation": {
                    "name": "purity_pct",
                    "expression": "m_analyte / m_sample * 100",
                },
                "specs": [
                    {"name": "purity_min", "expression": "m_analyte / m_sample * 100 >= 95",
                     "level": "severe"}
                ],
                "values": {"m_analyte": 3.0, "m_sample": 4.0},
            },
        )
        body = response.json()
        assert body["status"] == "applied"
        assert abs(body["result"]["value"] - 75.0) < 1e-9
        assert body["result"]["substituted"]
        spec = body["spec_results"][0]
        assert spec["passed"] is False
        assert spec["level"] == "severe"
        assert "FAIL" in spec["message"]

    def test_forbidden_construct_skips_with_named_reason(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/equations/evaluate",
            json={
                "equation": {"expression": "x.__class__"},
                "values": {"x": 1.0},
            },
        )
        body = response.json()
        assert body["status"] == "skipped"
        assert "Attribute" in body["reason"]

    def test_empty_request_skips(self) -> None:
        client = TestClient(create_app())
        response = client.post(
            "/nmr-preproc/equations/evaluate",
            json={"values": {"a": 1.0}},
        )
        body = response.json()
        assert body["status"] == "skipped"
        assert "no equation or specs" in body["reason"]
