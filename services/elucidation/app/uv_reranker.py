"""UV-chromophore post-ranking reranker (ADR-0062).

Additive by contract, like the taxonomy reranker (ADR-0052): this module
NEVER touches ``candidate_ranker``'s scoring logic.  It consumes the
ranked list, classifies the observed PDA spectrum once, predicts every
candidate's structural chromophore class from its SMILES, and adjusts
scores:

    observed class in candidate's predicted classes:  +0.20
    observed class incompatible with the candidate:   -0.30
    no observed class / cannot classify candidate:     0.00

"Incompatible" is directional: the observed class names the chromophore
the sample actually shows, so a candidate whose skeleton is a different
class (observed=flavonol, candidate=alkaloid_isoquinoline) contradicts
the observation.  A candidate whose class cannot be determined (RDKit
parse failure) is LEFT UNTOUCHED -- uncertainty is not evidence against.

Scores keep their ``ranking_convention`` (ADR-0008): a bonus SUBTRACTS
from a ``lower_is_better`` distance and ADDS to a ``higher_is_better``
similarity.  A low-confidence observed classification (below the
threshold, default 0.6) is a no-op: a shaky class must not move scores.
"""

from __future__ import annotations

import logging

from phyto_shared.models import CandidateRank, EvidenceGraph, EvidenceItem
from phyto_shared.uv_chromophore import UVClass, predict_class_from_smiles

__all__ = [
    "UV_MATCH_BONUS",
    "UV_INCOMPATIBLE_PENALTY",
    "rerank_with_uv",
]

logger = logging.getLogger(__name__)

UV_MATCH_BONUS = 0.20
UV_INCOMPATIBLE_PENALTY = 0.30

#: Classes whose presence in a candidate does NOT contradict the observed
#: class even when it does not equal it.  The ``phenolic_acid`` family
#: shares the hydroxycinnamate/benzoate chromophore with many "other"
#: structures; flavone/flavonol share the chromen-4-one core (their own
#: boundary ambiguity is documented in the classifier).  This is a
#: deliberate small whitelist: without it, every unlisted-but-adjacent
#: class pair would read as a contradiction.
_COMPATIBILITY_EQUIVALENCES: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"flavone", "flavonol"}),
        frozenset({"phenolic_acid", "unclassified"}),
    }
)


def _incompatible(observed: str, candidate_classes: list[str]) -> bool:
    """True when the candidate's structural classes contradict the
    observed chromophore class."""
    if not candidate_classes or "unclassified" in candidate_classes:
        return False
    if observed in candidate_classes:
        return False
    for pair in _COMPATIBILITY_EQUIVALENCES:
        if observed in pair and pair & set(candidate_classes):
            return False
    # Both are UV-active classes, just different ones: contradiction.
    return observed != "terpene_like" or "terpene_like" not in candidate_classes


def _apply(score: float, delta: float, convention: str) -> float:
    return score - delta if convention == "lower_is_better" else score + delta


def rerank_with_uv(
    ranked: list[CandidateRank],
    observed_uv: UVClass | None,
    uv_confidence_threshold: float = 0.6,
    predict_classes=None,
) -> list[CandidateRank]:
    """Rerank against the observed UV chromophore classification.

    Attaches ``uv_evidence`` (the observed classification), ``uv_bonus``
    (the applied delta, signed toward "better"), an EvidenceItem with
    ``relation="uv_chromophore_match"``, and appends "uv" to
    ``classification_chain`` on every touched candidate.  Candidates the
    layer could not inform (no classes, parse failure) keep their score
    and still get the observed classification recorded, so a reader can
    see the layer RAN and did not move them.

    ``observed_uv`` below the confidence threshold (default 0.6; basis:
    asserted, provisional -- ADR-0062) is a no-op that returns the input
    list unchanged: an unconfident class is not evidence.  The 0.6 gate
    separates unambiguous band assignments (one band pair lands squarely
    in one class window) from overlapped/edge cases (the flavone/flavonol
    and coumarin/phenolic-acid windows adjoin); recalibrate against
    labelled PDA spectra before treating it as validated.
    ``predict_classes`` is injectable for tests.
    """
    if observed_uv is None or observed_uv.confidence < uv_confidence_threshold:
        logger.info(
            "uv rerank skipped: observed classification absent or below "
            "confidence %.2f",
            uv_confidence_threshold,
        )
        return list(ranked)

    predict_classes = predict_classes or predict_class_from_smiles
    out: list[CandidateRank] = []
    for rank in ranked:
        classes = predict_classes(rank.candidate.smiles)
        observed = observed_uv.primary_class
        if observed == "terpene_like" and "terpene_like" in classes:
            delta = UV_MATCH_BONUS
        elif _incompatible(observed, classes):
            delta = -UV_INCOMPATIBLE_PENALTY
        elif observed in classes:
            delta = UV_MATCH_BONUS
        else:
            delta = 0.0

        new_score = round(_apply(rank.score, delta, rank.ranking_convention), 6)
        relation = (
            "uv_chromophore_match"
            if delta > 0
            else ("uv_chromophore_mismatch" if delta < 0 else "uv_chromophore_neutral")
        )
        feature = (
            f"observed {observed} (confidence {observed_uv.confidence:.2f}); "
            f"candidate classes: {', '.join(classes) or 'none determined'}"
        )
        item = EvidenceItem(
            source_spectrum="classifier:uv_chromophore",
            spectral_feature=feature,
            relation=relation,
            # EvidenceItem.strength is >= 0; a penalty's magnitude is its
            # strength, the relation name carries the sign.
            strength=round(min(abs(delta), 1.0), 4),
        )
        graph = rank.evidence_graph
        if graph is None:
            graph = EvidenceGraph(
                node_id=rank.candidate.id,
                claim=f"identity: {rank.candidate.name}",
                evidence=[],
                confidence=0.0,
            )
        graph = graph.model_copy(update={"evidence": [*graph.evidence, item]})
        out.append(
            rank.model_copy(
                update={
                    "score": new_score,
                    "uv_evidence": observed_uv,
                    "uv_bonus": round(delta, 4),
                    "evidence_graph": graph,
                    "classification_chain": [*rank.classification_chain, "uv"],
                    "evidence_chain": [
                        *rank.evidence_chain,
                        EvidenceItem(
                            source_spectrum="classifier:uv_chromophore",
                            spectral_feature=feature,
                            relation="uv",
                            strength=round(min(abs(delta), 1.0), 4),
                        ),
                    ],
                }
            )
        )
    return out