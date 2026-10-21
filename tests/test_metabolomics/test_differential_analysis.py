"""Differential analysis: group comparisons, volcano, time course, dose response."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from phyto_shared.exceptions import InvalidInputError
from metabolomics.differential_analysis import (
    compare_groups,
    dose_response_analysis,
    time_course_analysis,
    volcano_plot_data,
)
from metabolomics.feature_detection import (
    DetectParams,
    Feature,
    FeatureTable,
    MockDetector,
    SampleMetadata,
)


def _table() -> FeatureTable:
    return MockDetector().detect(
        [Path(f"/d/{n}.mzML") for n in
         ("control_1", "control_2", "control_3",
          "treated_1", "treated_2", "treated_3",
          "treated_high_1", "treated_high_2")],
        DetectParams(),
    )


def _groups() -> dict[str, list[str]]:
    return {
        "control": ["control_1", "control_2", "control_3"],
        "treated": ["treated_1", "treated_2", "treated_3"],
        "high": ["treated_high_1", "treated_high_2"],
    }


class TestCompareGroups:
    def test_each_group_compared_to_reference(self):
        result = compare_groups(_table(), _groups(), "control")
        assert {c.group for c in result.comparisons} == {"treated", "high"}
        assert all(c.reference_group == "control" for c in result.comparisons)

    def test_significant_feature_found(self):
        result = compare_groups(_table(), _groups(), "control")
        treated = next(c for c in result.comparisons if c.group == "treated")
        assert "FT5" in treated.significant_features

    def test_fdr_values_bounded(self):
        result = compare_groups(_table(), _groups(), "control")
        for comparison in result.comparisons:
            assert all(0.0 <= q <= 1.0 for q in comparison.fdr_p_value)

    def test_unknown_reference_raises(self):
        with pytest.raises(InvalidInputError):
            compare_groups(_table(), _groups(), "ghost")

    def test_missing_member_raises(self):
        groups = dict(_groups())
        groups["control"].append("nonexistent")
        with pytest.raises(InvalidInputError):
            compare_groups(_table(), groups, "control")


class TestVolcano:
    def test_points_carry_coordinates(self):
        result = compare_groups(_table(), _groups(), "control")
        data = volcano_plot_data(result)
        assert data["points"]
        point = data["points"][0]
        assert {"feature_id", "comparison", "x", "y", "significant"} <= set(point)
        assert all(point["y"] >= 0 for point in data["points"])


class TestTimeCourse:
    def _time_table(self) -> tuple[FeatureTable, dict[str, float]]:
        samples = [SampleMetadata(sample_id=f"S{i}", injection_order=i) for i in range(5)]
        feature = Feature(
            feature_id="FT1", mz=100.0, rt=10.0,
            intensities={f"S{i}": 100.0 + 50.0 * i for i in range(5)},
        )
        return (
            FeatureTable(features=[feature], samples=samples),
            {f"S{i}": float(i) for i in range(5)},
        )

    def test_linear_trend_recovered(self):
        table, times = self._time_table()
        result = time_course_analysis(table, times)
        assert result.slope[0] == pytest.approx(50.0)
        assert result.r_squared[0] == pytest.approx(1.0)
        assert result.p_value[0] < 0.01
        assert result.n_timepoints == 5

    def test_missing_times_raise(self):
        table, _ = self._time_table()
        with pytest.raises(InvalidInputError):
            time_course_analysis(table, {"S0": 0.0, "S1": 1.0})

    def test_single_timepoint_raises(self):
        table, _ = self._time_table()
        with pytest.raises(InvalidInputError):
            time_course_analysis(table, {f"S{i}": 1.0 for i in range(5)})


class TestDoseResponse:
    def _dose_table(self, ec50: float = 5.0) -> tuple[FeatureTable, dict[str, float]]:
        doses = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        samples = [
            SampleMetadata(sample_id=f"D{i}", injection_order=i)
            for i in range(len(doses))
        ]
        values = {
            f"D{i}": 100.0 / (1.0 + (ec50 / dose))  # simple 4PL, Hill=1
            for i, dose in enumerate(doses)
        }
        feature = Feature(feature_id="FT1", mz=100.0, rt=10.0, intensities=values)
        return (
            FeatureTable(features=[feature], samples=samples),
            {f"D{i}": dose for i, dose in enumerate(doses)},
        )

    def test_ec50_recovered(self):
        table, doses = self._dose_table(ec50=5.0)
        result = dose_response_analysis(table, doses)
        assert result.fit_failed == [False]
        assert result.ec50[0] == pytest.approx(5.0, rel=0.15)
        assert result.r_squared[0] > 0.98
        assert result.ec50_ci95[0] is not None

    def test_too_few_doses_raises(self):
        table, doses = self._dose_table()
        sliced = {k: v for k, v in list(doses.items())[:2]}
        table = table.model_copy(
            update={"samples": [s for s in table.samples if s.sample_id in sliced]}
        )
        with pytest.raises(InvalidInputError):
            dose_response_analysis(table, sliced)
