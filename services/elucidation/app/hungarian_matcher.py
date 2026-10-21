"""Hungarian matching of observed vs predicted shifts (SUMMIT v4 matcher).

Four modes over one core: 1D edited carbon (DEPT-like), 1D full carbon,
HSQC (with the edited-mode multiplicity penalty), and multinuclear HSQC
(31P / 15N hetero nuclei).

Every score here is a DISTANCE: lower is better, and every MatchResult
carries ``ranking_convention="lower_is_better"`` -- required, never
defaulted (scientific integrity requirement 1 and 5).
"""

from __future__ import annotations

from typing import Literal, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.optimize import linear_sum_assignment

__all__ = [
    "MatchAssignment",
    "MatchResult",
    "match_1d",
    "match_carbon_1d",
    "match_carbon_full",
    "match_hsqc",
    "match_hsqc_multinuclear",
    "MULTIPLICITY_PENALTY_FACTOR",
]

MULTIPLICITY_PENALTY_FACTOR = 10.0
"""Mismatched multiplicity classes cost ``cap * this`` (SUMMIT v4 edited
HSQC mode: CH/CH3 can never beat a compatible match with CH2)."""

# CH and CH3 share a compatibility group; CH2 is its own (edited HSQC /
# DEPT multiplicity classes).
_COMPATIBLE_MULTIPLICITY: dict[str, frozenset[str]] = {
    "CH": frozenset({"CH", "CH3"}),
    "CH3": frozenset({"CH", "CH3"}),
    "CH2": frozenset({"CH2"}),
}


class MatchAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicted_index: int = Field(..., ge=0)
    observed_index: int = Field(..., ge=0)
    cost: float = Field(..., ge=0.0)


