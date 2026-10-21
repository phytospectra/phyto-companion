"""Statistics: PCA, PLS-DA, random forest, univariate tests, FDR.

Everything uses scikit-learn or scipy -- no custom implementations.
OPLS-DA is deliberately NOT implemented (patented by Sartorius/Stegemann,
commercial license required); PLS-DA is sufficient (ADR-0015).
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats
from sklearn.cross_decomposition import PLSRegression
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import cross_val_predict
from sklearn.preprocessing import LabelEncoder

from phyto_shared.exceptions import InvalidInputError

from .feature_detection import FeatureTable, table_to_matrix

__all__ = [
    "PCAResult",
    "PLSDAResult",
    "RFResult",
    "UnivariateResult",
    "FeatureRank",
    "pca",
    "pls_da",
    "random_forest_classifier",
    "univariate_test",
    "fdr_correction",
    "biomarker_candidates",
    "vip_scores",
]

OPLS_DA_NOT_IMPLEMENTED = (
    "OPLS-DA is intentionally not implemented: the algorithm is patented "
    "(Trygg & Wold, licensed by Sartorius) and requires a commercial "
    "license. PLS-DA is provided instead (ADR-0015)."
)


class PCAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_components: int
    explained_variance_ratio: list[float]
    cumulative_explained_variance: float
    scores: list[list[float]]  # samples x components
    loadings: list[list[float]]  # features x components
    sample_ids: list[str]
    feature_ids: list[str]


class PLSDAResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_components: int
    r2y: float
    q2: float
    permutation_p_value: float
    n_permutations: int
    vip: list[float]
    scores: list[list[float]]
    feature_ids: list[str]
    sample_ids: list[str]
    labels_encoded: list[int]


class RFResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oob_error: float
    roc_auc: float
    feature_importances: list[float]
    feature_ids: list[str]


class UnivariateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["welch", "student", "mann_whitney"]
    feature_ids: list[str]
    statistic: list[float]
    p_value: list[float]
    fold_change: list[float]
    log2_fold_change: list[float]
    cohens_d: list[float]
    group_a: str
    group_b: str


class FeatureRank(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str
    vip: float
    fdr_p_value: float
    criteria: Literal["intersection", "union"]


def _matrix_and_labels(
    table: FeatureTable, labels: dict[str, str]
) -> tuple[list[str], list[str], np.ndarray, np.ndarray, list[str]]:
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    missing = [s for s in sample_ids if s not in labels]
    if missing:
        raise InvalidInputError(
            "labels missing for samples", context={"missing": missing}
        )
    y = np.array([labels[s] for s in sample_ids])
    return sample_ids, feature_ids, matrix, y, sorted(set(y.tolist()))


def pca(table: FeatureTable, n_components: int = 5) -> PCAResult:
    """Unsupervised PCA: needs no labels."""
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    n_components = max(1, min(n_components, min(matrix.shape)))
    model = PCA(n_components=n_components)
    scores = model.fit_transform(matrix)
    return PCAResult(
        n_components=n_components,
        explained_variance_ratio=[float(v) for v in model.explained_variance_ratio_],
        cumulative_explained_variance=float(
            np.sum(model.explained_variance_ratio_)
        ),
        scores=[[float(v) for v in row] for row in scores],
        loadings=[[float(v) for v in row] for row in model.components_.T],
        sample_ids=sample_ids,
        feature_ids=feature_ids,
    )


def vip_scores(pls: PLSRegression) -> np.ndarray:
    """Standard VIP scores from a fitted PLSRegression.

    s_a = (t_a' t_a) * (q_a' q_a) per component; VIP_j =
    sqrt(p * sum_a(w_ja^2 * s_a) / sum_a s_a).
    """
    t = np.asarray(pls.x_scores_, dtype=float)
    w = np.asarray(pls.x_weights_, dtype=float)
    q = np.asarray(pls.y_loadings_, dtype=float).reshape(t.shape[1], -1)
    p = w.shape[0]
    # component weight: variance explained in X times in Y
    s = np.array(
        [float(t[:, a] @ t[:, a]) * float(q[a] @ q[a]) for a in range(t.shape[1])]
    )
    total = float(s.sum())
    if total <= 0:
        return np.zeros(p)
    vips = np.sqrt(p * (w**2 @ s) / total)
    return vips


def _r2y_q2(matrix: np.ndarray, y_encoded: np.ndarray, n_components: int) -> tuple[float, float]:
    pls = PLSRegression(n_components=n_components)
    pls.fit(matrix, y_encoded)
    r2y = float(pls.score(matrix, y_encoded))
    try:
        predicted = cross_val_predict(pls, matrix, y_encoded, cv=3)
        q2 = float(1.0 - np.sum((y_encoded - predicted) ** 2) / np.sum(
            (y_encoded - np.mean(y_encoded)) ** 2
        ))
    except Exception:
        q2 = float("nan")
    return r2y, q2


def pls_da(
    table: FeatureTable,
    labels: dict[str, str],
    n_components: int = 3,
    n_permutations: int = 100,
) -> PLSDAResult:
    """PLS-DA with Q2, R2Y, permutation p-value, and VIP scores.

    OPLS-DA is NOT offered here -- see ``OPLS_DA_NOT_IMPLEMENTED``.
    """
    sample_ids, feature_ids, matrix, y_raw, classes = _matrix_and_labels(table, labels)
    if len(classes) != 2:
        raise InvalidInputError(
            "pls_da expects exactly two classes", context={"classes": classes}
        )
    encoder = LabelEncoder().fit(y_raw)
    y = encoder.transform(y_raw).astype(float)
    n_components = max(1, min(n_components, min(matrix.shape) - 1))

    r2y, q2 = _r2y_q2(matrix, y, n_components)

    observed_r2 = r2y
    exceed = 0
    rng = np.random.default_rng(42)
    for _ in range(n_permutations):
        shuffled = rng.permutation(y)
        permuted_r2, _ = _r2y_q2(matrix, shuffled, n_components)
        if permuted_r2 >= observed_r2:
            exceed += 1
    p_value = (1 + exceed) / (1 + n_permutations)

    pls = PLSRegression(n_components=n_components)
    pls.fit(matrix, y)
    vips = vip_scores(pls)

    return PLSDAResult(
        n_components=n_components,
        r2y=round(r2y, 4),
        q2=round(q2, 4) if not np.isnan(q2) else -1.0,
        permutation_p_value=round(p_value, 4),
        n_permutations=n_permutations,
        vip=[float(v) for v in vips],
        scores=[[float(v) for v in row] for row in pls.x_scores_[:, :n_components]],
        feature_ids=feature_ids,
        sample_ids=sample_ids,
        labels_encoded=[int(v) for v in y],
    )


def random_forest_classifier(
    table: FeatureTable, labels: dict[str, str], n_estimators: int = 500
) -> RFResult:
    sample_ids, feature_ids, matrix, y, classes = _matrix_and_labels(table, labels)
    if len(classes) != 2:
        raise InvalidInputError(
            "random_forest_classifier expects exactly two classes",
            context={"classes": classes},
        )
    forest = RandomForestClassifier(
        n_estimators=n_estimators, oob_score=True, random_state=42
    )
    forest.fit(matrix, y)
    proba = forest.oob_decision_function_
    positive_index = list(forest.classes_).index(classes[1])
    auc = roc_auc_score(y, proba[:, positive_index])
    return RFResult(
        oob_error=round(1.0 - forest.oob_score_, 4),
        roc_auc=round(float(auc), 4),
        feature_importances=[float(v) for v in forest.feature_importances_],
        feature_ids=feature_ids,
    )


def univariate_test(
    table: FeatureTable,
    labels: dict[str, str],
    method: Literal["welch", "student", "mann_whitney"] = "welch",
) -> UnivariateResult:
    """Per-feature two-group comparison with fold change and Cohen's d."""
    sample_ids, feature_ids, matrix, y, classes = _matrix_and_labels(table, labels)
    if len(classes) != 2:
        raise InvalidInputError(
            "univariate_test expects exactly two classes",
            context={"classes": classes},
        )
    mask_a = y == classes[0]
    mask_b = y == classes[1]

    statistics: list[float] = []
    p_values: list[float] = []
    fold_changes: list[float] = []
    log2_fcs: list[float] = []
    cohens: list[float] = []
    for j in range(matrix.shape[1]):
        a = matrix[mask_a, j].astype(float)
        b = matrix[mask_b, j].astype(float)
        if method in ("welch", "student"):
            result = stats.ttest_ind(
                a, b, equal_var=(method == "student")
            )
            statistic = float(result.statistic)
            p_value = float(result.pvalue)
        else:
            result = stats.mannwhitneyu(a, b, alternative="two-sided")
            statistic = float(result.statistic)
            p_value = float(result.pvalue)
        # zero-variance features yield NaN: report "no evidence" (p=1),
        # never poison the FDR pool with a non-finite value
        statistics.append(0.0 if not np.isfinite(statistic) else statistic)
        p_values.append(1.0 if not np.isfinite(p_value) else p_value)
        mean_a = float(np.mean(a)) or 1e-12
        mean_b = float(np.mean(b)) or 1e-12
        fold_changes.append(mean_a / mean_b)
        log2_fcs.append(float(np.log2(mean_a / mean_b)))
        pooled_sd = float(np.sqrt(
            ((len(a) - 1) * np.var(a, ddof=1) + (len(b) - 1) * np.var(b, ddof=1))
            / max(len(a) + len(b) - 2, 1)
        ))
        difference = mean_a - mean_b
        if pooled_sd > 0:
            cohens.append(float(difference / pooled_sd))
        else:
            # both groups are constant: a real difference is an infinite
            # effect; an identical constant is no effect at all
            cohens.append(float(np.sign(difference)) * 1e6 if difference else 0.0)

    return UnivariateResult(
        method=method,
        feature_ids=feature_ids,
        statistic=statistics,
        p_value=p_values,
        fold_change=fold_changes,
        log2_fold_change=log2_fcs,
        cohens_d=cohens,
        group_a=classes[0],
        group_b=classes[1],
    )


