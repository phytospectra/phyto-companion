"""Retention-time post-ranking reranker (ADR-0063, ADR-0064).

Additive by contract: NEVER touches ``candidate_ranker``'s scoring
logic.  For each candidate it predicts RT under the user's LC method
(adjusted by the home library's calibration offset when one exists),
compares against the observed RT, and adjusts:

    |predicted - observed| > rt_tolerance:            -0.25
    |predicted - observed| < rt_tolerance / 2:        +0.15
    else:                                              0.00

SOFT FILTER BY DESIGN (ADR-0063): RT never excludes a candidate.  Malm
& Kruve (2025) found RT prediction underperforms projection in some
regimes; a hard RT gate would compound that weakness into false
negatives.  Only scores move, and every movement is attached as
evidence with the prediction that caused it.

When the user co-injected an anchor series (ADR-0064), the reranker
compares retention INDICES instead of RTs -- RI is method-independent
and portable across columns (Aalizadeh 2022) -- and ignores the RT
offset (it is absorbed by the RI axis).  With no anchors it falls back
to method-specific RT.
"""

from __future__ import annotations

import logging

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    RetentionPrediction,
    rt_to_ri,
)
from phyto_shared.models import CandidateRank, EvidenceGraph, EvidenceItem

__all__ = [
    "RT_FAR_PENALTY",
    "RT_CLOSE_BONUS",
    "rerank_with_rt",
    "rerank_with_ri",
]

logger = logging.getLogger(__name__)

RT_FAR_PENALTY = 0.25
RT_CLOSE_BONUS = 0.15

_PREDICT_RT = None  # injected at import time by the service wiring


def _predict(smiles: str, method: LCMethod) -> RetentionPrediction:
    """Lazy import: the elucidation service does not depend on the
    retention service's app package (services import only shared)."""
    try:
        from retention.main import predict_rt
    except ImportError:
        try:
            from services.retention.app.main import predict_rt
        except ImportError as exc:
            raise ImportError(
                "retention service not staged in this plane; the RT "
                "reranker cannot run without it"
            ) from exc
    return predict_rt(smiles, method)


def _evidence(
    predicted: float, observed: float, delta: float, ri_axis: bool
) -> EvidenceItem:
    unit = "RI" if ri_axis else "min"
    relation = (
        "rt_prediction_close" if delta > 0
        else ("rt_prediction_far" if delta < 0 else "rt_prediction_neutral")
    )
    return EvidenceItem(
        source_spectrum="retention_model",
        spectral_feature=(
            f"predicted {predicted:.2f} {unit} vs observed {observed:.2f} {unit}"
        ),
        relation=relation,
        strength=round(min(abs(delta), 1.0), 4),
    )


def _apply(score: float, delta: float, convention: str) -> float:
    return score - delta if convention == "lower_is_better" else score + delta


def _rerank_one(
    rank: CandidateRank,
    observed: float,
    predicted: float,
    delta: float,
    ri_axis: bool,
    chain_layer: str,
    prediction: RetentionPrediction | None,
) -> CandidateRank:
    new_score = round(_apply(rank.score, delta, rank.ranking_convention), 6)
    item = _evidence(predicted, observed, delta, ri_axis)
    graph = rank.evidence_graph
    if graph is None:
        graph = EvidenceGraph(
            node_id=rank.candidate.id,
            claim=f"identity: {rank.candidate.name}",
            evidence=[],
            confidence=0.0,
        )
    graph = graph.model_copy(update={"evidence": [*graph.evidence, item]})
    update: dict = {
        "score": new_score,
        "evidence_graph": graph,
        "classification_chain": [*rank.classification_chain, chain_layer],
        "evidence_chain": [
            *rank.evidence_chain,
            EvidenceItem(
                source_spectrum="retention_model",
                spectral_feature=item.spectral_feature,
                relation=chain_layer,
                strength=round(min(abs(delta), 1.0), 4),
            ),
        ],
    }
    if ri_axis:
        update["ri_used"] = round(observed, 1)
        update["rt_bonus"] = round(delta, 4)
    else:
        update["rt_bonus"] = round(delta, 4)
        if prediction is not None:
            update["rt_prediction"] = prediction.predicted_rt_min
    return rank.model_copy(update=update)


