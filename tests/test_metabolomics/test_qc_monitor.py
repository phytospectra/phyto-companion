"""QC monitoring: report, alerts, rerun suggestion."""

from __future__ import annotations

from pathlib import Path

import pytest

from phyto_shared.exceptions import InvalidInputError
from metabolomics.feature_detection import (
    DetectParams,
    Feature,
    FeatureTable,
    MockDetector,
    SampleMetadata,
)
from metabolomics.qc_monitor import (
    MAX_QC_RSD,
    qc_alerts,
    qc_report,
    suggest_rerun,
)


def _qc_table(noisy: bool = False) -> FeatureTable:
    samples = [
        SampleMetadata(sample_id=f"S{i}", is_qc=i in (0, 2, 4),
                       group="qc" if i in (0, 2, 4) else "control", injection_order=i)
        for i in range(6)
    ]
    tight = Feature(
        feature_id="FT1", mz=100.0, rt=60.0,
        intensities={"S0": 1000.0, "S1": 5000.0, "S2": 1010.0,
                     "S3": 5100.0, "S4": 990.0, "S5": 4900.0},
    )
    wild = Feature(
        feature_id="FT2", mz=115.0, rt=120.0,
        intensities={"S0": 100.0, "S1": 5000.0, "S2": 900.0,
                     "S3": 5100.0, "S4": 2500.0, "S5": 4900.0}
        if noisy else
        {"S0": 800.0, "S1": 5000.0, "S2": 810.0, "S3": 5100.0, "S4": 790.0, "S5": 4900.0},
    )
    return FeatureTable(features=[tight, wild], samples=samples)


class TestQCReport:
    def test_report_fields(self):
        report = qc_report(_qc_table(), ["S0", "S2", "S4"])
        assert report.n_qc_samples == 3
        assert set(report.qc_rsd) == {"FT1", "FT2"}
        assert report.qc_pca_dispersion >= 0.0
        assert report.notes

    def test_tight_qc_has_low_rsd(self):
        report = qc_report(_qc_table(), ["S0", "S2", "S4"])
        assert report.qc_rsd["FT1"] < MAX_QC_RSD

    def test_no_qc_samples_raise(self):
        with pytest.raises(InvalidInputError):
            qc_report(_qc_table(), ["ghost"])


class TestAlerts:
    def test_noise_feature_triggers_rsd_alert(self):
        report = qc_report(_qc_table(noisy=True), ["S0", "S2", "S4"])
        alerts = qc_alerts(report)
        assert any(alert.code == "qc_rsd_exceeded" for alert in alerts)

    def test_clean_data_no_severe_alerts(self):
        report = qc_report(_qc_table(), ["S0", "S2", "S4"])
        assert not any(alert.level == "severe" for alert in qc_alerts(report))

    def test_suggest_rerun_tracks_severe(self):
        clean = qc_report(_qc_table(), ["S0", "S2", "S4"])
        noisy = qc_report(_qc_table(noisy=True), ["S0", "S2", "S4"])
        assert suggest_rerun(clean) is False
        assert suggest_rerun(noisy) is True

    def test_mock_detector_table_reports(self):
        table = MockDetector().detect(
            [Path(f"/d/{n}.mzML") for n in ("control_1", "qc_1", "qc_2")],
            DetectParams(),
        )
        report = qc_report(table, ["qc_1", "qc_2"])
        assert report.n_qc_samples == 2
