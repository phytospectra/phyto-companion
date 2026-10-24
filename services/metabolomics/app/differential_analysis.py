"""Differential analysis: group comparisons, volcano data, time courses,
dose-response (4-parameter logistic)."""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy.optimize import curve_fit
from scipy import stats

from phyto_shared.exceptions import InvalidInputError

from .feature_detection import FeatureTable
from .statistics import UnivariateResult, fdr_correction, univariate_test

__all__ = [
    "ComparisonResult",
    "DifferentialResult",
    "TimeCourseResult",
    "DoseResponseResult",
    "compare_groups",
    "volcano_plot_data",
    "time_course_analysis",
    "dose_response_analysis",
]


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group: str
    reference_group: str
    univariate: UnivariateResult
    fdr_p_value: list[float]
    significant_features: list[str]


class DifferentialResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparisons: list[ComparisonResult]
    method: str


class TimeCourseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_ids: list[str]
    slope: list[float]
    r_squared: list[float]
    p_value: list[float]
    n_timepoints: int


class DoseResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_ids: list[str]
    ec50: list[float | None]
    hill_slope: list[float | None]
    r_squared: list[float]
    ec50_ci95: list[tuple[float, float] | None]
    fit_failed: list[bool]


def compare_groups(
    table: FeatureTable,
    groups: dict[str, list[str]],
    reference_group: str,
    method: Literal["welch", "student", "mann_whitney"] = "welch",
) -> DifferentialResult:
    """Compare each group against the reference with FDR correction."""
    if reference_group not in groups:
        raise InvalidInputError(
            "reference group not in groups",
            context={"reference_group": reference_group, "groups": sorted(groups)},
        )
    labels: dict[str, str] = {}
    for group, members in groups.items():
        for member in members:
            labels[member] = group
    sample_ids = {s.sample_id for s in table.samples}
    unknown = [m for members in groups.values() for m in members if m not in sample_ids]
    if unknown:
        raise InvalidInputError(
            "group members missing from table", context={"unknown": unknown}
        )

    comparisons: list[ComparisonResult] = []
    for group in sorted(groups):
        if group == reference_group:
            continue
        two_group_ids = set(groups[group]) | set(groups[reference_group])
        subset = table.model_copy(
            update={"samples": [s for s in table.samples if s.sample_id in two_group_ids]}
        )
        subset_labels = {s: labels[s] for s in two_group_ids}
        result = univariate_test(subset, subset_labels, method)
        adjusted = [float(v) for v in fdr_correction(result.p_value)]
        significant = [
            feature_id
            for feature_id, q in zip(result.feature_ids, adjusted)
            if q < 0.05
        ]
        comparisons.append(
            ComparisonResult(
                group=group,
                reference_group=reference_group,
                univariate=result,
                fdr_p_value=adjusted,
                significant_features=significant,
            )
        )
    return DifferentialResult(comparisons=comparisons, method=method)


def volcano_plot_data(result: DifferentialResult) -> dict:
    """Frontend-ready points: x = log2 FC, y = -log10 FDR p."""
    points = []
    for comparison in result.comparisons:
        univariate = comparison.univariate
        for index, feature_id in enumerate(univariate.feature_ids):
            q = comparison.fdr_p_value[index]
            points.append(
                {
                    "feature_id": feature_id,
                    "comparison": f"{comparison.group}_vs_{comparison.reference_group}",
                    "x": univariate.log2_fold_change[index],
                    "y": float(-np.log10(max(q, 1e-300))),
                    "significant": q < 0.05,
                }
            )
    return {"points": points, "x_label": "log2 fold change", "y_label": "-log10(FDR p)"}


def time_course_analysis(
    table: FeatureTable,
    sample_times: dict[str, float],
) -> TimeCourseResult:
    """Per-feature linear trend over time (slope, R2, p)."""
    from .feature_detection import table_to_matrix

    sample_ids, feature_ids, matrix = table_to_matrix(table)
    missing = [s for s in sample_ids if s not in sample_times]
    if missing:
        raise InvalidInputError(
            "time values missing for samples", context={"missing": missing}
        )
    x = np.array([sample_times[s] for s in sample_ids], dtype=float)
    if len(np.unique(x)) < 2:
        raise InvalidInputError("need at least two distinct timepoints", context={})

    slopes: list[float] = []
    r2s: list[float] = []
    ps: list[float] = []
    for j in range(matrix.shape[1]):
        fit = stats.linregress(x, matrix[:, j])
        slopes.append(float(fit.slope))
        r2s.append(float(fit.rvalue**2))
        ps.append(float(fit.pvalue))
    return TimeCourseResult(
        feature_ids=feature_ids,
        slope=slopes,
        r_squared=r2s,
        p_value=ps,
        n_timepoints=len(np.unique(x)),
    )


def _four_param_logistic(dose: np.ndarray, bottom: float, top: float, ec50: float, hill: float):
    return bottom + (top - bottom) / (1.0 + (ec50 / np.maximum(dose, 1e-12)) ** hill)


def dose_response_analysis(
    table: FeatureTable,
    sample_doses: dict[str, float],
) -> DoseResponseResult:
    """4-parameter logistic fits per feature (EC50, Hill, R2, CI)."""
    from .feature_detection import table_to_matrix

    sample_ids, feature_ids, matrix = table_to_matrix(table)
    missing = [s for s in sample_ids if s not in sample_doses]
    if missing:
        raise InvalidInputError(
            "doses missing for samples", context={"missing": missing}
        )
    dose = np.array([sample_doses[s] for s in sample_ids], dtype=float)
    if len(np.unique(dose)) < 4:
        raise InvalidInputError(
            "4-parameter logistic fits need >= 4 distinct doses",
            context={"n_distinct": len(np.unique(dose))},
        )

    ec50s: list[float | None] = []
    hills: list[float | None] = []
    r2s: list[float] = []
    cis: list[tuple[float, float] | None] = []
    failed: list[bool] = []
    for j in range(matrix.shape[1]):
        y = matrix[:, j]
        try:
            lower, upper = float(np.min(y)), float(np.max(y))
            guess = [lower, upper, float(np.median(dose)), 1.0]
            bounds = (
                [min(0.0, lower) - 1, upper - abs(upper) - 1, 1e-9, 0.05],
                [lower + abs(lower) + 1 if lower != upper else upper + 1,
                 max(upper, lower) * 2 + 1, float(np.max(dose)) * 100, 10.0],
            )
            popt, pcov = curve_fit(
                _four_param_logistic, dose, y, p0=guess, bounds=bounds, maxfev=5000
            )
            residuals = y - _four_param_logistic(dose, *popt)
            ss_res = float(np.sum(residuals**2))
            ss_tot = float(np.sum((y - np.mean(y)) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
            ec50_std = float(np.sqrt(pcov[2, 2])) if np.all(np.isfinite(pcov)) else None
            ci = (
                (float(popt[2] - 1.96 * ec50_std), float(popt[2] + 1.96 * ec50_std))
                if ec50_std is not None and np.isfinite(ec50_std)
                else None
            )
            ec50s.append(float(popt[2]))
            hills.append(float(popt[3]))
            r2s.append(round(r2, 4))
            cis.append(ci)
            failed.append(False)
        except (RuntimeError, ValueError):
            ec50s.append(None)
            hills.append(None)
            r2s.append(0.0)
            cis.append(None)
            failed.append(True)
    return DoseResponseResult(
        feature_ids=feature_ids,
        ec50=ec50s,
        hill_slope=hills,
        r_squared=r2s,
        ec50_ci95=cis,
        fit_failed=failed,
    )
