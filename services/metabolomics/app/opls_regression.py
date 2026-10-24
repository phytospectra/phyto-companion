"""O-PLS regression for continuous Y (ADR-0070).

NIPALS O-PLS after Trygg & Wold 2002: one predictive PLS1 component
for the continuous covariate, then systematic Y-ORTHOGONAL components
extracted from the residual and deflated (they carry structure in X
that is unrelated to Y).  Predictive and orthogonal scores and
loadings are returned separately so a report can show the "degradation
axis" apart from the structured noise.

Scope note (ADR-0070, deliberately explicit): this module implements
O-PLS REGRESSION on a continuous covariate.  OPLS-DA -- the
discriminant analysis shipped by Sartorius's commercial software --
remains deliberately absent (ADR-0015); categorical Y keeps PLS-DA.
Continuous Y never silently falls back to PLS-DA here.

Validation (mirrors the Curcuma reference pipeline, metabom8 opls
defaults): Q2 by balanced Monte Carlo cross-validation (k=15
repetitions, 2/3 train split, fixed seed) and a permutation test with
n=100 default over R2Y.  A model failing its permutation threshold or
Q2 < 0 is reported as validation-failed evidence by the layer chain --
never quietly passed.  Fewer than 5 samples, or a constant covariate,
is a named skip, not an attempt.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.metabolomics import PLSModelEvidence

from .feature_detection import FeatureTable
from .feature_detection import table_to_matrix

__all__ = [
    "OPLSResult",
    "SkipOPLS",
    "opls_regression",
    "balanced_mc_q2",
]

MIN_SAMPLES = 5
CV_REPETITIONS = 15
CV_TRAIN_FRACTION = 2.0 / 3.0
DEFAULT_N_PERMUTATIONS = 100
PERMUTATION_SEED = 42


class SkipOPLS(Exception):
    """Named skip: regression impossible with the data given, with why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class OPLSResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: PLSModelEvidence
    predictive_scores: list[float]
    orthogonal_scores: list[list[float]]
    predictive_loadings: list[float]
    orthogonal_loadings: list[list[float]]
    predictive_weights: list[float]
    coefficients: list[float]
    feature_ids: list[str]
    sample_ids: list[str]


def _fit_opls(x: np.ndarray, y: np.ndarray, n_orthogonal: int) -> dict:
    """Core NIPALS O-PLS fit on ALREADY-CENTRED x and y."""
    x_resid = x.copy()
    # -- predictive component (PLS1, one component) ------------------------
    w1 = x_resid.T @ y
    norm = float(np.linalg.norm(w1))
    if norm <= 0:
        raise SkipOPLS("predictive weight is zero: X has no covariance with Y")
    w1 = w1 / norm
    t1 = x_resid @ w1
    p1 = (x_resid.T @ t1) / float(t1 @ t1)
    c1 = float((y @ t1) / (t1 @ t1))
    x_resid = x_resid - np.outer(t1, p1)
    y_resid = y - c1 * t1

    # -- orthogonal components (Trygg & Wold 2002) -------------------------
    t_ortho: list[np.ndarray] = []
    p_ortho: list[np.ndarray] = []
    for _ in range(n_orthogonal):
        w_next = x_resid.T @ y_resid
        norm_next = float(np.linalg.norm(w_next))
        if norm_next <= 1e-12:
            break
        w_next = w_next / norm_next
        wo = w_next - w1 * float((w1 @ w_next))
        wo_norm = float(np.linalg.norm(wo))
        if wo_norm <= 1e-12:
            break
        wo = wo / wo_norm
        to = x_resid @ wo
        po = (x_resid.T @ to) / float(to @ to)
        x_resid = x_resid - np.outer(to, po)
        t_ortho.append(to)
        p_ortho.append(po)

    coefficients = w1 * c1  # single-predictive-component regression vector
    return {
        "w1": w1,
        "t1": t1,
        "p1": p1,
        "c1": c1,
        "coefficients": coefficients,
        "t_ortho": t_ortho,
        "p_ortho": p_ortho,
    }


def _r2y(x: np.ndarray, y: np.ndarray, n_orthogonal: int) -> float:
    fitted = _fit_opls(x, y, n_orthogonal)
    predicted = x @ fitted["coefficients"]
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum(y**2))  # y is centred
    if ss_tot <= 0:
        raise SkipOPLS("covariate is constant (zero variance); no regression is possible")
    return 1.0 - ss_res / ss_tot


