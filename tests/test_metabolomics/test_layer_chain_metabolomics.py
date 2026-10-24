"""Metabolomics layer chain integration (ADR-0068..ADR-0072): pipeline
order, skip visibility, validation-failure evidence, and the reporting
checklist, run over the real Curcuma fixture preprocessing."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from phyto_shared.metabolomics import MetabolomicsInput

from orchestrator.layer_chain import apply_metabolomics_layers

CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"


@pytest.fixture(scope="module")
def preprocessing_result():
    from nmr_preproc.pipeline import preprocess_experiments

    dirs = sorted(
        str(p)
        for p in (CURCUMA / "Curcuma_longa_NMR_dataset").iterdir()
        if p.is_dir()
    )
    return preprocess_experiments(dirs)


@pytest.fixture(scope="module")
def sheet() -> dict[str, dict[str, str]]:
    with (CURCUMA / "sample_sheet_Curcuma_longa.csv").open() as fh:
        return {
            row["sample_id"]: {k: v for k, v in row.items() if k != "sample_id"}
            for row in csv.DictReader(fh)
        }


def _input(**overrides) -> MetabolomicsInput:
    fields = dict(
        kind="nmr_bruker_dirs",
        paths=["curcuma"],
        sample_sheet_path="sample_sheet_Curcuma_longa.csv",
        continuous_covariate="storage_day",
        driver_peak_ppm=7.55,
    )
    fields.update(overrides)
    return MetabolomicsInput(**fields)


class TestPipelineOrder:
    def test_full_chain_runs_every_layer(self, preprocessing_result, sheet) -> None:
        result = apply_metabolomics_layers(_input(), sheet, preprocessing_result)
        relations = [e.relation for e in result.evidence]
        assert "nmr_preprocessing_applied" in relations
        assert "pca_model" in relations
        assert "opls_regression_model" in relations
        assert "vip_feature" in relations
        assert "reporting_checklist" in relations
        assert result.skipped_layers == []

    def test_pca_runs_before_supervised_model(self, preprocessing_result, sheet) -> None:
        result = apply_metabolomics_layers(_input(), sheet, preprocessing_result)
        relations = [e.relation for e in result.evidence]
        assert relations.index("pca_model") < relations.index("opls_regression_model")

    def test_model_evidence_attached_to_result(
        self, preprocessing_result, sheet
    ) -> None:
        result = apply_metabolomics_layers(_input(), sheet, preprocessing_result)
        assert result.model_evidence is not None
        assert result.model_evidence.model_kind == "opls_regression"
        assert result.model_evidence.q2 is not None
        assert result.pca_evidence is not None
        assert result.pca_evidence.n_samples == 20

    def test_preprocessing_record_is_the_real_one(
        self, preprocessing_result, sheet
    ) -> None:
        result = apply_metabolomics_layers(_input(), sheet, preprocessing_result)
        assert result.preprocessing_record is preprocessing_result.record
        assert result.sample_ids == preprocessing_result.sample_ids


class TestSkipVisibility:
    def test_no_preprocessing_skips_everything_visibly(self) -> None:
        result = apply_metabolomics_layers(_input(), None, None)
        skipped_tags = [s["layer"] for s in result.skipped_layers]
        assert skipped_tags == [
            "preprocessing", "pca", "supervised_model",
            "vip_selection", "stocsy", "reporting_checklist",
        ]
        # every skip is ALSO evidence, never only a log line
        relations = [e.relation for e in result.evidence]
        assert "nmr_preprocessing_skipped" in relations
        assert "statistical_model_skipped" in relations
        assert all(e.strength == 0.0 for e in result.evidence)

    def test_no_covariate_skips_supervised_model(
        self, preprocessing_result, sheet
    ) -> None:
        result = apply_metabolomics_layers(
            _input(continuous_covariate=None), sheet, preprocessing_result
        )
        tags = [s["layer"] for s in result.skipped_layers]
        assert "supervised_model" in tags
        # PCA and STOCSY still ran: additive, not all-or-nothing
        relations = [e.relation for e in result.evidence]
        assert "pca_model" in relations
        assert "stocsy_correlation" in relations

    def test_pca_min_samples_gate(self, preprocessing_result, sheet) -> None:
        result = apply_metabolomics_layers(
            _input(), sheet, preprocessing_result, pca_min_samples=50,
        )
        tags = [s["layer"] for s in result.skipped_layers]
        assert "pca" in tags
        assert any("fewer than 50" in s["reason"] for s in result.skipped_layers)


class TestValidationFailureEvidence:
    def test_constant_covariate_is_model_validation_failure(
        self, preprocessing_result, sheet
    ) -> None:
        # a constant covariate makes the regression meaningless: recorded
        # as evidence with the named reason, chain continues to VIP/STOCSY
        broken = {
            sid: {**row, "storage_day": "5"} for sid, row in sheet.items()
        }
        result = apply_metabolomics_layers(_input(), broken, preprocessing_result)
        relations = [e.relation for e in result.evidence]
        assert "model_validation_failed" in relations
        failed = next(
            e for e in result.evidence if e.relation == "model_validation_failed"
        )
        assert "constant" in failed.spectral_feature
        # downstream layers still ran
        assert "reporting_checklist" in relations


class TestReportingChecklist:
    def test_checklist_built_from_real_run(self, preprocessing_result, sheet) -> None:
        result = apply_metabolomics_layers(_input(), sheet, preprocessing_result)
        assert result.checklist is not None
        assert 0.0 <= result.checklist.completeness <= 1.0
        # preprocessing was fully recorded, so the preprocessing fields pass
        assert "normalisation" not in result.checklist.missing_fields
        # no supervised model => model fields are missing, not fabricated
        assert result.checklist.missing_fields  # something is always honest-missing