def fdr_correction(
    p_values: list[float] | np.ndarray,
    method: Literal["bh", "by"] = "bh",
) -> np.ndarray:
    """Benjamini-Hochberg / Benjamini-Yekutieli adjusted p-values."""
    from scipy.stats import false_discovery_control

    array = np.asarray(p_values, dtype=float)
    if array.size == 0:
        return array
    if not np.all(np.isfinite(array)):
        raise InvalidInputError(
            "p-values must be finite (NaN arises from zero-variance features)",
            context={"n_non_finite": int(np.sum(~np.isfinite(array)))},
        )
    if np.any((array < 0.0) | (array > 1.0)):
        raise InvalidInputError(
            "p-values must lie within [0, 1]",
            context={"min": float(np.min(array)), "max": float(np.max(array))},
        )
    return false_discovery_control(array, method=method)


def biomarker_candidates(
    results: UnivariateResult,
    pls: PLSDAResult,
    vip_threshold: float = 1.0,
    fdr_threshold: float = 0.05,
) -> dict[str, list[FeatureRank]]:
    """VIP + FDR-significant features; intersection and union reported
    separately, never merged."""
    adjusted = fdr_correction(results.p_value)
    vip_by_id = dict(zip(pls.feature_ids, pls.vip))
    ranks: list[FeatureRank] = []
    for index, feature_id in enumerate(results.feature_ids):
        vip = float(vip_by_id.get(feature_id, 0.0))
        ranks.append(
            FeatureRank(
                feature_id=feature_id,
                vip=round(vip, 4),
                fdr_p_value=float(adjusted[index]),
                criteria="union",
            )
        )
    intersection = [
        rank for rank in ranks
        if rank.vip >= vip_threshold and rank.fdr_p_value < fdr_threshold
    ]
    union = [
        rank for rank in ranks
        if rank.vip >= vip_threshold or rank.fdr_p_value < fdr_threshold
    ]
    return {
        "intersection": [
            r.model_copy(update={"criteria": "intersection"}) for r in intersection
        ],
        "union": union,
    }