def balanced_mc_q2(
    x: np.ndarray, y: np.ndarray, n_orthogonal: int = 1,
    repetitions: int = CV_REPETITIONS, train_fraction: float = CV_TRAIN_FRACTION,
    seed: int = PERMUTATION_SEED,
) -> float:
    """Q2 by balanced Monte Carlo cross-validation (k repetitions, 2/3
    train): PRESS accumulated over held-out thirds, normalised by total
    sum of squares.  Deterministic seed."""
    n = y.size
    rng = np.random.default_rng(seed)
    press = 0.0
    covered = np.zeros(n, dtype=int)
    for _ in range(repetitions):
        order = rng.permutation(n)
        n_train = max(2, int(round(n * train_fraction)))
        train_idx, test_idx = order[:n_train], order[n_train:]
        if test_idx.size == 0:
            continue
        x_train, y_train = x[train_idx], y[train_idx]
        x_test, y_test = x[test_idx], y[test_idx]
        x_mean, y_mean = x_train.mean(axis=0), y_train.mean()
        try:
            fitted = _fit_opls(
                x_train - x_mean, y_train - y_mean, n_orthogonal
            )
        except SkipOPLS:
            continue
        predicted = (x_test - x_mean) @ fitted["coefficients"] + y_mean
        press += float(np.sum((y_test - predicted) ** 2))
        covered[test_idx] += 1
    if not np.any(covered):
        return float("nan")
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        raise SkipOPLS("covariate is constant (zero variance); no regression is possible")
    return 1.0 - press / (ss_tot * repetitions)


def opls_regression(
    table: FeatureTable,
    y_values: dict[str, float],
    n_orthogonal: int = 1,
    n_permutations: int = DEFAULT_N_PERMUTATIONS,
) -> OPLSResult:
    """O-PLS regression of the feature matrix on a continuous covariate.

    ``y_values`` maps sample_id -> the continuous covariate (e.g.
    storage_day).  Raises SkipOPLS with a named reason when the design
    cannot support a regression.
    """
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    missing = [s for s in sample_ids if s not in y_values]
    if missing:
        raise InvalidInputError(
            "covariate missing for samples", context={"missing": missing}
        )
    y = np.array([float(y_values[s]) for s in sample_ids])
    if y.size < MIN_SAMPLES:
        raise SkipOPLS(
            f"fewer than {MIN_SAMPLES} samples ({y.size}); O-PLS regression "
            "and its permutation test would be meaningless"
        )
    if float(np.ptp(y)) == 0.0:
        raise SkipOPLS("covariate is constant across samples; no regression is possible")

    x = matrix - matrix.mean(axis=0)
    y_centred = y - y.mean()

    fitted = _fit_opls(x, y_centred, n_orthogonal)
    r2y = _r2y(x, y_centred, n_orthogonal)
    q2 = balanced_mc_q2(x, y_centred, n_orthogonal)

    rng = np.random.default_rng(PERMUTATION_SEED)
    exceed = 0
    for _ in range(n_permutations):
        shuffled = rng.permutation(y_centred)
        try:
            permuted_r2y = _r2y(x, shuffled, n_orthogonal)
        except SkipOPLS:
            continue
        if permuted_r2y >= r2y:
            exceed += 1
    p_value = (1 + exceed) / (1 + n_permutations)

    evidence = PLSModelEvidence(
        model_kind="opls_regression",
        n_components=1,
        n_orthogonal_components=len(fitted["t_ortho"]),
        r2y=round(r2y, 4),
        q2=round(q2, 4) if np.isfinite(q2) else -1.0,
        permutation_p_value=round(p_value, 4),
        n_permutations=n_permutations,
        continuous_y_summary=(
            f"continuous covariate: n={y.size}, range "
            f"{y.min():.4g}-{y.max():.4g}, sd {float(np.std(y, ddof=1)):.4g}"
        ),
        class_balance=None,
        cv_scheme=(
            f"balanced Monte Carlo, k={CV_REPETITIONS}, "
            f"train fraction {CV_TRAIN_FRACTION:.2f}"
        ),
    )
    return OPLSResult(
        evidence=evidence,
        predictive_scores=[float(v) for v in fitted["t1"]],
        orthogonal_scores=[
            [float(v) for v in component] for component in fitted["t_ortho"]
        ],
        predictive_loadings=[float(v) for v in fitted["p1"]],
        orthogonal_loadings=[
            [float(v) for v in component] for component in fitted["p_ortho"]
        ],
        predictive_weights=[float(v) for v in fitted["w1"]],
        coefficients=[float(v) for v in fitted["coefficients"]],
        feature_ids=feature_ids,
        sample_ids=sample_ids,
    )
