"""Chromatographic evidence layer chain for the identify workflow
(ADR-0061..ADR-0066).

Runs the chromatography layers ADDITIVELY over an already-ranked
candidate list, in the pipeline order: taxonomy prior -> RT/RI -> UV ->
peak purity -> pharmacopoeial markers.  Each layer that RUNS appends one
EvidenceItem per candidate to ``evidence_chain`` (named source and
strength) and its tag to ``classification_chain``; each layer that is
SKIPPED for missing metadata is reported in ``skipped_layers`` with the
reason -- never silently.

The candidate_ranker's scoring is untouched: this module consumes ranks.
"""

from __future__ import annotations

import logging
from typing import Any

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    ReferenceStandard,
    rt_to_ri,
)
from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.models import CandidateRank, EvidenceItem
from phyto_shared.uv_chromophore import UVClass, classify_uv

from elucidation.rt_reranker import rerank_with_ri, rerank_with_rt
from elucidation.uv_reranker import rerank_with_uv

__all__ = [
    "LayerChainResult",
    "apply_chromatography_layers",
]

logger = logging.getLogger(__name__)


class LayerChainResult:
    """Ranks plus the audit trail of which layers ran or were skipped."""

    def __init__(self, ranks: list[CandidateRank], skipped: list[dict[str, str]]) -> None:
        self.ranks = ranks
        self.skipped_layers = skipped


def _skipped(tag: str, reason: str) -> dict[str, str]:
    return {"layer": tag, "reason": f"layer skipped: {reason}"}


def _level1_evidence_chain(
    ranks: list[CandidateRank],
    standards: list[ReferenceStandard],
    method: LCMethod | None,
    observed_rt: float | None,
    predict_rt_fn=None,
) -> tuple[list[CandidateRank], dict[str, str] | None]:
    """Home-library Level-1 matching + RT offset calibration (ADR-0061)."""
    from elucidation.taxonomy_reranker import _inchikey_of

    if not standards:
        return ranks, _skipped("level1", "no reference standards")
    # InChIKey join: the standard matched BY PROPERTIES names a structure;
    # the rank it applies to is the candidate with the same structure.
    standard_iks = {s.name: _inchikey_of(s.smiles) for s in standards}
    updates: dict[str, CandidateRank] = {}
    offset: float | None = None
    for s in standards:
        ik = standard_iks[s.name]
        if ik is None:
            continue
        if method is not None and predict_rt_fn is not None:
            try:
                prediction = predict_rt_fn(s.smiles, method)
                offset = round(s.rt_min - prediction.predicted_rt_min, 4)
            except Exception:
                logger.info("rt offset calibration unavailable for %r", s.name)
    out: list[CandidateRank] = []
    for rank in ranks:
        ik = _inchikey_of(rank.candidate.smiles)
        matched = next(
            (name for name, s_ik in standard_iks.items() if s_ik == ik), None
        )
        if matched:
            update = {"level_1_match": matched}
            if offset is not None:
                update["rt_offset_applied"] = offset
            rank = rank.model_copy(update=update)
            rank.evidence_chain = [
                *rank.evidence_chain,
                EvidenceItem(
                    source_spectrum="home_library:calibration",
                    spectral_feature=f"Level-1 match against standard {matched!r}",
                    relation="level_1_reference",
                    strength=1.0,
                ),
            ]
            rank.classification_chain = [*rank.classification_chain, "level1"]
            updates[rank.candidate.id] = rank
        else:
            if offset is not None:
                rank = rank.model_copy(
                    update={"rt_offset_applied": round(offset, 4)}
                )
        out.append(rank)
    return out, None


