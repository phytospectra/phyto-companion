"""Feature detection and table helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from metabolomics.feature_detection import (
    DetectParams,
    FeatureTable,
    MZmineDetector,
    MockDetector,
    get_detector,
    matrix_to_table,
    table_to_matrix,
)


def _paths() -> list[Path]:
    return [
        Path("/data/control_1.mzML"),
        Path("/data/control_2.mzML"),
        Path("/data/treated_1.mzML"),
        Path("/data/treated_2.mzML"),
        Path("/data/qc_1.mzML"),
    ]


@pytest.fixture()
def table() -> FeatureTable:
    return MockDetector().detect(_paths(), DetectParams())


class TestMockDetector:
    def test_schema_and_ground_truth(self, table):
        assert len(table.samples) == 5
        assert len(table.features) == 7
        groups = {s.sample_id: s.group for s in table.samples}
        assert groups["control_1"] == "control"
        assert groups["treated_1"] == "treated"
        assert groups["qc_1"] == "qc"
        assert [s.is_qc for s in table.samples].count(True) == 1

    def test_ground_truth_elevation(self, table):
        ft5 = next(f for f in table.features if f.feature_id == "FT5")
        assert ft5.intensities["treated_1"] == pytest.approx(
            2 * ft5.intensities["control_1"]
        )

    def test_internal_standard_is_flat(self, table):
        ft1 = next(f for f in table.features if f.feature_id == "FT1")
        assert len(set(ft1.intensities.values())) == 1

    def test_drift_feature_increases_with_injection_order(self, table):
        ft7 = next(f for f in table.features if f.feature_id == "FT7")
        ordered = sorted(table.samples, key=lambda s: s.injection_order)
        values = [ft7.intensities[s.sample_id] for s in ordered]
        assert values == sorted(values)

    def test_deterministic(self):
        first = MockDetector().detect(_paths(), DetectParams())
        second = MockDetector().detect(_paths(), DetectParams())
        assert first.model_dump() == second.model_dump()

    def test_empty_input_raises(self):
        with pytest.raises(InvalidInputError):
            MockDetector().detect([], DetectParams())

    def test_min_intensity_filters(self):
        table = MockDetector().detect(_paths(), DetectParams(min_intensity=100_000.0))
        assert all(
            max(f.intensities.values()) >= 100_000.0 for f in table.features
        )


class TestMZmineDetector:
    def test_fails_closed_without_installation(self, tmp_path):
        real_file = tmp_path / "sample.mzML"
        real_file.write_bytes(b"<indexedmzML/>")
        detector = MZmineDetector(mzmine_home="")
        with pytest.raises(CloudServiceError) as excinfo:
            detector.detect([real_file], DetectParams())
        assert "MockDetector" in str(excinfo.value)

    def test_rejects_nonexistent_input_files(self, monkeypatch):
        # The detector must validate inputs before any subprocess work.
        monkeypatch.setenv("MZMINE_HOME", "/opt/mzmine")
        detector = MZmineDetector(mzmine_home="/opt/mzmine")
        with pytest.raises(InvalidInputError, match="do not exist"):
            detector.detect(
                [Path("/nonexistent/sample.mzML")], DetectParams()
            )

    def test_detect_is_blocked_until_contract_validated(self, monkeypatch, tmp_path):
        # ADR-0084: even with an "installed" MZmine the batch contract was
        # never validated against a real installation, so detect() refuses
        # rather than return mis-shaped results. This pin stays until the
        # live integration test (real binary, XML batch, verified export
        # schema) replaces it.
        fake_bin = tmp_path / "mzmine"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        monkeypatch.setattr(
            "metabolomics.feature_detection.shutil.which", lambda _: str(fake_bin)
        )
        real_file = tmp_path / "sample.mzML"
        real_file.write_bytes(b"<indexedmzML/>")
        detector = MZmineDetector(mzmine_home=str(tmp_path))
        with pytest.raises(CloudServiceError, match="unverified"):
            detector.detect([real_file], DetectParams())


class TestFactory:
    def test_dry_run_returns_mock(self, monkeypatch):
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        assert isinstance(get_detector(), MockDetector)

    def test_local_plane_defaults_mock(self, monkeypatch):
        # Local plane (PHYTO_ENV unset => local => dry-run): the split-plane
        # default is the mock, no MZMINE_HOME needed.
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        monkeypatch.delenv("PHYTO_ENV", raising=False)
        assert isinstance(get_detector(), MockDetector)

    def test_real_mode_without_mzmine_raises_never_mock(self, monkeypatch):
        # Fail-closed (ADR-0078 rule): a caller that asserts real mode gets
        # an error naming the missing variable, never a silent mock.
        from metabolomics.runtime_config import MetabolomicsConfig

        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.delenv("MZMINE_HOME", raising=False)
        config = MetabolomicsConfig.from_env()
        assert not config.dry_run
        with pytest.raises(CloudServiceError, match="MZMINE_HOME"):
            get_detector(config=config)

    def test_real_mode_with_mzmine_returns_real_detector(self, monkeypatch):
        from metabolomics.runtime_config import MetabolomicsConfig

        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_DRY_RUN", raising=False)
        monkeypatch.setenv("MZMINE_HOME", "/opt/mzmine")
        config = MetabolomicsConfig.from_env()
        detector = get_detector(config=config)
        assert isinstance(detector, MZmineDetector)
        assert detector.mzmine_home == "/opt/mzmine"


class TestMatrixHelpers:
    def test_roundtrip(self, table):
        sample_ids, feature_ids, matrix = table_to_matrix(table)
        assert matrix.shape == (5, 7)
        rebuilt = matrix_to_table(table, sample_ids, feature_ids, matrix)
        assert rebuilt.model_dump() == table.model_dump()

    def test_matrix_values_match_intensities(self, table):
        sample_ids, feature_ids, matrix = table_to_matrix(table)
        for j, feature in enumerate(table.features):
            for i, sample_id in enumerate(sample_ids):
                assert matrix[i, j] == feature.intensities[sample_id]
