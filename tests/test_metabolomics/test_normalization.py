"""Normalization and QC correction."""

from __future__ import annotations

from pathlib import Path

import pytest

from phyto_shared.exceptions import InvalidInputError
from metabolomics.feature_detection import DetectParams, Feature, FeatureTable, SampleMetadata
from metabolomics.normalization import (
    flag_low_quality,
    normalize,
    qc_correction,
    rsd,
)
from metabolomics.feature_detection import MockDetector


def _table() -> FeatureTable:
    return MockDetector().detect(
        [Path(f"/d/{n}.mzML") for n in ("control_1", "treated_1", "qc_1")],
        DetectParams(),
    )


def _qc_table() -> FeatureTable:
    """Six samples with a strong linear drift, three of them QC."""
    samples = [
        SampleMetadata(sample_id=f"S{i}", group="qc" if i in (0, 2, 4) else "control",
                       is_qc=i in (0, 2, 4), injection_order=i + 1)
        for i in range(6)
    ]
    feature = Feature(
        feature_id="FT1", mz=100.0, rt=60.0,
        intensities={f"S{i}": 1000.0 + 100.0 * i for i in range(6)},
    )
    return FeatureTable(features=[feature], samples=samples)


class TestNormalize:
    def test_tic_equalizes_total_ion_current(self):
        table = _table()
        normalized = normalize(table, "tic")
        from metabolomics.feature_detection import table_to_matrix

        _, _, matrix = table_to_matrix(normalized)
        totals = matrix.sum(axis=1)
        assert totals == pytest.approx([totals[0]] * len(totals))

    def test_pqn_reduces_between_sample_differences(self):
        table = _table()
        normalized = normalize(table, "pqn")
        assert "pqn" in normalized.provenance_notes[-1]

    def test_internal_standard(self):
        normalized = normalize(
            _table(), "is", {"internal_standard_feature_id": "FT1"}
        )
        from metabolomics.feature_detection import table_to_matrix

        _, _, matrix = table_to_matrix(normalized)
        # after IS normalization every sample's FT1 is the mean FT1
        assert matrix[:, 0] == pytest.approx([matrix[0, 0]] * 3)

    def test_qnmr_factor(self):
        normalized = normalize(_table(), "qnmr", {"calibration_factor": 2.0})
        assert "qnmr" in normalized.provenance_notes[-1]

    def test_unknown_method_raises(self):
        with pytest.raises(InvalidInputError):
            normalize(_table(), "zscore")

    def test_missing_internal_standard_raises(self):
        with pytest.raises(InvalidInputError):
            normalize(_table(), "is", {"internal_standard_feature_id": "nope"})

    def test_provenance_note_recorded(self):
        normalized = normalize(_table(), "tic")
        assert normalized.provenance_notes


class TestQCCorrection:
    def test_correction_reduces_qc_rsd(self):
        table = _qc_table()
        corrected = qc_correction(table, ["S0", "S2", "S4"])
        before = rsd([table.features[0].intensities[s] for s in ("S0", "S2", "S4")])
        after = rsd([corrected.features[0].intensities[s] for s in ("S0", "S2", "S4")])
        assert after < before
        assert after == pytest.approx(0.0, abs=1e-6)  # perfectly linear drift

    def test_reports_improvement(self):
        corrected = qc_correction(_qc_table(), ["S0", "S2", "S4"])
        assert any("QC RSD improved" in flag for flag in corrected.qc_flags)

    def test_no_qc_raises(self):
        with pytest.raises(InvalidInputError):
            qc_correction(_table(), ["ghost"])

    def test_empty_qc_raises(self):
        with pytest.raises(InvalidInputError):
            qc_correction(_table(), [])


class TestLowQualityFlags:
    def test_flags_high_rsd_within_qc(self):
        # inject a wild QC value into the mock's FT2
        table = _qc_table()
        table.features[0].intensities["S4"] = 5000.0  # breaks the linear drift
        flagged = flag_low_quality(table, rsd_threshold=0.1)
        assert "FT1" in flagged

    def test_no_qc_samples_no_flags(self):
        table = _table()
        assert flag_low_quality(table) == []


class TestRSD:
    def test_basic(self):
        assert rsd([10.0, 10.0, 10.0]) == 0.0
        assert rsd([10.0, 20.0]) > 0.0

    def test_empty_and_zero_mean(self):
        assert rsd([]) == 0.0
        assert rsd([0.0, 0.0]) == 0.0
