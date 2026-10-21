"""Uncertainty quantification: ranking, margins, coverage, verdicts, novelty.

Two orthogonal axes drive every identification decision:

* **Confidence** (``ConfidenceVerdict``) -- how well the evidence supports
  a structure.
* **Novelty** (``NoveltyStatus``) -- whether that structure has literature
  precedent.  A compound can be CONFIRMED and novel, or CANDIDATE and
  known; the two axes never collapse into one label (ADR-0006).

Ranking APIs come in explicit pairs (ADR-0007):

* ``*_higher_is_better`` -- similarity/coverage-style scores (SUMMIT
  combined scores, cosine similarities, coverage fractions)
* ``*_lower_is_better`` -- distance-style scores (HSQC Modified Hungarian
  distances, ppm residuals)

There is deliberately no ambiguous single-convention function; downstream
callers must pick the variant matching their score semantics.

All decision thresholds are named module-level constants so they can be
audited, tuned, and argued about in one place -- never hidden inside a
function body.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import (
    MSIConfidenceLevel,
    NOVELTY_STATUS_DEFINITIONS,
    ConfidenceVerdict,
    NoveltyStatus,
    ToolTier,
)
from .exceptions import InvalidInputError

__all__ = [
    "EvidenceAssessment",
    "rank_higher_is_better",
    "rank_lower_is_better",
    "is_unique_top1_higher_is_better",
    "is_unique_top1_lower_is_better",
    "margin_higher_is_better",
    "margin_lower_is_better",
    "coverage_confidence",
    "verdict_from_evidence",
    # Thresholds (exported for audit and tuning)
    "MIN_COVERAGE_FOR_CANDIDATE",
    "MIN_COVERAGE_FOR_TENTATIVE",
    "MIN_MARGIN_FOR_TENTATIVE",
    "MIN_MARGIN_FOR_CONFIRMED",
    "MAX_TIES_FOR_UNAMBIGUOUS",
    "RESIDUAL_DECAY_SCALE",
]

# --- Named, auditable thresholds ------------------------------------------

MIN_COVERAGE_FOR_CANDIDATE = 0.50
"""Below this observed coverage the platform abstains entirely."""

MIN_COVERAGE_FOR_TENTATIVE = 0.80
"""Minimum observed coverage for a tentative (Level 2/3) annotation."""

MIN_MARGIN_FOR_TENTATIVE = 0.02
"""Minimum score margin over the best distinct competitor for 'tentative'."""

MIN_MARGIN_FOR_CONFIRMED = 0.10
"""Minimum margin for 'confirmed' even when MSI Level 1 evidence exists:
a standard match that barely beats its rivals still needs human eyes."""

MAX_TIES_FOR_UNAMBIGUOUS = 1
"""Number of statistically tied candidates (including the target) still
compatible with an unambiguous claim; anything larger is AMBIGUOUS."""

RESIDUAL_DECAY_SCALE = 1.0
"""Residual scale (in the units the caller uses, ppm-scale NMR residuals by
default) at which the coverage-confidence heuristic decays by ~63%."""


# --- Shared preconditions and core arithmetic ------------------------------


def _validated(scores: list[float], target_index: int) -> float:
    """Shared precondition checks; returns the target's score."""
    if not scores:
        raise InvalidInputError("scores must be a non-empty list", context={})
    if not 0 <= target_index < len(scores):
        raise InvalidInputError(
            "target_index out of range",
            context={"target_index": target_index, "n_scores": len(scores)},
        )
    return scores[target_index]


def _better_and_tied(
    scores: list[float], target_index: int, atol: float, higher_is_better: bool
) -> tuple[int, int]:
    """Count competitors ranking ahead of / tied with the target.

    ``better`` counts scores strictly better than the target beyond
    ``atol`` (above it when higher is better, below it when lower is
    better); ``tied`` counts scores within ``atol`` of the target.
    """
    target = _validated(scores, target_index)
    if higher_is_better:
        better = sum(1 for s in scores if s > target + atol)
    else:
        better = sum(1 for s in scores if s < target - atol)
    tied = sum(1 for s in scores if abs(s - target) <= atol)
    return better, tied


def _distinct_competitors(
    scores: list[float], target_index: int, atol: float
) -> tuple[float, list[float]]:
    """Return (target score, other scores distinct from it beyond ``atol``)."""
    target = _validated(scores, target_index)
    distinct = [
        s for i, s in enumerate(scores) if i != target_index and abs(s - target) > atol
    ]
    return target, distinct


