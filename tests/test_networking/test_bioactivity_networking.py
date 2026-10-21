"""Bioactivity networking tests (Nothias et al. 2018): Pearson correlation
correctness on synthetic data, flagging, and prioritization."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from phyto_shared.exceptions import InvalidInputError
from networking.bioactivity_networking import (
    BIOACTIVITY_MAX_P,
    BIOACTIVITY_MIN_ABS_R,
    bioactivity_networking,
    prioritize_bioactive_nodes,
)
from networking.gnps_client import MNResults

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gnps"
GRAPHML = (FIXTURES / "classical_mn.graphml").read_text()

ACTIVITY = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
FRACTIONS = ["F1", "F2", "F3", "F4", "F5", "F6"]


def _abundances(**overrides) -> pd.DataFrame:
    data = {
        "scan=1": ACTIVITY,             # perfect positive correlation
        "scan=2": np.full(6, 4.2),      # constant -> r defined as 0
        "scan=3": -ACTIVITY,            # perfect anticorrelation
        "scan=4": np.array([1, 5, 2, 6, 3, 4], dtype=float),  # weak
        "scan=5": ACTIVITY * 0.5 + 1.0, # perfect again (scaled)
    }
    data.update(overrides)
    frame = pd.DataFrame(data, index=FRACTIONS)
    return frame


def _bioactivities() -> pd.Series:
    return pd.Series(ACTIVITY, index=FRACTIONS, name="inhibition_pct")


def _results() -> object:
    return bioactivity_networking(MNResults(graphml=GRAPHML), _abundances(), _bioactivities())


class TestCorrelations:
    def test_perfect_tracking_gets_r_one(self):
        network = _results()
        assert network.bioactivity_scores["scan=1"] == pytest.approx(1.0)
        assert network.p_values["scan=1"] < BIOACTIVITY_MAX_P

    def test_constant_series_gets_zero(self):
        network = _results()
        assert network.bioactivity_scores["scan=2"] == 0.0
        assert network.p_values["scan=2"] == 1.0

    def test_anticorrelation_gets_minus_one(self):
        network = _results()
        assert network.bioactivity_scores["scan=3"] == pytest.approx(-1.0)
        assert network.p_values["scan=3"] < BIOACTIVITY_MAX_P

    def test_scaled_profile_still_perfect(self):
        network = _results()
        assert network.bioactivity_scores["scan=5"] == pytest.approx(1.0)

    def test_weak_correlation_not_flagged(self):
        network = _results()
        r = network.bioactivity_scores["scan=4"]
        assert abs(r) < 0.5  # weak by construction
        assert "scan=4" not in network.bioactivity_metadata["flagged_nodes"]


class TestPrioritization:
    def test_filters_and_sorts_by_r_descending(self):
        network = _results()
        prioritized = prioritize_bioactive_nodes(network)
        assert prioritized[0] == "scan=1"
        assert prioritized[-1] == "scan=3"  # -1 sorts last but is flagged
        assert {"scan=1", "scan=3", "scan=5"} <= set(prioritized)
        assert "scan=2" not in prioritized

    def test_impossible_threshold_selects_nothing(self):
        network = _results()
        # |r| cannot exceed 1, so no node can pass min_r=1.5
        assert prioritize_bioactive_nodes(network, min_r=1.5, max_p=0.001) == []

    def test_default_thresholds_match_paper(self):
        assert BIOACTIVITY_MIN_ABS_R == 0.5
        assert BIOACTIVITY_MAX_P == 0.05


class TestValidation:
    def test_too_few_fractions_raises(self):
        small = _abundances().iloc[:2]
        with pytest.raises(InvalidInputError):
            bioactivity_networking(MNResults(graphml=GRAPHML), small, _bioactivities().iloc[:2])

    def test_nan_bioactivity_raises(self):
        broken = _bioactivities()
        broken.iloc[0] = float("nan")
        with pytest.raises(InvalidInputError):
            bioactivity_networking(MNResults(graphml=GRAPHML), _abundances(), broken)

    def test_missing_feature_column_recorded(self):
        frame = _abundances().drop(columns=["scan=4"])
        network = bioactivity_networking(
            MNResults(graphml=GRAPHML), frame, _bioactivities()
        )
        assert "scan=4" in network.bioactivity_metadata["features_without_abundance"]
        assert "scan=4" not in network.bioactivity_scores
