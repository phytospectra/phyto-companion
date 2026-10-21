"""Synergy: shared targets, complementary pathways, combination index."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from pharmacology.synergy_analysis import combination_index, predict_synergy
from pharmacology.target_prediction import TargetPrediction

Q = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
K = "VNEYFRNUTPLQCC-UHFFFAOYSA-N"
C = "RYYVLZVUVIJVGH-UHFFFAOYSA-N"


def _prediction(compound: str, target: str) -> TargetPrediction:
    return TargetPrediction(
        compound_ik=compound,
        target_id=target,
        target_name=target,
        method="similarity",
        confidence=0.6,
        confidence_basis="test",
        citation="PMID:11914285",
    )


class TestPredictSynergy:
    def test_shared_targets_flagged(self):
        result = predict_synergy(
            [Q, K],
            {
                Q: [_prediction(Q, "P35354"), _prediction(Q, "P23219")],
                K: [_prediction(K, "P35354")],
            },
        )
        assert result.hypotheses
        hypothesis = result.hypotheses[0]
        assert hypothesis.shared_targets == ["P35354"]
        assert hypothesis.is_hypothesis is True
        assert "additive" in hypothesis.rationale or "synergistic" in hypothesis.rationale

    def test_complementary_pathway_targets(self):
        result = predict_synergy(
            [Q, K],
            {
                Q: [_prediction(Q, "P35354")],  # map00590
                K: [_prediction(K, "P23219")],  # map00590 too
            },
        )
        hypothesis = result.hypotheses[0]
        assert hypothesis.complementary_pathway_targets
        assert "complementary" in hypothesis.rationale

    def test_unrelated_compounds_no_hypothesis(self):
        result = predict_synergy(
            [Q, C],
            {
                Q: [_prediction(Q, "P35354")],
                C: [_prediction(C, "P00533")],
            },
        )
        assert result.hypotheses == []

    def test_needs_two_compounds(self):
        with pytest.raises(InvalidInputError):
            predict_synergy([Q], {Q: []})

    def test_compounds_without_targets_reported(self):
        result = predict_synergy([Q, K], {Q: [_prediction(Q, "P35354")], K: []})
        assert result.single_target_compounds == [K]


class TestCombinationIndex:
    def test_synergy_additivity_antagonism(self):
        # fa=0.5, combination doses 2 + 3; single-agent doses 10 + 10
        effects = [0.5, 2.0, 3.0]
        single = [[0.5, 10.0], [0.5, 10.0]]
        ci = combination_index(effects, single)
        assert ci == pytest.approx(0.5)  # CI < 1 -> synergy

    def test_additive_ci_is_one(self):
        ci = combination_index([0.5, 5.0, 5.0], [[0.5, 10.0], [0.5, 10.0]])
        assert ci == pytest.approx(1.0)

    def test_antagonism_above_one(self):
        ci = combination_index([0.5, 8.0, 8.0], [[0.5, 10.0], [0.5, 10.0]])
        assert ci > 1.0

    @pytest.mark.parametrize(
        ("effects", "single"),
        [
            (None, [[0.5, 10.0]]),
            ([], [[0.5, 10.0]]),
            ([0.5, 1.0, 1.0], None),
            ([0.5, 1.0, 1.0], [[0.5, 10.0]]),
            ([0.5, 1.0, 1.0], [[0.7, 10.0], [0.7, 10.0]]),  # fa mismatch
        ],
        ids=["none_effects", "empty", "none_single", "one_single", "fa_mismatch"],
    )
    def test_insufficient_data_returns_none(self, effects, single):
        assert combination_index(effects, single) is None