# --- Ranking (explicit pairs; ADR-0007) -------------------------------------


def rank_higher_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> tuple[int, int]:
    """Best-to-worst rank of the target when HIGHER scores rank first.

    Use for similarity/coverage-style scores (SUMMIT combined score,
    cosine, coverage).

    best  = 1 + #(scores strictly greater than target beyond ``atol``)
    worst = best + #(scores tied with target within ``atol``) - 1

    Empty input or a bad index raises ``InvalidInputError``.
    """
    better, tied = _better_and_tied(scores, target_index, atol, higher_is_better=True)
    best = 1 + better
    return best, best + tied - 1


def rank_lower_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> tuple[int, int]:
    """Best-to-worst rank of the target when LOWER scores rank first.

    Use for distance-style scores (HSQC Modified Hungarian distances, ppm
    residuals).  Identical arithmetic to ``rank_higher_is_better`` with the
    comparison inverted:

    best  = 1 + #(scores strictly smaller than target beyond ``atol``)
    worst = best + #(scores tied with target within ``atol``) - 1
    """
    better, tied = _better_and_tied(scores, target_index, atol, higher_is_better=False)
    best = 1 + better
    return best, best + tied - 1


def is_unique_top1_higher_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> bool:
    """True only when the target is the sole rank-1 candidate (higher wins)."""
    best, worst = rank_higher_is_better(scores, target_index, atol=atol)
    return best == 1 and worst == 1


def is_unique_top1_lower_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> bool:
    """True only when the target is the sole rank-1 candidate (lower wins)."""
    best, worst = rank_lower_is_better(scores, target_index, atol=atol)
    return best == 1 and worst == 1


# --- Margins (explicit pairs; ADR-0007) --------------------------------------


def margin_higher_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> float:
    """Target score minus its best *distinct* competitor's score (higher wins).

    Returns ``math.inf`` when no distinct competitor exists at all
    (single-element list), and ``0.0`` when every competitor ties the
    target -- zero separation must never read as infinite confidence
    (ADR-0004, point 2).  Negative when the target is not top-1.
    """
    target, distinct = _distinct_competitors(scores, target_index, atol)
    if not distinct:
        return math.inf if len(scores) == 1 else 0.0
    return target - max(distinct)


def margin_lower_is_better(
    scores: list[float], target_index: int, atol: float = 1e-8
) -> float:
    """Best distinct competitor's score minus the target's (lower wins).

    The competitor to beat is the SMALLEST other score, so the margin is
    ``min(distinct) - target``: positive when the target is uniquely best
    (lowest), negative when another score undercuts it.  Same special
    cases as ``margin_higher_is_better``: ``inf`` for a single-element
    list, ``0.0`` when all competitors tie.
    """
    target, distinct = _distinct_competitors(scores, target_index, atol)
    if not distinct:
        return math.inf if len(scores) == 1 else 0.0
    return min(distinct) - target


# --- Coverage heuristic -------------------------------------------------------


def coverage_confidence(
    obs_coverage: float, ref_coverage: float, residual: float
) -> float:
    """Heuristic 0-1 confidence combining coverage and matching residual.

    Formula::

        conf = sqrt(obs_coverage * ref_coverage) * exp(-residual / RESIDUAL_DECAY_SCALE)

    clamped to [0, 1].

    ``obs_coverage``  -- fraction of observed peaks the candidate explains
    ``ref_coverage``  -- fraction of the candidate's predicted peaks that
                         are actually observed
    ``residual``      -- typical absolute shift deviation (ppm-scale by
                         convention; keep it consistent with
                         ``RESIDUAL_DECAY_SCALE``)

    Limitations (documented, not hidden): this is an uncalibrated heuristic,
    NOT a probability; it treats the three inputs as independent; it is
    symmetric in the two coverages via their geometric mean.  Use it to
    rank and to flag weak matches -- never as a standalone identification
    score.
    """
    for name, value in (
        ("obs_coverage", obs_coverage),
        ("ref_coverage", ref_coverage),
    ):
        if not 0.0 <= value <= 1.0:
            raise InvalidInputError(
                f"{name} must be within [0, 1]", context={name: value}
            )
    if residual < 0:
        raise InvalidInputError(
            "residual must be non-negative (it is an absolute deviation)",
            context={"residual": residual},
        )
    conf = math.sqrt(obs_coverage * ref_coverage) * math.exp(
        -residual / RESIDUAL_DECAY_SCALE
    )
    return min(1.0, max(0.0, conf))


