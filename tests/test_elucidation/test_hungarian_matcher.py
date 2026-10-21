"""Hungarian matcher tests: all four modes, multiplicity penalty,
ranking_convention invariant."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from elucidation.hungarian_matcher import (
    MatchResult,
    match_1d,
    match_carbon_1d,
    match_carbon_full,
    match_hsqc,
    match_hsqc_multinuclear,
)


class TestMatchResultModel:
    def test_ranking_convention_required(self):
        with pytest.raises(ValidationError):
            MatchResult(score=1.0, coverage=1.0, n_observed=1, n_predicted=1)

    def test_all_results_carry_lower_is_better(self):
        for result in (
            match_1d([100.0], [100.0], tol=0.5),
            match_carbon_1d([100.0], [100.0]),
            match_carbon_full([100.0], [102.0]),
            match_hsqc([(1.0, 50.0)], [(1.0, 50.0)]),
            match_hsqc_multinuclear([(1.0, 50.0)], [(1.0, 50.0)], "31P"),
        ):
            assert result.ranking_convention == "lower_is_better"


class TestOneDimensional:
    def test_identical_lists_score_zero(self):
        result = match_carbon_1d([100.0, 120.0, 140.0], [100.0, 120.0, 140.0])
        assert result.score == 0.0
        assert result.coverage == 1.0

    def test_disjoint_equal_length_scores_cap(self):
        result = match_carbon_1d([10.0, 20.0], [110.0, 220.0])
        assert result.score == pytest.approx(4.0)  # cap
        assert result.coverage == 0.0

    def test_edited_tight_vs_full_loose_tolerance(self):
        predicted = [100.0]
        observed = [102.5]
        assert match_carbon_1d(predicted, observed).score == pytest.approx(4.0)
        assert match_carbon_full(predicted, observed).score == pytest.approx(2.5)

    def test_partial_match_coverage_and_unmatched(self):
        result = match_1d([100.0, 101.0], [100.0, 101.0, 200.0], tol=0.5)
        assert result.coverage == pytest.approx(2.0 / 3.0)
        assert result.unmatched_observed_indices == [2]
        assert result.unmatched_predicted_indices == []

    def test_empty_lists_raise(self):
        with pytest.raises(ValueError):
            match_1d([], [100.0], tol=0.5)


class TestHSQC:
    def test_identical_pairs_score_zero(self):
        pairs = [(1.0, 50.0), (2.0, 60.0)]
        result = match_hsqc(pairs, pairs)
        assert result.score == 0.0
        assert result.coverage == 1.0

    def test_disjoint_pairs_score_cap(self):
        result = match_hsqc([(1.0, 50.0)], [(8.0, 190.0)])
        assert result.score == pytest.approx(4.0)

    def test_partial_deviation_scaled_by_cap(self):
        # half-tolerance deviation on each axis -> cost cap/2 -> score 2.0
        result = match_hsqc([(1.0, 50.0)], [(1.025, 50.25)])
        assert result.score == pytest.approx(2.0)


class TestMultiplicity:
    def test_ch2_cannot_match_ch(self):
        result = match_hsqc(
            [(3.0, 60.0, "CH2")],
            [(3.0, 60.0, "CH")],
        )
        # penalty (cap*10) added to the cap cell -> score far above cap
        assert result.score > 4.0

    def test_ch_and_ch3_are_compatible(self):
        result = match_hsqc(
            [(3.0, 60.0, "CH3")],
            [(3.0, 60.0, "CH")],
        )
        assert result.score == pytest.approx(0.0)

    def test_penalty_lets_compatible_partner_win(self):
        predicted = [(3.0, 60.0, "CH2"), (3.05, 60.4, "CH")]
        observed = [(3.05, 60.4, "CH")]
        result = match_hsqc(predicted, observed)
        assert result.assignments[0].predicted_index == 1

    def test_numeric_multiplicity_accepted(self):
        result = match_hsqc([(3.0, 60.0, 2)], [(3.0, 60.0, 1)])
        assert result.score > 4.0

    def test_none_mode_ignores_multiplicity(self):
        result = match_hsqc(
            [(3.0, 60.0, "CH2")],
            [(3.0, 60.0, "CH")],
            multiplicity_mode="none",
        )
        assert result.score == pytest.approx(0.0)


class TestMultinuclear:
    def test_phosphorus_tolerance_wider_than_carbon(self):
        predicted = [(7.5, 20.0)]
        observed = [(7.5, 21.0)]
        as_phosphorus = match_hsqc_multinuclear(
            predicted, observed, "31P", tol_h=0.05, tol_x=2.0
        )
        as_carbon = match_hsqc_multinuclear(
            predicted, observed, "13C", tol_h=0.05, tol_x=0.5
        )
        # within the wide 31P window: matched (cost = deviation * cap)
        assert as_phosphorus.coverage == 1.0
        assert as_phosphorus.score < 4.0
        # outside the 13C window: capped
        assert as_carbon.score == pytest.approx(4.0)

    def test_nitrogen_mode(self):
        result = match_hsqc_multinuclear(
            [(7.0, 250.0)], [(7.02, 250.3)], "15N", tol_h=0.05, tol_x=0.5
        )
        assert result.coverage == 1.0
