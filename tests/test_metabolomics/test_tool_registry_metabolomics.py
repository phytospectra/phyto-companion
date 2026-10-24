"""Tool-registry integration for the metabolomics tools (ADR-0068..ADR-0072):
tier, dry-run availability, real execution through the mock executor, and
the boundary behaviour when services are not staged."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from phyto_shared.metabolomics import MetabolomicsInput

from orchestrator.tool_registry import (
    DEFAULT_REGISTRY,
    _mock_apply_metabolomics_layers,
    _mock_metabolomics_preprocess,
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