# --- Verdict + novelty assessment --------------------------------------------


@dataclass(frozen=True)
class EvidenceAssessment:
    """The two orthogonal outcomes of an identification decision.

    ``verdict``  -- confidence axis: how well the evidence supports the
    structure (one of the five ``ConfidenceVerdict`` values).
    ``novelty``  -- precedent axis: whether the compound is known, novel,
    or unassessed.  Tracked separately because a compound can be CONFIRMED
    and novel (known structure, new natural source) or CANDIDATE and novel
    (plausible structure without literature match) -- ADR-0006.
    """

    verdict: ConfidenceVerdict
    novelty: NoveltyStatus


def verdict_from_evidence(
    coverage: float,
    margin: float,
    n_tied: int,
    msi_level: int | MSIConfidenceLevel,
    tool_tier: int | ToolTier,
    novelty: NoveltyStatus = "uncertain",
) -> EvidenceAssessment:
    """Map evidence onto an honest ``EvidenceAssessment``.

    Returns BOTH orthogonal axes; the verdict axis ignores ``novelty``
    entirely (and vice versa).  Confidence decision rules, evaluated in
    order (first match wins):

    1.  ``coverage < MIN_COVERAGE_FOR_CANDIDATE``          -> ABSTAIN
        (nothing to build a claim on).
    2.  ``tool_tier is TIER_3_LLM_GENERATED``              -> CANDIDATE
        (LLM-originated structures stay hypotheses, one tier below any
        evidence-based claim, regardless of the numbers).
    3.  ``n_tied > MAX_TIES_FOR_UNAMBIGUOUS``              -> AMBIGUOUS
        (tied candidates are reported as a set).
    4.  MSI Level 1 AND ``margin >= MIN_MARGIN_FOR_CONFIRMED``
        AND ``coverage >= MIN_COVERAGE_FOR_TENTATIVE``     -> CONFIRMED
        (a near-tied standard match still needs human review, so weak
        margins fall through to the next rules).
    5.  ``coverage >= MIN_COVERAGE_FOR_TENTATIVE`` AND
        ``margin >= MIN_MARGIN_FOR_TENTATIVE``             -> TENTATIVE.
    6.  Otherwise                                           -> CANDIDATE.

    ``margin`` may be ``math.inf`` (no distinct competitor).  Rule 4 still
    requires recorded human-in-the-loop approval at the call site before
    the platform *reports* "confirmed".

    ``novelty`` is passed through unchanged; determining it (library-match
    analysis) is downstream's job, and ``"uncertain"`` is the honest
    default.
    """
    if not 0.0 <= coverage <= 1.0:
        raise InvalidInputError("coverage must be within [0, 1]", context={"coverage": coverage})
    if n_tied < 1:
        raise InvalidInputError(
            "n_tied counts the target itself and must be >= 1",
            context={"n_tied": n_tied},
        )
    if novelty not in NOVELTY_STATUS_DEFINITIONS:
        raise InvalidInputError(
            "novelty must be one of 'novel', 'known', 'uncertain'",
            context={"novelty": novelty, "allowed": sorted(NOVELTY_STATUS_DEFINITIONS)},
        )

    if coverage < MIN_COVERAGE_FOR_CANDIDATE:
        verdict = ConfidenceVerdict.ABSTAIN
    elif tool_tier == ToolTier.TIER_3_LLM_GENERATED:
        verdict = ConfidenceVerdict.CANDIDATE
    elif n_tied > MAX_TIES_FOR_UNAMBIGUOUS:
        verdict = ConfidenceVerdict.AMBIGUOUS
    elif (
        int(msi_level) == int(MSIConfidenceLevel.LEVEL_1)
        and margin >= MIN_MARGIN_FOR_CONFIRMED
        and coverage >= MIN_COVERAGE_FOR_TENTATIVE
    ):
        verdict = ConfidenceVerdict.CONFIRMED
    elif coverage >= MIN_COVERAGE_FOR_TENTATIVE and margin >= MIN_MARGIN_FOR_TENTATIVE:
        verdict = ConfidenceVerdict.TENTATIVE
    else:
        verdict = ConfidenceVerdict.CANDIDATE

    return EvidenceAssessment(verdict=verdict, novelty=novelty)
