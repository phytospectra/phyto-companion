"""STOCSY: statistical total correlation spectroscopy (ADR-0071).

1D driver-peak STOCSY: the driver bucket's cross-sample intensity
vector is correlated (Pearson) against every other bucket.  2D
all-pairs STOCSY: every qualifying bucket pair.  Statistics per pair:
Pearson r, t = r*sqrt((n-2)/(1-r^2)) with n-2 df, two-tailed p,
Benjamini-Hochberg FDR (scipy false_discovery_control, the same engine
as the rest of the service).

Reporting thresholds |r| >= 0.7 AND FDR <= 0.05 (both configurable);
sub-threshold pairs are not emitted as evidence.  Minimum three
spectra; fewer is a named skip.

STANDING WARNING, carried by every emitted record's evidence text:
a STOCSY correlation is a HYPOTHESIS requiring chemical confirmation
(spiking, isolation, 2D NMR) -- it is not an assignment.  The all-pairs
mode is O(buckets^2) in emitted candidates before thresholding; with
the default 0.04 ppm bucketing over a 12 ppm window that is ~45,000
tests, which the BH correction accounts for.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

from phyto_shared.metabolomics import STOCSYCorrelation

__all__ = [
    "SkipSTOCSY",
    "STOCSYResult",
    "driver_stocsy",
    "all_pairs_stocsy",
    "STOCSY_HYPOTHESIS_WARNING",
    "MIN_SPECTRA",
    "DEFAULT_R_THRESHOLD",
    "DEFAULT_FDR_THRESHOLD",
]

MIN_SPECTRA = 3
DEFAULT_R_THRESHOLD = 0.7
DEFAULT_FDR_THRESHOLD = 0.05
STOCSY_HYPOTHESIS_WARNING = (
    "STOCSY correlation is a hypothesis requiring chemical confirmation "
    "(spiking, isolation, 2D NMR), not an assignment"
)


class SkipSTOCSY(Exception):
    """Named skip: STOCSY impossible with the data given, with why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class STOCSYResult:
    """Correlations that passed the thresholds, plus the full pair count."""

    def __init__(
        self,
        correlations: list[STOCSYCorrelation],
        mode: str,
        n_pairs_tested: int,
        warning: str = STOCSY_HYPOTHESIS_WARNING,
    ) -> None:
        self.correlations = correlations
        self.mode = mode
        self.n_pairs_tested = n_pairs_tested
        self.warning = warning


def _bucket_index(ppm: np.ndarray, target_ppm: float) -> int:
    return int(np.argmin(np.abs(np.asarray(ppm, dtype=float) - target_ppm)))


def _p_values_for_r(r: np.ndarray, n: int) -> np.ndarray:
    """Two-tailed p from Pearson r via the t statistic (n >= 3)."""
    clipped = np.clip(np.abs(r), 0.0, 1.0 - 1e-15)
    t = clipped * np.sqrt((n - 2) / (1.0 - clipped**2))
    return 2.0 * stats.t.sf(t, df=n - 2)


def _constant_columns(matrix: np.ndarray) -> np.ndarray:
    return np.ptp(matrix, axis=0) == 0.0


