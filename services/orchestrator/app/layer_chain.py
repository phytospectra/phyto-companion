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

import numpy as np

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    ReferenceStandard,
)
from phyto_shared.constants import ConfidenceVerdict
from phyto_shared.models import CandidateRank, EvidenceItem
from phyto_shared.uv_chromophore import classify_uv

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

# ---------------------------------------------------------------------------
# Metabolomics evidence layer chain (ADR-0068..ADR-0072)
# ---------------------------------------------------------------------------
# Sibling of apply_chromatography_layers, same discipline: additive over
# whatever inputs exist, every skip a named reason, never silent, and the
# existing identify workflow is untouched.  Where the chromatography chain
# consumes CandidateRank objects, this chain consumes a feature matrix
# (bucketed NMR peaks or an adapted LC-MS table) plus per-sample metadata,
# and emits EvidenceItems against a claim string -- each layer appends to
# one shared evidence list, each failure is recorded as evidence, and the
# remaining steps always run.

from phyto_shared.metabolomics import (  # noqa: E402
    MetabolomicsInput,
    PCAModelEvidence,
    PLSModelEvidence,
    PreprocessingRecord,
    ReportingChecklist,
    STOCSYCorrelation,
    VIPFeature,
)


class MetabolomicsChainResult:
    """The evidence graph contents plus which layers ran or skipped."""

    def __init__(
        self,
        evidence: list[EvidenceItem],
        skipped: list[dict[str, str]],
        claim: str,
    ) -> None:
        self.evidence = evidence
        self.skipped_layers = skipped
        self.claim = claim
        self.preprocessing_record: PreprocessingRecord | None = None
        self.pca_evidence: PCAModelEvidence | None = None
        self.model_evidence: PLSModelEvidence | None = None
        self.vip_features: list[VIPFeature] = []
        self.stocsy_correlations: list[STOCSYCorrelation] = []
        self.checklist: ReportingChecklist | None = None
        self.matrix: Any = None
        self.feature_ppm: Any = None
        self.sample_ids: list[str] = []

    def skipped_reasons(self) -> list[str]:
        return [entry["reason"] for entry in self.skipped]


def _metabolomics_item(
    source: str,
    feature: str,
    relation: str,
    strength: float,
) -> EvidenceItem:
    return EvidenceItem(
        source_spectrum=source,
        spectral_feature=feature,
        relation=relation,
        strength=max(0.0, min(1.0, strength)),
    )


def _skip_item(relation: str, reason: str) -> EvidenceItem:
    """A skip AS EVIDENCE: the relation names the skip, the reason lives
    in spectral_feature (house pattern; EvidenceItem has no payload)."""
    return _metabolomics_item(
        source="metabolomics:layer_chain",
        feature=reason,
        relation=relation,
        strength=0.0,
    )