def apply_chromatography_layers(
    ranks: list[CandidateRank],
    *,
    species: str | None,
    observed_rt_min: float | None,
    observed_uv_spectrum: list[tuple[float, float]] | None,
    peak_purity_score: float | None,
    lc_method: LCMethod | None,
    reference_standards: list[ReferenceStandard] | None,
    anchor_series: list[AnchorStandard] | None,
    predict_rt_fn=None,
    rt_tolerance: float = 1.5,
) -> LayerChainResult:
    """Run every chromatography layer whose metadata exists; report skips.

    Order and rules (ADR-0061..ADR-0066):
    * Level-1 home library match (needs ``reference_standards``).
    * RT reranking (needs ``lc_method`` and ``observed_rt_min``); if
      ``anchor_series`` is present, RI reranking replaces RT reranking.
    * UV reranking (needs ``observed_uv_spectrum``).
    * Peak purity (needs ``peak_purity_score``): < 0.5 forces AMBIGUOUS
      with a caveat, regardless of score.
    * Pharmacopoeial markers (needs ``species`` and ``observed_rt_min``).
    """
    skipped: list[dict[str, str]] = []

    # --- Level 1 -----------------------------------------------------------
    if reference_standards:
        ranks, skip = _level1_evidence_chain(
            ranks, reference_standards, lc_method, observed_rt_min, predict_rt_fn
        )
        if skip:
            skipped.append(skip)
    else:
        skipped.append(_skipped("level1", "no metadata (reference standards)"))

    # --- RT / RI -----------------------------------------------------------
    if lc_method is not None and observed_rt_min is not None:
        if anchor_series and len(anchor_series) >= 2:
            ranks = rerank_with_ri(
                ranks,
                observed_rt_min,
                anchor_series,
                method=lc_method,
                predict=predict_rt_fn,
            )
        else:
            offset = 0.0
            applied = {r.rt_offset_applied for r in ranks if r.rt_offset_applied}
            if applied:
                offset = applied.pop()
            ranks = rerank_with_rt(
                ranks,
                observed_rt_min,
                lc_method,
                rt_tolerance=rt_tolerance,
                rt_offset=offset,
                predict=predict_rt_fn,
            )
        # the RI path stamps the same tag; RT stamping happened inside the
        # rerankers ("rt" chain tag)
    else:
        skipped.append(_skipped("rt", "no metadata (LC method or observed RT)"))

    # --- UV ----------------------------------------------------------------
    if observed_uv_spectrum:
        uv_class = classify_uv(observed_uv_spectrum)
        ranks = rerank_with_uv(ranks, uv_class)
    else:
        skipped.append(_skipped("uv", "no metadata (observed UV spectrum)"))

    # --- Peak purity ---------------------------------------------------------
    if peak_purity_score is not None:
        ranks = [
            r.model_copy(
                update={
                    "purity_score": round(peak_purity_score, 4),
                    "classification_chain": [*r.classification_chain, "purity"],
                }
            )
            if peak_purity_score < 0.5
            else r.model_copy(
                update={
                    "purity_score": round(peak_purity_score, 4),
                    "classification_chain": [*r.classification_chain, "purity"],
                }
            )
            for r in ranks
        ]
        if peak_purity_score < 0.5:
            from phyto_shared.peak_purity import PURITY_CAVEAT

            forced: list[CandidateRank] = []
            for rank in ranks:
                graph = rank.evidence_graph
                if graph is not None:
                    gaps = list(graph.gaps)
                    if PURITY_CAVEAT not in gaps:
                        gaps.append(PURITY_CAVEAT)
                    graph = graph.model_copy(update={"gaps": gaps})
                forced.append(
                    rank.model_copy(
                        update={
                            "verdict": ConfidenceVerdict.AMBIGUOUS,
                            "evidence_graph": graph,
                            "evidence_chain": [
                                *rank.evidence_chain,
                                EvidenceItem(
                                    source_spectrum="peak_purity:classifier",
                                    spectral_feature=(
                                        f"purity {peak_purity_score:.2f} < 0.5"
                                    ),
                                    relation="peak_purity_override",
                                    strength=1.0,
                                ),
                            ],
                        }
                    )
                )
            ranks = forced
    else:
        skipped.append(_skipped("purity", "no metadata (peak purity inputs)"))

    # --- Pharmacopoeial markers ---------------------------------------------
    if species and observed_rt_min is not None:
        try:
            from taxon_prior.markers import evaluate_markers
        except ImportError:
            try:
                from services.taxon_prior.app.markers import evaluate_markers
            except ImportError:
                evaluate_markers = None  # type: ignore[assignment]
        if evaluate_markers is None:
            skipped.append(
                _skipped("pharmacopoeial_markers", "marker service not staged")
            )
        else:
            maxima = _observed_maxima(observed_uv_spectrum) if observed_uv_spectrum else None
            hits = evaluate_markers(species, observed_rt_min, maxima)
            if not hits:
                skipped.append(
                    _skipped(
                        "pharmacopoeial_markers",
                        f"no curated markers for {species!r}",
                    )
                )
            else:
                supporting = [
                    h for h in hits if h.rt_in_range or h.uv_in_range
                ]
                ranks = [
                    _attach_marker_evidence(rank, hits, supporting)
                    for rank in ranks
                ]
    else:
        skipped.append(
            _skipped("pharmacopoeial_markers", "no metadata (species or RT)")
        )

    return LayerChainResult(ranks, skipped)


def _observed_maxima(spectrum: list[tuple[float, float]]) -> list[float]:
    from phyto_shared.uv_chromophore import _local_maxima

    xs = [w for w, _a in spectrum]
    ys = [a for _w, a in spectrum]
    if not xs:
        return []
    return [x for x, _y in _local_maxima(xs, ys)]


