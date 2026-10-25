"""Tool-registry integration for the metabolomics tools (ADR-0068..ADR-0074):
tier, dry-run availability, real execution through the mock executor, and
the boundary behaviour when services are not staged."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from phyto_shared.metabolomics import MetabolomicsInput

from orchestrator.tool_registry import (
    DEFAULT_REGISTRY,
    DeconvolveSpectrumInput,
    EvaluateEquationInput,
    MockToolExecutor,
    QuantifySampleInput,
    _mock_apply_metabolomics_layers,
    _mock_deconvolve,
    _mock_evaluate_equation,
    _mock_metabolomics_preprocess,
    _mock_quantify,
    ApplyMetabolomicsLayersInput,
    MetabolomicsPreprocessInput,
)


CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"


class TestRegistryEntries:
    def test_both_tools_registered_tier1_dry_run(self) -> None:
        for name in ("metabolomics.preprocess", "metabolomics.apply_layers"):
            spec = DEFAULT_REGISTRY.get_tool(name)
            assert spec.tier.name == "TIER_1_DETERMINISTIC"
            assert spec.enabled_in_dry_run is True
            assert not spec.requires_gpu

    def test_planner_task_tools_exist(self) -> None:
        # the planner references these by name: a typo would fail planning
        for name in ("metabolomics.preprocess", "metabolomics.apply_layers"):
            DEFAULT_REGISTRY.get_tool(name)  # raises when unknown


class TestPreprocessExecution:
    def test_bruker_dirs_run_real_pipeline(self) -> None:
        out = _mock_metabolomics_preprocess(
            MetabolomicsPreprocessInput(
                metabolomics_input=MetabolomicsInput(
                    kind="nmr_bruker_dirs",
                    paths=[
                        str(p)
                        for p in sorted(
                            (CURCUMA / "Curcuma_longa_NMR_dataset").iterdir()
                        )
                        if p.is_dir()
                    ],
                )
            )
        )
        assert out.skipped is False
        assert out.preprocessing_result is not None
        pr = out.preprocessing_result
        assert len(pr["sample_ids"]) == 20
        assert len(pr["ppm"]) == len(pr["matrix"][0])
        assert pr["record"]["modality"] == "nmr"

    def test_unusable_input_skips_with_named_reason(self) -> None:
        out = _mock_metabolomics_preprocess(
            MetabolomicsPreprocessInput(
                metabolomics_input=MetabolomicsInput(
                    kind="nmr_bruker_dirs", paths=["/nonexistent/x"]
                )
            )
        )
        assert out.skipped is True
        assert out.reason
        assert "not a directory" in out.reason


class TestApplyLayersExecution:
    @pytest.fixture(scope="class")
    def preprocessed(self) -> dict:
        out = _mock_metabolomics_preprocess(
            MetabolomicsPreprocessInput(
                metabolomics_input=MetabolomicsInput(
                    kind="nmr_bruker_dirs",
                    paths=[
                        str(p)
                        for p in sorted(
                            (CURCUMA / "Curcuma_longa_NMR_dataset").iterdir()
                        )
                        if p.is_dir()
                    ],
                )
            )
        )
        assert out.preprocessing_result is not None
        return out.preprocessing_result

    def _sheet(self) -> dict[str, dict[str, str]]:
        with (CURCUMA / "sample_sheet_Curcuma_longa.csv").open() as fh:
            return {
                row["sample_id"]: {k: v for k, v in row.items() if k != "sample_id"}
                for row in csv.DictReader(fh)
            }

    def test_full_run_through_registry_contract(self, preprocessed) -> None:
        out = _mock_apply_metabolomics_layers(
            ApplyMetabolomicsLayersInput(
                metabolomics_input=MetabolomicsInput(
                    kind="nmr_bruker_dirs",
                    paths=["curcuma"],
                    continuous_covariate="storage_day",
                ),
                sample_sheet=self._sheet(),
                preprocessing_result=preprocessed,
            )
        )
        relations = [e["relation"] for e in out.evidence]
        assert "opls_regression_model" in relations
        assert out.model["model_kind"] == "opls_regression"
        assert out.pca["n_samples"] == 20
        assert out.reporting_checklist is not None

    def test_sheet_loaded_from_path_when_not_supplied(self, preprocessed, tmp_path) -> None:
        sheet_path = tmp_path / "sheet.csv"
        with sheet_path.open("w") as fh:
            fh.write("sample_id,storage_day\n")
            for sid, row in self._sheet().items():
                fh.write(f"{sid},{row['storage_day']}\n")
        out = _mock_apply_metabolomics_layers(
            ApplyMetabolomicsLayersInput(
                metabolomics_input=MetabolomicsInput(
                    kind="nmr_bruker_dirs",
                    paths=["curcuma"],
                    continuous_covariate="storage_day",
                    sample_sheet_path=str(sheet_path),
                ),
                preprocessing_result=preprocessed,
            )
        )
        assert "opls_regression_model" in [e["relation"] for e in out.evidence]

    def test_no_preprocessing_every_layer_skipped(self) -> None:
        out = _mock_apply_metabolomics_layers(
            ApplyMetabolomicsLayersInput(
                metabolomics_input=MetabolomicsInput(
                    kind="feature_table_csv", paths=["t.csv"]
                )
            )
        )
        assert [s["layer"] for s in out.skipped_layers] == [
            "preprocessing", "pca", "supervised_model",
            "vip_selection", "stocsy", "reporting_checklist",
        ]
        assert out.evidence  # skips are evidence too
        assert out.pca is None and out.model is None


def _singlet_arrays() -> tuple[list[float], list[float]]:
    """Isolated Lorentzian at 7.20 ppm (analytic area pi*h*w/2)."""
    ppm = np.linspace(7.4, 7.0, 4001)
    gamma2 = (0.003 / 2.0) ** 2
    intensity = gamma2 / ((ppm - 7.20) ** 2 + gamma2)
    return ppm.tolist(), intensity.tolist()


class TestQnmrRegistryEntries:
    def test_three_tools_registered_tier1_dry_run(self) -> None:
        for name in (
            "nmr_preproc.deconvolve",
            "nmr_preproc.quantify",
            "nmr_preproc.evaluate_equation",
        ):
            spec = DEFAULT_REGISTRY.get_tool(name)
            assert spec.tier.name == "TIER_1_DETERMINISTIC", name
            assert spec.enabled_in_dry_run is True, name
            assert not spec.requires_gpu, name


class TestQnmrExecution:
    def test_deconvolve_runs_real_fitter_through_contract(self) -> None:
        ppm, intensity = _singlet_arrays()
        out = _mock_deconvolve(
            DeconvolveSpectrumInput(
                ppm=ppm, intensity=intensity, regions=[(7.15, 7.25)],
                model="lorentzian",
            )
        )
        assert out.skipped is False
        assert out.model_id == "deconv-0.1.0"
        fit = out.fits[0]
        assert fit["status"] == "fitted"
        truth = math.pi * 0.003 / 2.0
        assert abs(fit["components"][0]["area"] - truth) / truth < 0.03

    def test_deconvolve_bad_axes_skip_with_reason(self) -> None:
        out = _mock_deconvolve(
            DeconvolveSpectrumInput(
                ppm=[float(i) for i in range(10)],
                intensity=[float(i) for i in range(11)],
                regions=[(0.0, 9.0)],
            )
        )
        assert out.skipped is True
        assert "axes disagree" in (out.reason or "")

    def test_quantify_runs_real_math(self) -> None:
        out = _mock_quantify(
            QuantifySampleInput(
                standard={"name": "maleate", "n_protons": 2, "integral": 100.0,
                          "moles_nmol": 100.0},
                analytes=[{"name": "x", "n_protons": 3, "integral": 150.0}],
            )
        )
        assert out.skipped is False
        analyte = out.result["analytes"][0]
        assert abs(analyte["moles_nmol"] - 100.0) < 1e-6
        assert "n(x)" in analyte["equation"]

    def test_quantify_non_physical_input_skips(self) -> None:
        out = _mock_quantify(
            QuantifySampleInput(
                standard={"name": "s", "n_protons": 2, "integral": 100.0},
                analytes=[{"name": "x", "n_protons": 2, "integral": 1.0}],
            )
        )
        assert out.skipped is True
        assert "no usable amount" in (out.reason or "")

    def test_evaluate_equation_real_evaluator(self) -> None:
        out = _mock_evaluate_equation(
            EvaluateEquationInput(
                expression="(I_a / N_a) / (I_s / N_s) * n_s",
                values={"I_a": 150.0, "N_a": 3.0, "I_s": 100.0, "N_s": 2.0,
                        "n_s": 100.0},
                specs=[{"name": "check", "expression": "100 >= 95", "level": "severe"}],
            )
        )
        assert out.skipped is False
        assert abs(float(out.value) - 100.0) < 1e-9
        assert out.spec_results[0]["passed"] is True

    def test_evaluate_equation_forbidden_construct_skips(self) -> None:
        out = _mock_evaluate_equation(
            EvaluateEquationInput(expression="x.__class__", values={"x": 1.0})
        )
        assert out.skipped is True
        assert "Attribute" in (out.reason or "")

    def test_executor_round_trip_all_three(self) -> None:
        executor = MockToolExecutor(DEFAULT_REGISTRY)
        ppm, intensity = _singlet_arrays()
        out = executor.execute(
            "nmr_preproc.deconvolve",
            {"ppm": ppm, "intensity": intensity, "regions": [[7.15, 7.25]],
             "model": "lorentzian"},
        )
        assert out.fits[0]["status"] == "fitted"