def driver_stocsy(
    matrix: list[list[float]] | np.ndarray,
    ppm: list[float] | np.ndarray,
    driver_ppm: float,
    r_threshold: float = DEFAULT_R_THRESHOLD,
    fdr_threshold: float = DEFAULT_FDR_THRESHOLD,
) -> STOCSYResult:
    """1D driver-peak STOCSY: driver bucket vs every other bucket."""
    data = np.asarray(matrix, dtype=float)
    shifts = np.asarray(ppm, dtype=float)
    if data.ndim != 2 or data.shape[0] < MIN_SPECTRA:
        raise SkipSTOCSY(
            f"STOCSY needs at least {MIN_SPECTRA} spectra; got {data.shape[0] if data.ndim == 2 else 'non-2D input'}"
        )
    if data.shape[1] != shifts.size:
        raise SkipSTOCSY(
            f"matrix has {data.shape[1]} buckets but the ppm axis has {shifts.size} entries"
        )
    driver_col = data[:, _bucket_index(shifts, driver_ppm)]
    if float(np.ptp(driver_col)) == 0.0:
        raise SkipSTOCSY(
            f"driver peak at {driver_ppm} ppm has zero variance across "
            "spectra; nothing can correlate with a constant"
        )
    usable = ~_constant_columns(data)
    usable[_bucket_index(shifts, driver_ppm)] = False

    candidate_cols = np.where(usable)[0]
    if candidate_cols.size == 0:
        raise SkipSTOCSY("no non-constant buckets to correlate against")

    rs = np.array(
        [float(np.corrcoef(driver_col, data[:, j])[0, 1]) for j in candidate_cols]
    )
    p_values = _p_values_for_r(rs, data.shape[0])
    fdr = stats.false_discovery_control(p_values, method="bh")

    correlations: list[STOCSYCorrelation] = []
    for position, j in enumerate(candidate_cols):
        if abs(rs[position]) >= r_threshold and fdr[position] <= fdr_threshold:
            correlations.append(
                STOCSYCorrelation(
                    mode="driver_1d",
                    driver_ppm=float(shifts[_bucket_index(shifts, driver_ppm)]),
                    bucket_ppm=float(shifts[j]),
                    pearson_r=round(float(rs[position]), 4),
                    p_value=float(p_values[position]),
                    fdr=round(float(fdr[position]), 6),
                    passes_threshold=True,
                )
            )
    return STOCSYResult(
        correlations, mode="driver_1d", n_pairs_tested=int(candidate_cols.size)
    )


def all_pairs_stocsy(
    matrix: list[list[float]] | np.ndarray,
    ppm: list[float] | np.ndarray,
    r_threshold: float = DEFAULT_R_THRESHOLD,
    fdr_threshold: float = DEFAULT_FDR_THRESHOLD,
) -> STOCSYResult:
    """2D all-pairs STOCSY: every non-constant bucket pair (vectorised)."""
    data = np.asarray(matrix, dtype=float)
    shifts = np.asarray(ppm, dtype=float)
    if data.ndim != 2 or data.shape[0] < MIN_SPECTRA:
        raise SkipSTOCSY(
            f"STOCSY needs at least {MIN_SPECTRA} spectra; got {data.shape[0] if data.ndim == 2 else 'non-2D input'}"
        )
    if data.shape[1] != shifts.size:
        raise SkipSTOCSY(
            f"matrix has {data.shape[1]} buckets but the ppm axis has {shifts.size} entries"
        )
    usable = np.where(~_constant_columns(data))[0]
    if usable.size < 2:
        raise SkipSTOCSY("fewer than two non-constant buckets; no pairs to test")

    subset = data[:, usable]
    correlation_matrix = np.corrcoef(subset, rowvar=False)
    iu = np.triu_indices(usable.size, k=1)
    rs = correlation_matrix[iu]
    p_values = _p_values_for_r(rs, data.shape[0])
    fdr = stats.false_discovery_control(p_values, method="bh")

    correlations: list[STOCSYCorrelation] = []
    for position in np.where(
        (np.abs(rs) >= r_threshold) & (fdr <= fdr_threshold)
    )[0]:
        i, j = int(iu[0][position]), int(iu[1][position])
        correlations.append(
            STOCSYCorrelation(
                mode="all_pairs_2d",
                driver_ppm=float(shifts[usable[i]]),
                bucket_ppm=float(shifts[usable[j]]),
                pearson_r=round(float(rs[position]), 4),
                p_value=float(p_values[position]),
                fdr=round(float(fdr[position]), 6),
                passes_threshold=True,
            )
        )
    return STOCSYResult(
        correlations, mode="all_pairs_2d", n_pairs_tested=int(rs.size)
    )
