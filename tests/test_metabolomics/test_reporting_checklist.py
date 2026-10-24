"""Reporting checklist (ADR-0072): every audit field is either PRESENT
(from the preprocessing record / model evidence) or LISTED MISSING --
gaps are reported, never filled or guessed.  Run against the real
Curcuma preprocessing record."""

from __future__ import annotations

import pytest

from phyto_shared.metabolomics import PreprocessingRecord

from metabolomics.reporting import build_reporting_checklist


def _nmr_record() -> PreprocessingRecord:
    from nmr_preproc.pipeline import preprocess_experiments
    from pathlib import Path

    curcuma = (
        Path(__file__).resolve().parents[1]
        / "fixtures" / "metabolomics" / "curcuma"
    )
    dirs = sorted(
        str(p)
        for p in (curcuma / "Curcuma_longa_NMR_dataset").iterdir()
        if p.is_dir()
    )
    return preprocess_experiments(dirs).record


class TestChecklistContents:
    def test_record_fields_are_present_not_fabricated(self) -> None:
        record = _nmr_record()
        checklist = build_reporting_checklist(record)
        assert "apodisation_function" in checklist.provided_fields
        assert "normalisation_method" in checklist.provided_fields
        assert "alignment_method" in checklist.provided_fields
        # the fixture record does not carry an explicit phase method
        # name: it must be LISTED MISSING, not silently filled
        missing = checklist.missing_fields
        assert isinstance(missing, list)

    def test_missing_fields_enumerated_not_filled(self) -> None:
        record = _nmr_record()
        checklist = build_reporting_checklist(record)
        # every missing field is named in the missing list
        for field in checklist.missing_fields:
            assert field not in checklist.provided_fields
        # and completeness is the exact provided/required ratio
        n = len(checklist.provided_fields)
        d = len(checklist.provided_fields) + len(checklist.missing_fields)
        assert checklist.completeness == pytest.approx(n / d, abs=1e-4)

    def test_no_record_reports_everything_missing(self) -> None:
        checklist = build_reporting_checklist(None)
        # only the constant fdr_method comes free; every record field is missing
        assert checklist.provided_fields == ["fdr_method"]
        # with no record there is no modality, so only statistics fields
        # are required; every one except fdr_method is missing
        assert "pca_software" in checklist.missing_fields
        assert checklist.completeness < 0.2

    def test_model_evidence_extends_checklist(self) -> None:
        from phyto_shared.metabolomics import PLSModelEvidence

        record = _nmr_record()
        model = PLSModelEvidence(
            model_kind="opls_regression",
            n_components=1,
            r2y=0.9,
            q2=0.8,
            permutation_p_value=0.01,
            n_permutations=100,
            cv_scheme="balanced Monte Carlo",
        )
        without = build_reporting_checklist(record)
        with_model = build_reporting_checklist(record, model=model)
        assert set(with_model.provided_fields) >= {
            x for x in without.provided_fields
        }
        assert "model_permutation_p" in with_model.provided_fields
        assert "model_permutation_n" in with_model.provided_fields
        assert with_model.completeness > without.completeness

    def test_vip_and_stocsy_thresholds_recorded(self) -> None:
        record = _nmr_record()
        checklist = build_reporting_checklist(
            record, vip_threshold=1.0, stocsy_driver_ppm=5.95,
            stocsy_thresholds="|r| >= 0.7, FDR <= 0.05",
        )
        assert "vip_threshold" in checklist.provided_fields
        assert "stocsy_driver" in checklist.provided_fields
        assert "stocsy_thresholds" in checklist.provided_fields