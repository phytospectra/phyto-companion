"""Candidate ranking by combined NMR + MS evidence.

Every score is a distance (lower is better): MatchResults are distances,
the combined weighted score is a distance, and the resulting
CandidateRank objects carry ``ranking_convention="lower_is_better"``
with ranking intervals computed by ``rank_lower_is_better`` (Step 2).
Verdicts come from ``verdict_from_evidence`` -- tied candidates surface
as AMBIGUOUS, never CONFIRMED.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from phyto_shared.constants import (
    ELEMENT_MASSES,
    MSIConfidenceLevel,
    ObservationModality,
    ToolTier,
    modality_families,
    single_family_gap,
)
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.lipid_regions import (
    DEFAULT_LIPID_REGIONS_13C,
    curate_13c_assignment,
)
from phyto_shared.models import (
    Candidate,
    CandidateEvidenceProfile,
    CandidateRank,
    EvidenceGraph,
    EvidenceItem,
    MolecularFormula,
    Adduct,
)
from phyto_shared.uncertainty import (
    margin_lower_is_better,
    rank_lower_is_better,
    verdict_from_evidence,
)

from .hungarian_matcher import (
    MatchResult,
    match_1d,
    match_carbon_1d,
    match_carbon_full,
    match_hsqc,
)
from .shift_predictor import ShiftPredictor, get_shift_predictor

__all__ = [
    "ObservedEvidence",
    "RankingConfig",
    "parse_formula_string",
    "ms2_cosine",
    "rank_candidates",
    "score_molecule",
    "supplied_modalities",
    "evidence_profile",
    "lipid_curation_13c",
]

_ELEMENT_PATTERN = re.compile(r"([A-Z][a-z]?)(\d*)")

# Matcher result keys -> the modality they represent.  MS1 is deliberately
# absent: precursor m/z is a filter, never a score (ADR-0014).
_SCORE_MODALITIES: dict[str, ObservationModality] = {
    "h_1d": ObservationModality.H_1D,
    "c_1d": ObservationModality.C_1D,
    "hsqc": ObservationModality.HSQC,
    "ms2": ObservationModality.MS2,
}


class ObservedEvidence(BaseModel):
    """Everything observed about the unknown, per modality."""

    model_config = ConfigDict(extra="forbid")

    h_1d: Optional[list[float]] = None
    c_1d: Optional[list[float]] = None
    hsqc: Optional[list[tuple[float, float, str]]] = None  # (h, c, multiplicity)
    ms2: Optional[list[tuple[float, float]]] = None  # (mz, intensity)
    formula: Optional[str] = None
    precursor_mz: Optional[float] = None
    precursor_adduct: Optional[str] = None


class RankingConfig(BaseModel):
    """Weights and matching thresholds for candidate ranking."""

    model_config = ConfigDict(extra="forbid")

    weight_h: float = 0.2
    weight_c: float = 0.3
    weight_hsqc: float = 0.4
    weight_ms2: float = 0.1
    require_formula_match: bool = True
    tol_h: float = 0.05
    tol_c: float = 0.5
    tol_c_full: float = 3.0
    cap: float = 4.0
    # Precursor m/z is a FILTER, not a score (ADR-0014): candidates whose
    # adduct-adjusted ion mass is outside this window are excluded from
    # ranking entirely.
    precursor_tolerance_ppm: float = 5.0
    # MS2 distance conversion (ADR-0014): reference-vs-observed spectral
    # cosine (higher is better) converts to a distance via 1 - cosine.
    ms2_distance: Literal["one_minus_cosine"] = "one_minus_cosine"
    fragment_tolerance_da: float = 0.02
    # 13C lipid-region exclusion (ADR-0076): observed 13C peaks inside
    # the lipid/acyl contaminant envelopes (glycerol backbone, acyl
    # chains) are dropped from c_1d/hsqc evidence BEFORE matching, so a
    # lipid-contaminated extract cannot manufacture false assignments in
    # the 60-80 ppm glycerol window.  Empty tuple disables the filter.
    exclude_lipid_regions_13c: tuple[tuple[float, float], ...] = (
        DEFAULT_LIPID_REGIONS_13C
    )


def ms2_cosine(
    reference: list[tuple[float, float]],
    observed: list[tuple[float, float]],
    fragment_tolerance_da: float = 0.02,
) -> tuple[float, int]:
    """Spectral cosine between reference and observed MS2 spectra.

    Peaks are matched greedily by ascending m/z distance within the
    fragment tolerance; the cosine uses sqrt-transformed intensities of
    the matched pairs only (GNPS convention).  Returns
    ``(cosine in [0, 1], n_matched_peaks)``.
    """
    import numpy as np

    ref = sorted(reference)
    obs = sorted(observed)
    matched_ref: list[float] = []
    matched_obs: list[float] = []
    used_obs = [False] * len(obs)
    for ref_mz, ref_intensity in ref:
        best_index = -1
        best_distance = fragment_tolerance_da + 1.0
        for j, (obs_mz, _) in enumerate(obs):
            if used_obs[j]:
                continue
            distance = abs(ref_mz - obs_mz)
            if distance <= fragment_tolerance_da and distance < best_distance:
                best_distance = distance
                best_index = j
        if best_index >= 0:
            used_obs[best_index] = True
            matched_ref.append(ref_intensity)
            matched_obs.append(obs[best_index][1])
    if not matched_ref:
        return 0.0, 0
    a = np.sqrt(np.asarray(matched_ref, dtype=float))
    b = np.sqrt(np.asarray(matched_obs, dtype=float))
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0, 0
    return float(float(np.dot(a, b)) / denominator), len(matched_ref)


def parse_formula_string(text: str) -> dict[str, int]:
    """Parse 'C6H12O6'-style formula strings into element counts.

    Validated against the shared element table; raises InvalidInputError
    on unknown elements or empty input.
    """
    text = text.strip()
    if not text:
        raise InvalidInputError("empty formula string", context={})
    counts: dict[str, int] = {}
    position = 0
    for match in _ELEMENT_PATTERN.finditer(text):
        if match.start() != position:
            raise InvalidInputError(
                "formula contains unparseable characters",
                context={"formula": text, "position": position},
            )
        element = match.group(1)
        if element not in ELEMENT_MASSES:
            raise InvalidInputError(
                "unknown element in formula",
                context={"formula": text, "element": element},
            )
        count = int(match.group(2)) if match.group(2) else 1
        counts[element] = counts.get(element, 0) + count
        position = match.end()
    if position != len(text) or not counts:
        raise InvalidInputError("malformed formula", context={"formula": text})
    return counts


def _predicted_views(prediction) -> tuple[list[float], list[float], list[tuple[float, float]]]:
    """Extract 1H, 13C, and (h, c) HSQC views from a ShiftPrediction,
    dropping the -1.0 'not predicted' sentinels."""
    h_1d = [
        h for h in prediction.predicted_h_shifts if h >= 0.0
    ]
    c_1d = [
        c
        for symbol, c in zip(prediction.atom_symbols, prediction.predicted_c_shifts)
        if symbol == "C" and c >= 0.0
    ]
    hsqc = [
        (h, c)
        for symbol, h, c in zip(
            prediction.atom_symbols,
            prediction.predicted_h_shifts,
            prediction.predicted_c_shifts,
        )
        if symbol == "C" and h >= 0.0 and c >= 0.0
    ]
    return h_1d, c_1d, hsqc


def supplied_modalities(observed: ObservedEvidence) -> frozenset[ObservationModality]:
    """Which modalities the user actually supplied.

    ``precursor_mz`` alone counts as MS1: it is real evidence (a mass
    filter), even though it never becomes a structural score.  MS2 counts
    only when peaks are present -- an empty fragment list is not evidence.
    """
    modalities: set[ObservationModality] = set()
    if observed.h_1d:
        modalities.add(ObservationModality.H_1D)
    if observed.c_1d:
        modalities.add(ObservationModality.C_1D)
    if observed.hsqc:
        modalities.add(ObservationModality.HSQC)
    if observed.precursor_mz is not None:
        modalities.add(ObservationModality.MS1)
    if observed.ms2:
        modalities.add(ObservationModality.MS2)
    return frozenset(modalities)


def evidence_profile(
    scored: set[str],
    observed: ObservedEvidence,
    gaps: list[EvidenceItem] | None = None,
) -> CandidateEvidenceProfile:
    """Describe which modalities were scored and what could not be checked.

    ``scored`` holds the names the matcher actually produced (the keys of
    a modality-results dict), so this reports behaviour, not intent.  MS1
    is applied as a mass FILTER (ADR-0014) and never appears as a score, so
    it is neither ``evaluated`` nor ``unavailable`` -- its presence still
    counts toward the instrument families covered.
    """
    supplied = supplied_modalities(observed)
    evaluated = {
        ObservationModality(name) for name in scored if name in _SCORE_MODALITIES
    }
    # A supplied, score-capable modality with no score is a gap, not
    # agreement: the canonical case is MS2 with no reference spectrum.
    ms1_filter = {ObservationModality.MS1} if ObservationModality.MS1 in supplied else set()
    supporting = evaluated | ms1_filter
    unavailable = {
        modality
        for modality in supplied - supporting
        if modality in _SCORE_MODALITIES
    }
    # Families are counted from what actually contributed, not from what
    # the user sent: a supplied-but-unusable MS2 must not upgrade an
    # NMR-only assignment to "cross-modal".
    families = modality_families(supporting)
    gap = single_family_gap(supporting)
    if gap and unavailable:
        # The base sentence says a family was never supplied; here the user
        # did supply it but it could not be scored, so say that instead of
        # claiming it is absent.
        missing = "mass spectrometry" if families == {"NMR"} else "NMR"
        gap = (
            "single-family evidence: the supplied "
            f"{', '.join(sorted(m.value for m in unavailable))} could not be "
            f"scored (no reference spectrum to compare against), so it adds "
            f"no support; this assignment rests on {next(iter(families))} "
            f"alone, with no {missing} cross-check"
        )
    return CandidateEvidenceProfile(
        evaluated=sorted(m.value for m in evaluated),
        unavailable=sorted(m.value for m in unavailable),
        families=sorted(families),
        is_single_family=len(families) <= 1,
        gap=gap,
    )


def _precursor_passes(
    observed: ObservedEvidence, candidate: Candidate, config: RankingConfig
) -> bool:
    """Precursor m/z agreement as a FILTER (ADR-0014), not a score."""
    if observed.precursor_mz is None:
        return True
    adduct_name = observed.precursor_adduct or "[M+H]+"
    try:
        adduct = Adduct.from_name(adduct_name)
    except InvalidInputError:
        adduct = Adduct.from_name("[M+H]+")
    ion = adduct.ion_mz(candidate.exact_mass)
    ppm = abs(ion - observed.precursor_mz) / observed.precursor_mz * 1e6
    return ppm <= config.precursor_tolerance_ppm


def _match_ms2(
    observed: ObservedEvidence,
    candidate: Candidate,
    config: RankingConfig,
    reference_ms2: list[tuple[float, float]] | None,
) -> tuple[MatchResult | None, EvidenceItem | None]:
    """Reference-spectrum MS2 matching (ADR-0014).

    Returns (match, gap_note): the match is None when MS2 scoring is
    disabled (no reference spectrum available for this candidate), in
    which case gap_note explains why.
    """
    if observed.ms2 is None:
        return None, None
    if not reference_ms2:
        return None, EvidenceItem(
            source_spectrum="observed:ms2",
            spectral_feature=f"{len(observed.ms2)} MS2 peaks observed",
            relation="ms2_scoring_disabled",
            strength=0.0,
        )
    cosine, n_matched = ms2_cosine(
        reference_ms2, [tuple(p) for p in observed.ms2], config.fragment_tolerance_da
    )
    distance = 1.0 - cosine if config.ms2_distance == "one_minus_cosine" else 1.0 - cosine
    result = MatchResult(
        score=round(distance, 6),
        coverage=(n_matched / len(observed.ms2)) if observed.ms2 else 0.0,
        n_observed=len(observed.ms2),
        n_predicted=len(reference_ms2),
        assignments=[],
        unmatched_observed_indices=list(range(n_matched, len(observed.ms2))),
        unmatched_predicted_indices=[],
        ranking_convention="lower_is_better",
    )
    return result, None


def _modality_scores(
    observed: ObservedEvidence,
    prediction,
    candidate: Candidate,
    config: RankingConfig,
    reference_ms2: list[tuple[float, float]] | None = None,
) -> tuple[dict[str, MatchResult], list[EvidenceItem]]:
    """Run every available modality match for one candidate.

    Returns (results, gap_notes); gap_notes carry disabled-modality
    explanations (e.g. MS2 without a reference spectrum).
    """
    results: dict[str, MatchResult] = {}
    gap_notes: list[EvidenceItem] = []
    pred_h, pred_c, pred_hsqc = _predicted_views(prediction)

    if observed.h_1d and pred_h:
        results["h_1d"] = match_1d(pred_h, observed.h_1d, config.tol_h, config.cap)
    if observed.c_1d and pred_c:
        if observed.hsqc is not None:
            # DEPT/edited-style carbon list available -> tight tolerance
            results["c_1d"] = match_carbon_1d(
                pred_c, observed.c_1d, config.tol_c, config.cap
            )
        else:
            results["c_1d"] = match_carbon_full(
                pred_c, observed.c_1d, config.tol_c_full, config.cap
            )
    if observed.hsqc and pred_hsqc:
        results["hsqc"] = match_hsqc(
            pred_hsqc,
            [tuple(p) for p in observed.hsqc],
            config.tol_h,
            config.tol_c,
            config.cap,
            multiplicity_mode="edited",
        )
    ms2, ms2_gap = _match_ms2(observed, candidate, config, reference_ms2)
    if ms2 is not None:
        results["ms2"] = ms2
    elif ms2_gap is not None:
        gap_notes.append(ms2_gap)
    if not results:
        supplied = supplied_modalities(observed)
        if ObservationModality.MS2 in supplied:
            detail = (
                "MS2 peaks were supplied but no reference spectrum is "
                "available for this candidate, so there is nothing to score "
                "against (a precursor m/z alone is a filter, not a score)"
            )
        elif supplied & {ObservationModality.MS1}:
            detail = (
                "only MS1 evidence was supplied: a precursor m/z filters "
                "candidates but cannot rank them, so an MS2 spectrum (with "
                "a reference) or any NMR modality is required"
            )
        else:
            detail = (
                "no modality available for ranking: provide h_1d, c_1d, or "
                "hsqc in the observed evidence (precursor m/z is a filter, "
                "not a score; MS2 needs a reference spectrum)"
            )
        raise InvalidInputError(detail, context={"supplied": sorted(m.value for m in supplied)})
    return results, gap_notes


def _combined_score(
    results: dict[str, MatchResult], config: RankingConfig
) -> tuple[float, float]:
    """Weighted distance over available modalities + overall coverage."""
    weights = {
        "h_1d": config.weight_h,
        "c_1d": config.weight_c,
        "hsqc": config.weight_hsqc,
        "ms2": config.weight_ms2,
    }
    total_weight = sum(weights[name] for name in results)
    combined = sum(weights[name] * result.score for name, result in results.items())
    combined /= total_weight
    coverage = sum(
        weights[name] * result.coverage for name, result in results.items()
    ) / total_weight
    return combined, coverage


def score_molecule(
    observed: ObservedEvidence,
    smiles: str,
    config: RankingConfig | None = None,
    predictor: ShiftPredictor | None = None,
    exact_mass: float | None = None,
) -> tuple[float, dict[str, MatchResult]]:
    """Combined distance of one structure against the observed evidence.

    Used directly by site localization (scoring simulated edits) and
    internally by ``rank_candidates``.
    """
    predictor = predictor or get_shift_predictor()
    config = config or RankingConfig()
    prediction = predictor.predict(smiles)
    if exact_mass is None:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors

        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            raise InvalidInputError("cannot parse SMILES", context={"smiles": smiles})
        exact_mass = rdMolDescriptors.CalcExactMolWt(mol)
    stand_in = Candidate(
        id="score-stand-in",
        name="stand-in",
        smiles=smiles,
        formula=MolecularFormula(counts={"C": 1}),
        exact_mass=exact_mass,
        provenance="structure_only",
        source="scoring",
    )
    results, _gaps = _modality_scores(observed, prediction, stand_in, config)
    combined, _ = _combined_score(results, config)
    return combined, results


def _evidence_items_for(
    candidate: Candidate, results: dict[str, MatchResult], cap: float
) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for modality, result in sorted(results.items()):
        n_matched = len(result.assignments) - len(
            [a for a in result.assignments if a.cost >= cap]
        )
        items.append(
            EvidenceItem(
                source_spectrum=f"observed:{modality}",
                spectral_feature=(
                    f"{n_matched}/{result.n_observed} observed peaks matched "
                    f"within tolerance"
                ),
                relation=f"matched:{modality}",
                strength=round(max(0.0, 1.0 - result.score / cap), 4),
            )
        )
        for idx in result.unmatched_observed_indices[:10]:
            items.append(
                EvidenceItem(
                    source_spectrum=f"observed:{modality}",
                    spectral_feature=f"{modality} peak #{idx}",
                    relation="unexplained_observed_peak",
                    strength=0.0,
                )
            )
        for idx in result.unmatched_predicted_indices[:10]:
            items.append(
                EvidenceItem(
                    source_spectrum=f"predicted:{modality}",
                    spectral_feature=f"{modality} predicted peak #{idx}",
                    relation="unexplained_predicted_peak",
                    strength=0.0,
                )
            )
    return items


def lipid_curation_13c(
    observed: ObservedEvidence, config: RankingConfig | None = None
) -> tuple[ObservedEvidence, list[EvidenceItem]]:
    """Apply the 13C lipid-region exclusion to observed evidence.

    Returns the filtered evidence (new object; the input is never
    mutated) plus one EvidenceItem documenting the exclusion so the
    ranking's audit chain shows what was removed and why.  The curation
    runs on BOTH c_1d shifts and HSQC carbon coordinates -- a lipid
    cross-peak must not survive through the 2D path either.
    """
    config = config or RankingConfig()
    if not config.exclude_lipid_regions_13c:
        return observed, []
    notes: list[EvidenceItem] = []
    updates: dict = {}
    if observed.c_1d:
        curation = curate_13c_assignment(
            [(c, 1.0) for c in observed.c_1d],
            regions=config.exclude_lipid_regions_13c,
        )
        if curation.excluded:
            updates["c_1d"] = [c for c, _p in curation.kept]
            notes.append(EvidenceItem(
                source_spectrum="observed:c_1d",
                spectral_feature=(
                    f"{len(curation.excluded)} 13C peak(s) excluded as "
                    "lipid-region signals (ADR-0076): "
                    + ", ".join(f"{c:.2f}" for c, _p, _r in curation.excluded)
                ),
                relation="lipid_region_excluded",
                strength=0.0,
            ))
    if observed.hsqc:
        kept_hsqc = [
            (h, c, m)
            for h, c, m in observed.hsqc
            if not any(
                lo <= c <= hi for lo, hi in config.exclude_lipid_regions_13c
            )
        ]
        excluded = len(observed.hsqc) - len(kept_hsqc)
        if excluded:
            updates["hsqc"] = kept_hsqc
            notes.append(EvidenceItem(
                source_spectrum="observed:hsqc",
                spectral_feature=(
                    f"{excluded} HSQC cross-peak(s) excluded as "
                    "lipid-region signals (ADR-0076)"
                ),
                relation="lipid_region_excluded",
                strength=0.0,
            ))
    if not updates:
        return observed, []
    return observed.model_copy(update=updates), notes


def rank_candidates(
    observed: ObservedEvidence,
    candidates: list[Candidate],
    config: RankingConfig | None = None,
    predictor: ShiftPredictor | None = None,
    reference_ms2: dict[str, list[tuple[float, float]]] | None = None,
) -> list[CandidateRank]:
    """Rank candidates by combined evidence; lower distance ranks first.

    Candidates failing the precursor-m/z FILTER (ADR-0014) are excluded
    from the returned ranks entirely; candidates failing the formula
    match are kept but pinned at the maximum distance.
    """
    if not candidates:
        return []
    config = config or RankingConfig()
    predictor = predictor or get_shift_predictor()
    reference_ms2 = reference_ms2 or {}

    # 13C lipid-region exclusion (ADR-0076) BEFORE any matching; the
    # exclusion is recorded as evidence and re-attached to every rank.
    observed, lipid_notes = lipid_curation_13c(observed, config)

    observed_counts = (
        parse_formula_string(observed.formula) if observed.formula else None
    )

    admitted: list[Candidate] = []
    for candidate in candidates:
        if _precursor_passes(observed, candidate, config):
            admitted.append(candidate)
    candidates = admitted
    if not candidates:
        return []

    scores: list[float] = []
    coverages: list[float] = []
    per_candidate_results: list[dict[str, MatchResult]] = []
    per_candidate_gaps: list[list[EvidenceItem]] = []
    formula_failed: list[bool] = []

    for candidate in candidates:
        prediction = predictor.predict(candidate.smiles)
        results, gap_notes = _modality_scores(
            observed, prediction, candidate, config, reference_ms2.get(candidate.id)
        )
        failed = False
        if (
            config.require_formula_match
            and observed_counts is not None
            and dict(candidate.formula.counts) != observed_counts
        ):
            failed = True
        formula_failed.append(failed)
        if failed:
            combined, coverage = config.cap, 0.0
        else:
            combined, coverage = _combined_score(results, config)
        scores.append(round(combined, 6))
        coverages.append(coverage)
        per_candidate_results.append(results)
        per_candidate_gaps.append(gap_notes)

    ranks: list[CandidateRank] = []
    for index, candidate in enumerate(candidates):
        best, worst = rank_lower_is_better(scores, index)
        margin = margin_lower_is_better(scores, index)
        tied_ids = [
            other.id
            for j, other in enumerate(candidates)
            if abs(scores[j] - scores[index]) <= 1e-8
        ]
        n_tied = worst - best + 1
        assessment = verdict_from_evidence(
            coverage=coverages[index],
            margin=margin,
            n_tied=n_tied,
            msi_level=MSIConfidenceLevel.LEVEL_2,
            tool_tier=ToolTier.TIER_2_ML_VALIDATED,
            novelty="known",  # database candidates are known compounds
        )
        evidence = _evidence_items_for(
            candidate, per_candidate_results[index], config.cap
        )
        evidence.extend(per_candidate_gaps[index])
        if formula_failed[index]:
            evidence.append(
                EvidenceItem(
                    source_spectrum="observed:formula",
                    spectral_feature=f"candidate formula {candidate.formula}",
                    relation="formula_mismatch",
                    strength=0.0,
                )
            )
        graph = EvidenceGraph(
            node_id=candidate.id,
            claim=f"identity: {candidate.name}",
            evidence=evidence,
            confidence=coverages[index],
            gaps=[
                f"{n} unexplained peak(s)" if (n := len([
                    e for e in evidence
                    if e.relation.startswith("unexplained")
                ])) else "all observed peaks explained"
            ],
        )
        profile = evidence_profile(
            set(per_candidate_results[index]), observed, per_candidate_gaps[index]
        )
        if profile.gap:
            # The single-family caveat belongs on the claim, not only in a
            # side channel: a reader of ``gaps`` must not conclude that
            # an NMR-only assignment was cross-modal supported (ADR-0050).
            graph.gaps.append(profile.gap)
        ranks.append(
            CandidateRank(
                candidate=candidate,
                score=scores[index],
                ranking_convention="lower_is_better",
                coverage=round(coverages[index], 4),
                margin=margin,
                ranking_interval=(best, worst),
                tied_candidates=tied_ids,
                verdict=assessment.verdict,
                evidence_graph=graph,
                evidence_profile=profile,
            )
        )
    return sorted(ranks, key=lambda r: r.score)