class MatchResult(BaseModel):
    """Outcome of one matching run.  ``score`` is a normalized distance:
    lower is better; ``cap`` is the score of a completely disjoint pair."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(..., ge=0.0)
    coverage: float = Field(..., ge=0.0, le=1.0)
    n_observed: int = Field(..., ge=0)
    n_predicted: int = Field(..., ge=0)
    assignments: list[MatchAssignment] = Field(default_factory=list)
    unmatched_predicted_indices: list[int] = Field(default_factory=list)
    unmatched_observed_indices: list[int] = Field(default_factory=list)
    ranking_convention: Literal["lower_is_better"]

    @staticmethod
    def _require_convention() -> None:  # pragma: no cover - documentation hook
        """The field is required (no default): constructing a MatchResult
        without ranking_convention raises ValidationError by design."""


def _multiplicity_of(entry: Sequence[float | str | int]) -> str | None:
    """Third tuple element ('CH'/'CH2'/'CH3' or 1/2/3), if present."""
    if len(entry) < 3:
        return None
    value = entry[2]
    mapping = {1: "CH", 2: "CH2", 3: "CH3"}
    if isinstance(value, int):
        return mapping.get(value)
    text = str(value).upper().replace(" ", "")
    return text if text in ("CH", "CH2", "CH3") else None


def _solve(
    cost: np.ndarray,
    n_predicted: int,
    n_observed: int,
    cap: float,
) -> MatchResult:
    rows, cols = linear_sum_assignment(cost)
    matched_predicted = set(rows.tolist())
    matched_observed = set(cols.tolist())
    assignments = [
        MatchAssignment(
            predicted_index=int(r), observed_index=int(c), cost=float(cost[r, c])
        )
        for r, c in zip(rows, cols)
    ]
    n_matched = sum(1 for a in assignments if a.cost < cap)
    return MatchResult(
        score=float(cost[rows, cols].sum() / max(n_predicted, n_observed, 1)),
        coverage=(n_matched / n_observed) if n_observed else 0.0,
        n_observed=n_observed,
        n_predicted=n_predicted,
        assignments=assignments,
        unmatched_predicted_indices=sorted(set(range(n_predicted)) - matched_predicted),
        unmatched_observed_indices=sorted(set(range(n_observed)) - matched_observed),
        ranking_convention="lower_is_better",
    )


def _match_1d(
    predicted: Sequence[float],
    observed: Sequence[float],
    tol: float,
    cap: float,
) -> MatchResult:
    """1D matching: cost = |difference| within tolerance, else ``cap``."""
    pred = np.asarray(predicted, dtype=float).reshape(-1)
    obs = np.asarray(observed, dtype=float).reshape(-1)
    if pred.size == 0 or obs.size == 0:
        raise ValueError("predicted and observed peak lists must be non-empty")
    diff = np.abs(pred[:, None] - obs[None, :])
    cost = np.where(diff <= tol, diff, cap)
    return _solve(cost, pred.size, obs.size, cap)


def match_1d(
    predicted: list[float],
    observed: list[float],
    tol: float,
    cap: float = 4.0,
) -> MatchResult:
    """Generic 1D matching (any axis: 1H, 13C, ...): cost is the absolute
    deviation within tolerance, else ``cap``."""
    return _match_1d(predicted, observed, tol, cap)


def match_carbon_1d(
    predicted_c: list[float],
    observed_c: list[float],
    tol_c: float = 0.5,
    cap: float = 4.0,
) -> MatchResult:
    """Edited/DEPT-style 1D 13C matching (tight tolerance)."""
    return _match_1d(predicted_c, observed_c, tol_c, cap)


def match_carbon_full(
    predicted_c: list[float],
    observed_c: list[float],
    tol_c: float = 3.0,
    cap: float = 4.0,
) -> MatchResult:
    """Full-spectrum 1D 13C matching (looser tolerance)."""
    return _match_1d(predicted_c, observed_c, tol_c, cap)


def _match_2d(
    predicted: Sequence[Sequence[float | str | int]],
    observed: Sequence[Sequence[float | str | int]],
    tol_first: float,
    tol_second: float,
    cap: float,
    apply_multiplicity_penalty: bool,
    label: str,
) -> MatchResult:
    """Core 2D matcher: normalized deviation within both tolerances, else
    cap; optional edited-mode multiplicity penalty (cap x factor)."""
    pred = np.asarray(
        [[p[0], p[1]] for p in predicted], dtype=float
    ).reshape(-1, 2)
    obs = np.asarray([[o[0], o[1]] for o in observed], dtype=float).reshape(-1, 2)
    if pred.size == 0 or obs.size == 0:
        raise ValueError(f"predicted and observed {label} lists must be non-empty")

    d_first = np.abs(pred[:, None, 0] - obs[None, :, 0])
    d_second = np.abs(pred[:, None, 1] - obs[None, :, 1])
    within = (d_first <= tol_first) & (d_second <= tol_second)
    normalized = np.sqrt(
        (d_first / tol_first) ** 2 + (d_second / tol_second) ** 2
    ) / np.sqrt(2.0)
    cost = np.where(within, np.minimum(normalized, 1.0) * cap, cap)

    if apply_multiplicity_penalty:
        for i, p in enumerate(predicted):
            p_mult = _multiplicity_of(p)
            if p_mult is None:
                continue
            for j, o in enumerate(observed):
                o_mult = _multiplicity_of(o)
                if o_mult is None:
                    continue
                if o_mult not in _COMPATIBLE_MULTIPLICITY[p_mult]:
                    cost[i, j] += cap * MULTIPLICITY_PENALTY_FACTOR

    return _solve(cost, len(pred), len(obs), cap)


def match_hsqc(
    predicted: list[tuple[float, float] | tuple[float, float, str]],
    observed: list[tuple[float, float] | tuple[float, float, str]],
    tol_h: float = 0.05,
    tol_c: float = 0.5,
    cap: float = 4.0,
    multiplicity_mode: Literal["none", "edited"] = "edited",
) -> MatchResult:
    """HSQC matching (h, c) pairs; entries may carry a third element with
    multiplicity ('CH'/'CH2'/'CH3').  In edited mode, CH <-> CH2 pairs pay
    ``cap * 10`` so they can never win an assignment against a compatible
    alternative."""
    return _match_2d(
        predicted,
        observed,
        tol_h,
        tol_c,
        cap,
        apply_multiplicity_penalty=(multiplicity_mode == "edited"),
        label="HSQC",
    )


def match_hsqc_multinuclear(
    predicted: list[tuple[float, float]],
    observed: list[tuple[float, float]],
    hetero_nucleus: Literal["13C", "31P", "15N"],
    tol_h: float = 0.05,
    tol_x: float = 0.5,
    cap: float = 4.0,
) -> MatchResult:
    """Multinuclear variant: the heteronuclear axis may be 31P or 15N with
    its own tolerance (no multiplicity handling)."""
    if hetero_nucleus == "13C":
        # plain HSQC via the same core
        return _match_2d(
            predicted, observed, tol_h, tol_x, cap,
            apply_multiplicity_penalty=False, label="HSQC",
        )
    # 31P / 15N windows are much wider in ppm
    tol_x_effective = tol_x if hetero_nucleus != "31P" else max(tol_x, 2.0)
    return _match_2d(
        predicted, observed, tol_h, tol_x_effective, cap,
        apply_multiplicity_penalty=False, label=f"HSQC-{hetero_nucleus}",
    )