def rerank_with_rt(
    ranked: list[CandidateRank],
    observed_rt_min: float,
    method: LCMethod,
    rt_tolerance: float = 1.5,
    rt_offset: float = 0.0,
    predict=None,
) -> list[CandidateRank]:
    """Rerank against the observed RT under the user's method.

    ``rt_offset`` is the home-library calibration delta (ADR-0061):
    predicted RTs are shifted by it before comparison, and each rank
    records ``rt_offset_applied``.  A method the model was not trained
    for still gets the baseline model's wide interval -- the caller can
    inspect ``rt_prediction`` and the model id; the tolerance logic is
    unchanged (soft filter either way).
    """
    if method is None:
        raise ValueError("rerank_with_rt requires an LCMethod (ADR-0063)")
    predict = predict or _predict
    out: list[CandidateRank] = []
    for rank in ranked:
        prediction = predict(rank.candidate.smiles, method)
        predicted = max(0.0, prediction.predicted_rt_min + rt_offset)
        delta = abs(predicted - observed_rt_min)
        if delta > rt_tolerance:
            adjustment = -RT_FAR_PENALTY
        elif delta < rt_tolerance / 2:
            adjustment = RT_CLOSE_BONUS
        else:
            adjustment = 0.0
        out.append(
            _rerank_one(
                rank,
                observed_rt_min,
                predicted,
                adjustment,
                ri_axis=False,
                chain_layer="rt",
                prediction=prediction,
            ).model_copy(
                update={"rt_offset_applied": round(rt_offset, 4)}
                if rt_offset
                else {}
            )
        )
    return out


def rerank_with_ri(
    ranked: list[CandidateRank],
    observed_rt_min: float,
    anchors: list[AnchorStandard],
    method: LCMethod | None = None,
    ri_tolerance: float = 150.0,
    predict=None,
) -> list[CandidateRank]:
    """Rerank on the retention-index axis (ADR-0064).

    The observed peak's RI comes from the anchor map; each candidate's
    predicted RI comes from its predicted RT mapped through the same
    anchors.  Strictly more reliable than raw RT when anchors exist
    (method-independent); callers use this INSTEAD of
    :func:`rerank_with_rt` when the user co-injected anchors.
    """
    if len(anchors) < 2:
        raise ValueError(
            "rerank_with_ri requires at least two anchors (ADR-0064)"
        )
    predict = predict or _predict
    out: list[CandidateRank] = []
    for rank in ranked:
        prediction = predict(rank.candidate.smiles, method) if method else None
        if prediction is None:
            # A candidate whose RT cannot be predicted has no RI to
            # compare; DROPPING it would erase the candidate from the
            # report entirely.  It keeps its rank untouched -- the RI
            # layer simply did not inform it (the same honesty rule the
            # UV reranker applies to unparseable SMILES).
            out.append(rank)
            continue
        predicted_ri = rt_to_ri(prediction.predicted_rt_min, anchors)
        observed_ri = rt_to_ri(observed_rt_min, anchors)
        delta_ri = abs(predicted_ri - observed_ri)
        if delta_ri > ri_tolerance:
            adjustment = -RT_FAR_PENALTY
        elif delta_ri < ri_tolerance / 2:
            adjustment = RT_CLOSE_BONUS
        else:
            adjustment = 0.0
        out.append(
            _rerank_one(
                rank,
                observed_ri,
                predicted_ri,
                adjustment,
                ri_axis=True,
                chain_layer="rt",
                prediction=prediction,
            )
        )
    return out