def _attach_marker_evidence(
    rank: CandidateRank,
    hits: list[Any],
    supporting: list[Any],
) -> CandidateRank:
    """Attach one marker-check evidence item to every rank.

    The marker check is about the PEAK (species authenticity), so every
    candidate sees it; only supporting markers contribute positive
    strength, and each item carries its pharmacopoeial reference.

    Miss semantics (ADR-0066, chemotype-variation caveat): an unflagged
    marker miss carries strength 0.0 and NO score penalty -- seasonal,
    ontogenetic and extraction-driven variation mean a genuine sample can
    lack a monograph marker, so a miss is never treated as proof of
    absence and must never outweigh the support bonus.  Only a marker
    explicitly flagged ``expected_in_matrix`` (the monograph's passing
    criterion for this matrix) takes the larger miss penalty when absent.
    """
    from taxon_prior.markers import (
        MARKER_MISS_PENALTY,
        MARKER_MISS_PENALTY_UNFLAGGED,
        MARKER_SUPPORT_BONUS,
    )

    if not hits:
        return rank
    _hit = lambda h: bool(h.rt_in_range or h.uv_in_range)  # noqa: E731

    def _miss_item(h: Any) -> EvidenceItem:
        # House convention (cf. rt_reranker): strength is |delta| and the
        # direction lives in the relation name -- a flagged miss is a
        # penalty relation, an unflagged miss is neutral evidence.
        if h.expected_in_matrix:
            return EvidenceItem(
                source_spectrum=f"pharmacopoeial_marker:{h.reference}",
                spectral_feature=(
                    f"marker {h.marker} (expected in matrix): RT in "
                    f"range={h.rt_in_range}, UV in range={h.uv_in_range} "
                    f"(expected RT {h.rt_expected[0]}-{h.rt_expected[1]} min)"
                ),
                relation="pharmacopoeial_marker_miss_penalty",
                strength=MARKER_MISS_PENALTY,
            )
        return EvidenceItem(
            source_spectrum=f"pharmacopoeial_marker:{h.reference}",
            spectral_feature=(
                f"marker {h.marker}: RT in range={h.rt_in_range}, "
                f"UV in range={h.uv_in_range} (expected RT "
                f"{h.rt_expected[0]}-{h.rt_expected[1]} min)"
            ),
            relation="pharmacopoeial_marker_miss",
            strength=0.0,
        )

    items = [
        EvidenceItem(
            source_spectrum=f"pharmacopoeial_marker:{h.reference}",
            spectral_feature=(
                f"marker {h.marker}: RT in range={h.rt_in_range}, "
                f"UV in range={h.uv_in_range} (expected RT "
                f"{h.rt_expected[0]}-{h.rt_expected[1]} min)"
            ),
            relation="pharmacopoeial_marker_match",
            strength=0.3,
        )
        if _hit(h)
        else _miss_item(h)
        for h in hits[:5]
    ]
    # A miss on an unflagged marker contributes 0 (never negative), so the
    # support-only baseline is the floor for genuine samples; a flagged
    # miss is the only path to a real penalty, and its magnitude must stay
    # strictly larger than the support bonus to be meaningful.
    assert MARKER_MISS_PENALTY > MARKER_SUPPORT_BONUS
    flagged_miss = any(
        h.expected_in_matrix and not _hit(h) for h in hits
    )
    if flagged_miss:
        marker_delta = -MARKER_MISS_PENALTY
    elif supporting:
        marker_delta = MARKER_SUPPORT_BONUS
    else:
        marker_delta = MARKER_MISS_PENALTY_UNFLAGGED
    score = (
        rank.score - marker_delta
        if rank.ranking_convention == "lower_is_better"
        else rank.score + marker_delta
    )
    graph = rank.evidence_graph
    if graph is not None:
        graph = graph.model_copy(
            update={"evidence": [*graph.evidence, *items]}
        )
    chain_items = [
        EvidenceItem(
            source_spectrum=f"pharmacopoeial_marker:{h.reference}",
            spectral_feature=f"marker {h.marker}",
            relation="pharmacopoeial_markers",
            strength=0.3 if _hit(h)
            else (MARKER_MISS_PENALTY if h.expected_in_matrix else 0.0),
        )
        for h in hits[:5]
    ]
    return rank.model_copy(
        update={
            "score": round(score, 6),
            "evidence_graph": graph,
            "classification_chain": [
                *rank.classification_chain, "pharmacopoeial_markers"
            ],
            "evidence_chain": [*rank.evidence_chain, *chain_items],
        }
    )