def apply_metabolomics_layers(
    metabolomics_input: MetabolomicsInput,
    sample_sheet: dict[str, dict[str, str]] | None = None,
    preprocessing_result: Any = None,
    *,
    pca_min_samples: int = 2,
    vip_threshold: float = 1.0,
    stocsy_r_threshold: float = 0.7,
    stocsy_fdr_threshold: float = 0.05,
    run_2d_stocsy: bool = True,
) -> MetabolomicsChainResult:
    """Run every metabolomics layer whose inputs exist; report skips.

    Order (ADR-0068..ADR-0072):
      preprocessing (nmr/lcms, external result or skip)
        -> feature matrix
        -> pca                [always if >= pca_min_samples]
        -> pls_da / opls_reg  [depends on Y type from the sample sheet]
        -> vip_selection
        -> stocsy             [needs >= 3 spectra; 1D needs a driver peak]
        -> reporting_checklist

    ``preprocessing_result`` is a ``nmr_preproc.pipeline.PreprocessResult``
    (or any object carrying .matrix, .ppm, .sample_ids, .record); the
    chain never runs preprocessing itself.  A layer failure is recorded
    as evidence and the remaining steps still run -- the chromatography
    chain's discipline.
    """
    evidence: list[EvidenceItem] = []
    skipped: list[dict[str, str]] = []
    claim = "metabolomics analysis evidence chain (ADR-0068..ADR-0072)"
    result = MetabolomicsChainResult(evidence, skipped, claim)

    def skip_layer(tag: str, relation: str, reason: str) -> None:
        skipped.append({"layer": tag, "reason": f"layer skipped: {reason}"})
        evidence.append(_skip_item(relation, reason))

    # --- preprocessing ------------------------------------------------------
    matrix = None
    ppm_axis = None
    if preprocessing_result is None:
        skip_layer(
            "preprocessing",
            "nmr_preprocessing_skipped"
            if metabolomics_input.kind == "nmr_bruker_dirs"
            else "lcms_preprocessing_skipped",
            "no preprocessing result supplied (no raw FID / unsupported export)",
        )
    else:
        record = getattr(preprocessing_result, "record", None)
        matrix = np.asarray(preprocessing_result.matrix, dtype=float) \
            if hasattr(np, "asarray") else None
        ppm_axis = getattr(preprocessing_result, "ppm", None)
        result.sample_ids = list(getattr(preprocessing_result, "sample_ids", []))
        result.preprocessing_record = record
        evidence.append(
            _metabolomics_item(
                source="nmr_preproc:pipeline"
                if (record is None and metabolomics_input.kind == "nmr_bruker_dirs")
                or (record and record.modality == "nmr")
                else "lcms_preproc:adapter",
                feature=(
                    f"preprocessing applied: apodisation={record.apodisation_function}, "
                    f"baseline={record.baseline_method}, alignment={record.alignment_method}, "
                    f"normalisation={record.normalisation_method}"
                    if record
                    else "preprocessing applied (no record attached)"
                ),
                relation=(
                    "nmr_preprocessing_applied"
                    if (record is None and metabolomics_input.kind == "nmr_bruker_dirs")
                    or (record and record.modality == "nmr")
                    else "lcms_preprocessing_applied"
                ),
                strength=1.0,
            )
        )
        result.matrix, result.feature_ppm = matrix, ppm_axis

    if matrix is None or getattr(matrix, "size", 0) == 0:
        # nothing downstream can run without a matrix: report every
        # remaining layer as skipped with its reason -- visible, not silent
        for tag, relation, reason in (
            ("pca", "statistical_model_skipped", "no feature matrix (preprocessing skipped)"),
            ("supervised_model", "statistical_model_skipped", "no feature matrix (preprocessing skipped)"),
            ("vip_selection", "vip_selection_empty", "no feature matrix (preprocessing skipped)"),
            ("stocsy", "stocsy_skipped", "no feature matrix (preprocessing skipped)"),
            ("reporting_checklist", "reporting_checklist", "nothing to report on (preprocessing skipped)"),
        ):
            skip_layer(tag, relation, reason)
        return result

    # --- sample sheet lookups -------------------------------------------------
    sheet = sample_sheet or {}
    y_continuous: dict[str, float] = {}
    y_labels: dict[str, str] = {}
    if metabolomics_input.continuous_covariate and sheet:
        for sample_id, row in sheet.items():
            raw = row.get(metabolomics_input.continuous_covariate)
            if raw is None or raw == "":
                continue
            try:
                y_continuous[sample_id] = float(raw)
            except ValueError:
                pass
    if metabolomics_input.group_column and sheet:
        for sample_id, row in sheet.items():
            label = row.get(metabolomics_input.group_column)
            if label:
                y_labels[sample_id] = label

    analysis_ids = [s for s in result.sample_ids if s in sheet or not sheet]
    if sheet:
        analysis_ids = [s for s in result.sample_ids if s in sheet]

    # --- PCA -------------------------------------------------------------------
    pca_evidence: PCAModelEvidence | None = None
    try:
        if matrix.shape[0] < pca_min_samples:
            skip_layer(
                "pca", "statistical_model_skipped",
                f"fewer than {pca_min_samples} samples ({matrix.shape[0]})",
            )
        else:
            from sklearn.decomposition import PCA

            n_components = max(1, min(3, min(matrix.shape) - 1)) if min(matrix.shape) > 1 else 1
            model = PCA(n_components=n_components)
            scores = model.fit_transform(matrix)
            # outliers: samples whose max-metric distance to the PC1-2
            # centroid exceeds 3x the median distance (documented heuristic)
            outlier_ids: list[str] = []
            if n_components >= 1:
                distances = np.linalg.norm(scores - scores.mean(axis=0), axis=1)
                median = float(np.median(distances))
                if median > 0:
                    outlier_ids = [
                        result.sample_ids[i]
                        for i, d in enumerate(distances)
                        if d > 3.0 * median
                    ]
            batch_note = None
            batch_col = next(
                (c for c in ("rhizome_batch_id", "batch", "plate") if sheet and any(c in row for row in sheet.values())),
                None,
            )
            if batch_col and n_components >= 1 and len(analysis_ids) == len(result.sample_ids):
                batches = [sheet.get(s, {}).get(batch_col, "") for s in result.sample_ids]
                if len(set(batches)) > 1:
                    batch_note = (
                        "batch metadata supplied "
                        f"({batch_col}, {len(set(batches))} levels); a formal "
                        "batch-association test is not implemented -- inspect "
                        "PC1-2 scores against batch labels manually"
                    )
            r2x = [float(v) for v in model.explained_variance_ratio_]
            pca_evidence = PCAModelEvidence(
                n_components=n_components,
                r2x_per_component=[round(v, 4) for v in r2x],
                cumulative_r2x=round(float(np.sum(r2x)), 4),
                outlier_sample_ids=outlier_ids,
                batch_association=batch_note,
                n_samples=matrix.shape[0],
            )
            result.pca_evidence = pca_evidence
            evidence.append(
                _metabolomics_item(
                    source="metabolomics:pca",
                    feature=(
                        f"PCA: {n_components} components, cumulative R2X "
                        f"{pca_evidence.cumulative_r2x:.4f}"
                        + (f", outliers {outlier_ids}" if outlier_ids else "")
                        + (f"; {batch_note}" if batch_note else "")
                    ),
                    relation="pca_model",
                    strength=min(1.0, pca_evidence.cumulative_r2x),
                )
            )
    except Exception as exc:  # noqa: BLE001 - failure recorded, chain continues
        evidence.append(
            _metabolomics_item(
                "metabolomics:pca", f"PCA failed: {exc}", "model_validation_failed", 0.0
            )
        )

    # --- supervised model -------------------------------------------------------
    model_evidence: PLSModelEvidence | None = None
    vip_source_model = None
    vip_feature_ids: list[str] = []
    # The chain deliberately does NOT import the metabolomics service
    # internals at module scope; it locates them the way the tool registry
    # mock does (try/except import, documented in tool_registry.py).
    try:
        if y_continuous:
            matched = [s for s in result.sample_ids if s in y_continuous]
            if len(matched) < 5:
                skip_layer(
                    "supervised_model", "statistical_model_skipped",
                    f"continuous covariate present but only {len(matched)} "
                    "sample(s) carry it; O-PLS regression needs >= 5",
                )
            else:
                try:
                    from metabolomics.opls_regression import opls_regression
                except ImportError:
                    from services.metabolomics.app.opls_regression import (  # type: ignore
                        opls_regression,
                    )
                try:
                    from metabolomics.feature_detection import (
                        Feature,
                        FeatureTable,
                        SampleMetadata,
                    )
                except ImportError:
                    from services.metabolomics.app.feature_detection import (  # type: ignore
                        Feature,
                        FeatureTable,
                        SampleMetadata,
                    )
                table = FeatureTable(
                    features=[
                        Feature(
                            feature_id=f"B{j}",
                            mz=float(j + 1),
                            rt=float(j),
                            intensities={
                                result.sample_ids[i]: float(matrix[i, j])
                                for i in range(matrix.shape[0])
                            },
                        )
                        for j in range(matrix.shape[1])
                    ],
                    samples=[
                        SampleMetadata(sample_id=s)
                        for s in result.sample_ids
                        if s in y_continuous
                    ],
                )
                opls = opls_regression(
                    table,
                    {s: y_continuous[s] for s in result.sample_ids if s in y_continuous},
                )
                model_evidence = opls.evidence
                result.model_evidence = opls.evidence
                vip_source_model = opls
                vip_feature_ids = opls.feature_ids
                evidence.append(
                    _metabolomics_item(
                        source="metabolomics:opls_regression",
                        feature=(
                            f"O-PLS regression on continuous Y: R2Y "
                            f"{opls.evidence.r2y}, Q2 {opls.evidence.q2}, "
                            f"permutation p {opls.evidence.permutation_p_value} "
                            f"(n={opls.evidence.n_permutations}), CV {opls.evidence.cv_scheme}"
                        ),
                        relation="opls_regression_model",
                        strength=1.0
                        if (
                            (opls.evidence.q2 or 0) > 0
                            and (opls.evidence.permutation_p_value or 1) <= 0.05
                        )
                        else 0.3,
                    )
                )
                if (opls.evidence.q2 or 0) <= 0 or (
                    opls.evidence.permutation_p_value or 1
                ) > 0.05:
                    evidence.append(
                        _metabolomics_item(
                            source="metabolomics:opls_regression",
                            feature=(
                                f"model validation failed: Q2 {opls.evidence.q2} "
                                f"(<= 0) or permutation p {opls.evidence.permutation_p_value} "
                                "(> 0.05); treat the model as exploratory only"
                            ),
                            relation="model_validation_failed",
                            strength=0.0,
                        )
                    )
        elif y_labels:
            matched = [s for s in result.sample_ids if s in y_labels]
            classes = sorted(set(y_labels[s] for s in matched))
            if len(matched) < 4 or len(classes) != 2:
                skip_layer(
                    "supervised_model", "statistical_model_skipped",
                    f"categorical labels present but design unusable: "
                    f"{len(matched)} labelled samples, classes {classes} "
                    "(PLS-DA needs >= 4 samples in exactly two classes)",
                )
            else:
                try:
                    from metabolomics.statistics import pls_da
                except ImportError:
                    from services.metabolomics.app.statistics import pls_da  # type: ignore
                try:
                    from metabolomics.feature_detection import FeatureTable, Feature, SampleMetadata
                except ImportError:
                    from services.metabolomics.app.feature_detection import (  # type: ignore
                        FeatureTable, Feature, SampleMetadata,
                    )
                table = FeatureTable(
                    features=[
                        Feature(
                            feature_id=f"B{j}",
                            mz=float(j + 1),
                            rt=float(j),
                            intensities={
                                result.sample_ids[i]: float(matrix[i, j])
                                for i in range(matrix.shape[0])
                            },
                        )
                        for j in range(matrix.shape[1])
                    ],
                    samples=[
                        SampleMetadata(sample_id=s) for s in matched
                    ],
                )
                pls = pls_da(table, {s: y_labels[s] for s in matched})
                model_evidence = PLSModelEvidence(
                    model_kind="pls_da",
                    n_components=pls.n_components,
                    r2y=pls.r2y,
                    q2=pls.q2,
                    permutation_p_value=pls.permutation_p_value,
                    n_permutations=pls.n_permutations,
                    class_balance=f"two classes: {classes}",
                    cv_scheme="3-fold CV (existing statistics.pls_da)",
                )
                result.model_evidence = model_evidence
                vip_source_model = pls
                vip_feature_ids = pls.feature_ids
                evidence.append(
                    _metabolomics_item(
                        source="metabolomics:pls_da",
                        feature=(
                            f"PLS-DA: R2Y {pls.r2y}, Q2 {pls.q2}, permutation p "
                            f"{pls.permutation_p_value} (n={pls.n_permutations})"
                        ),
                        relation="pls_da_model",
                        strength=1.0
                        if (pls.q2 > 0 and pls.permutation_p_value <= 0.05)
                        else 0.3,
                    )
                )
        else:
            skip_layer(
                "supervised_model", "statistical_model_skipped",
                "no covariate or class labels in the sample sheet "
                "(supply continuous_covariate or group_column)",
            )
    except Exception as exc:  # noqa: BLE001 - failure recorded, chain continues
        evidence.append(
            _metabolomics_item(
                "metabolomics:supervised_model",
                f"supervised modelling failed: {exc}",
                "model_validation_failed",
                0.0,
            )
        )

    # --- VIP selection -------------------------------------------------------------
    try:
        if vip_source_model is None:
            skip_layer(
                "vip_selection", "vip_selection_empty",
                "no supervised model produced VIP scores (no model ran)",
            )
        else:
            try:
                from metabolomics.vip import select_vip_features, vip_from_opls
            except ImportError:
                from services.metabolomics.app.vip import (  # type: ignore
                    select_vip_features, vip_from_opls,
                )
            if hasattr(vip_source_model, "vip"):
                vips = list(vip_source_model.vip)  # PLSDAResult carries .vip
            else:
                vips = vip_from_opls(vip_source_model)
            selection = select_vip_features(vips, vip_feature_ids, threshold=vip_threshold)
            passing = [f for f in selection.features if f.passes_threshold]
            result.vip_features = selection.features
            if not passing:
                skip_layer(
                    "vip_selection", "vip_selection_empty",
                    selection.empty_reason or "no feature passed the VIP threshold",
                )
            for feature in passing[:20]:
                evidence.append(
                    _metabolomics_item(
                        source="metabolomics:vip",
                        feature=(
                            f"VIP {feature.vip_score:.3f} > {feature.threshold} "
                            f"for {feature.feature_id} ({feature.pairing_note})"
                        ),
                        relation="vip_feature",
                        strength=min(1.0, feature.vip_score / 10.0),
                    )
                )
    except Exception as exc:  # noqa: BLE001 - failure recorded, chain continues
        evidence.append(
            _metabolomics_item(
                "metabolomics:vip", f"VIP selection failed: {exc}",
                "model_validation_failed", 0.0,
            )
        )

    # --- STOCSY -----------------------------------------------------------------------
    try:
        if matrix.shape[0] < 3:
            skip_layer(
                "stocsy", "stocsy_skipped",
                f"fewer than 3 spectra ({matrix.shape[0]}); correlations "
                "would be meaningless",
            )
        else:
            if ppm_axis is None:
                skip_layer(
                    "stocsy", "stocsy_skipped",
                    "no ppm axis available (LC-MS feature table); STOCSY "
                    "is defined over NMR chemical shifts",
                )
            else:
                try:
                    from metabolomics.stocsy import (
                        SkipSTOCSY,
                        all_pairs_stocsy,
                        driver_stocsy,
                        STOCSY_HYPOTHESIS_WARNING,
                    )
                except ImportError:
                    from services.metabolomics.app.stocsy import (  # type: ignore
                        SkipSTOCSY,
                        all_pairs_stocsy,
                        driver_stocsy,
                        STOCSY_HYPOTHESIS_WARNING,
                    )
                driver = metabolomics_input.driver_peak_ppm
                if driver is None:
                    skip_layer(
                        "stocsy_1d", "stocsy_skipped",
                        "no driver peak supplied (driver_peak_ppm unset); "
                        "1D driver STOCSY not run",
                    )
                else:
                    try:
                        driver_result = driver_stocsy(
                            matrix, ppm_axis, driver,
                            r_threshold=stocsy_r_threshold,
                            fdr_threshold=stocsy_fdr_threshold,
                        )
                        result.stocsy_correlations.extend(driver_result.correlations)
                        for corr in driver_result.correlations[:20]:
                            evidence.append(
                                _metabolomics_item(
                                    source="metabolomics:stocsy",
                                    feature=(
                                        f"driver {corr.driver_ppm:.2f} ppm vs "
                                        f"{corr.bucket_ppm:.2f} ppm: r={corr.pearson_r}, "
                                        f"FDR={corr.fdr}. {STOCSY_HYPOTHESIS_WARNING}"
                                    ),
                                    relation="stocsy_correlation",
                                    strength=min(1.0, abs(corr.pearson_r)),
                                )
                            )
                    except SkipSTOCSY as skip:
                        skip_layer("stocsy_1d", "stocsy_skipped", skip.reason)
                if run_2d_stocsy:
                    try:
                        pairs_result = all_pairs_stocsy(
                            matrix, ppm_axis,
                            r_threshold=stocsy_r_threshold,
                            fdr_threshold=stocsy_fdr_threshold,
                        )
                        result.stocsy_correlations.extend(pairs_result.correlations)
                        for corr in pairs_result.correlations[:20]:
                            evidence.append(
                                _metabolomics_item(
                                    source="metabolomics:stocsy",
                                    feature=(
                                        f"2D pair {corr.driver_ppm:.2f} vs "
                                        f"{corr.bucket_ppm:.2f} ppm: r={corr.pearson_r}, "
                                        f"FDR={corr.fdr}. {STOCSY_HYPOTHESIS_WARNING}"
                                    ),
                                    relation="stocsy_correlation",
                                    strength=min(1.0, abs(corr.pearson_r)),
                                )
                            )
                    except SkipSTOCSY as skip:
                        skip_layer("stocsy_2d", "stocsy_skipped", skip.reason)
    except Exception as exc:  # noqa: BLE001 - failure recorded, chain continues
        evidence.append(
            _metabolomics_item(
                "metabolomics:stocsy", f"STOCSY failed: {exc}",
                "model_validation_failed", 0.0,
            )
        )

    # --- reporting checklist ---------------------------------------------------------
    try:
        try:
            from metabolomics.reporting import build_reporting_checklist
        except ImportError:
            from services.metabolomics.app.reporting import (  # type: ignore
                build_reporting_checklist,
            )
        checklist = build_reporting_checklist(
            result.preprocessing_record,
            pca=pca_evidence,
            model=model_evidence,
            vip_threshold=vip_threshold if vip_source_model is not None else None,
            stocsy_driver_ppm=metabolomics_input.driver_peak_ppm,
            stocsy_thresholds=(
                f"|r| >= {stocsy_r_threshold}, FDR <= {stocsy_fdr_threshold}"
                if result.stocsy_correlations or metabolomics_input.driver_peak_ppm
                else None
            ),
        )
        result.checklist = checklist
        evidence.append(
            _metabolomics_item(
                source="metabolomics:reporting",
                feature=(
                    f"reporting completeness {checklist.completeness:.0%} "
                    f"({len(checklist.provided_fields)}/{len(checklist.required_fields)})"
                    + (
                        f"; missing: {', '.join(checklist.missing_fields)}"
                        if checklist.missing_fields
                        else "; no missing fields"
                    )
                ),
                relation="reporting_checklist",
                strength=checklist.completeness,
            )
        )
    except Exception as exc:  # noqa: BLE001 - failure recorded, chain continues
        evidence.append(
            _metabolomics_item(
                "metabolomics:reporting",
                f"reporting checklist failed: {exc}",
                "model_validation_failed",
                0.0,
            )
        )

    return result
