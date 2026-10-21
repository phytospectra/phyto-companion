"""Normalization and QC correction.

Every method is named, documented here, and appended to the table's
``provenance_notes`` so it lands in the provenance manifest.
"""

from __future__ import annotations

import numpy as np

from phyto_shared.exceptions import InvalidInputError

from .feature_detection import (
    FeatureTable,
    matrix_to_table,
    table_to_matrix,
)

__all__ = [
    "normalize",
    "qc_correction",
    "flag_low_quality",
    "rsd",
]

NORMALIZATION_METHODS = ("tic", "pqn", "is", "qnmr")


def rsd(values: np.ndarray) -> float:
    """Relative standard deviation (CV %/100); 0.0 for empty/flat data."""
    values = np.asarray(values, dtype=float)
    if values.size == 0 or float(np.mean(values)) == 0.0:
        return 0.0
    return float(np.std(values, ddof=1) / np.mean(values)) if values.size > 1 else 0.0


def normalize(table: FeatureTable, method: str, params: dict | None = None) -> FeatureTable:
    """Normalize sample intensities.

    Methods:
      * ``tic``  -- each sample divided by its total ion current, rescaled
                    to the mean TIC.
      * ``pqn``  -- probabilistic quotient normalization (Dieterle 2006):
                    per-feature median quotient, then each sample scaled
                    by the median of its quotients.
      * ``is``   -- internal standard: samples scaled by
                    ``params['internal_standard_feature_id']`` intensity.
      * ``qnmr`` -- qNMR external calibration: multiply by
                    ``params['calibration_factor']`` (documented as a
                    scale placeholder pending the real qNMR service).
    """
    params = params or {}
    if method not in NORMALIZATION_METHODS:
        raise InvalidInputError(
            "unknown normalization method",
            context={"method": method, "known": list(NORMALIZATION_METHODS)},
        )
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    note = ""

    if method == "tic":
        factors = matrix.sum(axis=1)
        mean_tic = float(np.mean(factors))
        if np.any(factors == 0):
            raise InvalidInputError("a sample has zero total ion current", context={})
        matrix = matrix / factors[:, None] * mean_tic
        note = f"tic normalization (mean TIC {mean_tic:.1f})"

    elif method == "pqn":
        feature_medians = np.median(matrix, axis=0)
        feature_medians[feature_medians == 0] = 1.0
        quotients = matrix / feature_medians[None, :]
        factors = np.median(quotients, axis=1)
        factors[factors == 0] = 1.0
        matrix = matrix / factors[:, None]
        note = "pqn normalization (Dieterle et al. 2006)"

    elif method == "is":
        standard = params.get("internal_standard_feature_id")
        if standard not in feature_ids:
            raise InvalidInputError(
                "internal standard feature not in table",
                context={"internal_standard_feature_id": standard},
            )
        column = feature_ids.index(standard)
        factors = matrix[:, column]
        factors[factors == 0] = 1.0
        matrix = matrix / factors[:, None] * float(np.mean(factors))
        note = f"internal-standard normalization against {standard}"

    elif method == "qnmr":
        factor = float(params.get("calibration_factor", 1.0))
        matrix = matrix * factor
        note = f"qnmr external calibration x{factor}"

    normalized = matrix_to_table(table, sample_ids, feature_ids, matrix)
    normalized.provenance_notes.append(note)
    return normalized


def qc_correction(
    table: FeatureTable, qc_samples: list[str]
) -> FeatureTable:
    """Linear QC-RLSC-style drift correction.

    For each feature, a linear drift curve is fit across the QC samples
    (by injection order) and every sample is corrected back to the first
    QC level.  Reports per-feature QC RSD before and after in the QC
    flags.
    """
    if not qc_samples:
        raise InvalidInputError("no QC samples given", context={})
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    qc_positions = [sample_ids.index(q) for q in qc_samples if q in sample_ids]
    if not qc_positions:
        raise InvalidInputError(
            "none of the QC samples are in the table", context={"qc_samples": qc_samples}
        )
    order = [s.injection_order for s in table.samples]

    corrected = matrix.copy()
    rsd_before: list[float] = []
    rsd_after: list[float] = []
    for j in range(matrix.shape[1]):
        qc_x = np.array([order[i] for i in qc_positions], dtype=float)
        qc_y = matrix[qc_positions, j]
        if len(qc_x) >= 2 and np.ptp(qc_x) > 0:
            slope, intercept = np.polyfit(qc_x, qc_y, 1)
            predicted = slope * np.asarray(order, dtype=float) + intercept
            reference_level = slope * qc_x[0] + intercept
            safe = np.where(np.abs(predicted) < 1e-9, 1e-9, predicted)
            corrected[:, j] = matrix[:, j] * (reference_level / safe)
        rsd_before.append(rsd(matrix[qc_positions, j]))
        rsd_after.append(rsd(corrected[qc_positions, j]))

    result = matrix_to_table(table, sample_ids, feature_ids, corrected)
    improved = sum(1 for b, a in zip(rsd_before, rsd_after) if a < b)
    result.qc_flags.append(
        f"qc_correction: linear drift correction on {len(qc_samples)} QC "
        f"samples; QC RSD improved for {improved}/{len(feature_ids)} features"
    )
    result.provenance_notes.append("qc_correction (linear QC-RLSC style)")
    return result


def flag_low_quality(
    table: FeatureTable, rsd_threshold: float = 0.30
) -> list[str]:
    """Feature ids whose QC-sample RSD exceeds the threshold.

    Downstream analyses EXCLUDE these by default; the caller must carry
    the flag, not silently drop the feature.
    """
    qc_ids = [sample.sample_id for sample in table.samples if sample.is_qc]
    flagged: list[str] = []
    if not qc_ids:
        return flagged
    for feature in table.features:
        values = [feature.intensities[q] for q in qc_ids if q in feature.intensities]
        if values and rsd(np.asarray(values)) > rsd_threshold:
            flagged.append(feature.feature_id)
    return flagged
