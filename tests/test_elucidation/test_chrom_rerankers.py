"""UV and RT/RI reranker tests (ADR-0062..ADR-0064): additive deltas,
evidence attachment, convention handling, and honest skips."""

from __future__ import annotations

import pytest

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    RetentionPrediction,
)
from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.models import (
    Candidate,
    CandidateRank,
    EvidenceGraph,
    MolecularFormula,
)
from phyto_shared.uv_chromophore import UVClass

from elucidation.rt_reranker import (
    RT_CLOSE_BONUS,
    RT_FAR_PENALTY,
    rerank_with_ri,
    rerank_with_rt,
)
from elucidation.uv_reranker import (
    UV_INCOMPATIBLE_PENALTY,
    UV_MATCH_BONUS,
    rerank_with_uv,
)

QUERCETIN = "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)O)O"
PANDURATIN = "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1"
CAFFEINE = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"


def _rank(cid: str, name: str, smiles: str, score: float) -> CandidateRank:
    return CandidateRank(
        candidate=Candidate(
            id=cid, name=name, smiles=smiles,
            formula=MolecularFormula(counts={"C": 1}), exact_mass=100.0,
            provenance="experimental", source="test",
        ),
        score=score,
        ranking_convention="lower_is_better",
        verdict=ConfidenceVerdict.TENTATIVE,
        evidence_graph=EvidenceGraph(
            node_id=cid, claim=f"identity: {name}", evidence=[], confidence=0.5,
        ),
    )


def _method() -> LCMethod:
    return LCMethod(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    )


FLAVONOL_OBSERVED = UVClass(
    primary_class="flavonol", confidence=0.9, band_i_nm=370.0, band_ii_nm=270.0
)
LOW_CONFIDENCE_FLAVONOL = UVClass(
    primary_class="flavonol", confidence=0.3, band_i_nm=370.0, band_ii_nm=270.0
)


# --- UV reranker -------------------------------------------------------------


def test_uv_match_bonuses_matching_candidate() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    out = rerank_with_uv(ranks, FLAVONOL_OBSERVED, predict_classes=lambda s: ["flavonol"])
    assert out[0].score == pytest.approx(1.0 - UV_MATCH_BONUS)
    assert out[0].uv_bonus == UV_MATCH_BONUS
    assert "uv" in out[0].classification_chain
    assert out[0].uv_evidence == FLAVONOL_OBSERVED


def test_uv_incompatible_candidate_is_penalised() -> None:
    ranks = [_rank("c", "caffeine", CAFFEINE, 1.0)]
    out = rerank_with_uv(
        ranks, FLAVONOL_OBSERVED, predict_classes=lambda s: ["alkaloid_indole"]
    )
    assert out[0].score == pytest.approx(1.0 + UV_INCOMPATIBLE_PENALTY)
    assert out[0].uv_bonus == -UV_INCOMPATIBLE_PENALTY


def test_unknown_class_candidate_is_left_at_zero_delta() -> None:
    ranks = [_rank("x", "unknown", "C", 1.0)]
    out = rerank_with_uv(ranks, FLAVONOL_OBSERVED, predict_classes=lambda s: [])
    assert out[0].score == 1.0
    assert out[0].uv_bonus == 0.0
    # The layer still records that it RAN on this candidate.
    assert out[0].uv_evidence == FLAVONOL_OBSERVED


def test_low_confidence_observed_class_is_a_noop() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    out = rerank_with_uv(ranks, LOW_CONFIDENCE_FLAVONOL, predict_classes=lambda s: ["flavonol"])
    assert out[0].score == 1.0
    assert out[0].uv_bonus == 0.0


def test_none_observed_class_returns_input_unchanged() -> None:
    ranks = [_rank("q", "quercetin", QUERCETIN, 1.0)]
    assert rerank_with_uv(ranks, None) == ranks


def test_higher_is_better_convention_gets_bonus_added() -> None:
    rank = _rank("q", "quercetin", QUERCETIN, 0.5)
    rank = rank.model_copy(update={"ranking_convention": "higher_is_better"})
    out = rerank_with_uv([rank], FLAVONOL_OBSERVED, predict_classes=lambda s: ["flavonol"])
    assert out[0].score == pytest.approx(0.5 + UV_MATCH_BONUS)


# --- RT reranker -------------------------------------------------------------


def _prediction(rt: float) -> RetentionPrediction:
    return RetentionPrediction(
        predicted_rt_min=rt, prediction_interval=(rt - 0.5, rt + 0.5),
        model_id="test", model_version="1",
    )


def test_rt_close_gets_bonus_and_far_gets_penalty() -> None:
    ranks = [
        _rank("near", "near", "C", 1.0),
        _rank("far", "far", "CC", 1.0),
    ]

    def predict(smiles: str, method: LCMethod) -> RetentionPrediction:
        return _prediction(10.0 if smiles == "C" else 20.0)

    out = rerank_with_rt(ranks, observed_rt_min=10.2, method=_method(), predict=predict)
    by_id = {r.candidate.id: r for r in out}
    assert by_id["near"].score == pytest.approx(1.0 - RT_CLOSE_BONUS)
    assert by_id["far"].score == pytest.approx(1.0 + RT_FAR_PENALTY)
    assert by_id["near"].rt_prediction == 10.0


def test_rt_reranker_records_offset() -> None:
    ranks = [_rank("c", "c", "C", 1.0)]
    predict = lambda s, m: _prediction(10.0)  # noqa: E731
    out = rerank_with_rt(
        ranks, observed_rt_min=11.0, method=_method(), rt_offset=0.0, predict=predict
    )
    assert out[0].rt_offset_applied is None
    # |10 - 11| = 1.0, inside the neutral band [tolerance/2, tolerance].
    assert out[0].rt_bonus == 0.0
    out = rerank_with_rt(
        ranks, observed_rt_min=11.0, method=_method(), rt_offset=0.5, predict=predict
    )
    assert out[0].rt_offset_applied == 0.5
    # predicted 10 + offset 0.5 = 10.5; |10.5 - 11| = 0.5 < tolerance/2.
    assert out[0].rt_bonus == RT_CLOSE_BONUS


def test_rt_reranker_offset_can_flip_the_band() -> None:
    ranks = [_rank("c", "c", "C", 1.0)]
    predict = lambda s, m: _prediction(10.0)  # noqa: E731
    # Without the offset: |10 - 12| = 2 > tolerance -> penalty.
    # With a +2 offset: predicted 12 == observed -> bonus.
    no_offset = rerank_with_rt(ranks, 12.0, _method(), predict=predict)
    with_offset = rerank_with_rt(ranks, 12.0, _method(), rt_offset=2.0, predict=predict)
    assert no_offset[0].rt_bonus == -RT_FAR_PENALTY
    assert with_offset[0].rt_bonus == RT_CLOSE_BONUS


def test_rt_reranker_requires_a_method() -> None:
    with pytest.raises(ValueError, match="LCMethod"):
        rerank_with_rt([_rank("c", "c", "C", 1.0)], 10.0, None)  # type: ignore[arg-type]


def test_ri_reranker_compares_on_the_index_axis() -> None:
    anchors = [
        AnchorStandard(name="C10", chain_length=10, observed_rt_min=5.0),
        AnchorStandard(name="C11", chain_length=11, observed_rt_min=11.0),
    ]
    ranks = [_rank("near", "near", "C", 1.0), _rank("far", "far", "CC", 1.0)]

    def predict(smiles: str, method: LCMethod) -> RetentionPrediction:
        # C10@5.0/C11@11.0 anchors: 8 min -> RI 1050; 10 min -> RI ~1083.
        return _prediction(8.0 if smiles == "C" else 10.0)

    out = rerank_with_ri(
        ranks, observed_rt_min=8.0, anchors=anchors, method=_method(), predict=predict
    )
    by_id = {r.candidate.id: r for r in out}
    assert by_id["near"].ri_used == pytest.approx(1050.0)
    assert by_id["near"].score == pytest.approx(1.0 - RT_CLOSE_BONUS)
    # far: predicted RI 1083, observed 1050 -> delta 33 RI, within half of
    # the 150 tolerance (75): CLOSE bonus too, not the far penalty.
    assert by_id["far"].rt_bonus == RT_CLOSE_BONUS
    assert "rt" in by_id["near"].classification_chain


def test_ri_reranker_requires_two_anchors() -> None:
    with pytest.raises(ValueError, match="two anchors"):
        rerank_with_ri(
            [_rank("c", "c", "C", 1.0)], 8.0,
            [AnchorStandard(name="C10", chain_length=10, observed_rt_min=5.0)],
            predict=lambda s, m: _prediction(8.0),
        )


def test_rt_evidence_is_attached_to_the_graph() -> None:
    ranks = [_rank("near", "near", "C", 1.0)]
    out = rerank_with_rt(
        ranks, observed_rt_min=10.2, method=_method(),
        predict=lambda s, m: _prediction(10.0),
    )
    relations = [e.relation for e in out[0].evidence_graph.evidence]
    assert "rt_prediction_close" in relations