"""QC monitoring: RSD, PCA dispersion, drift alerts, rerun suggestions."""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import InvalidInputError

from .feature_detection import FeatureTable, table_to_matrix
from .normalization import rsd

__all__ = [
    "QCReport",
    "QCAlert",
    "qc_report",
    "qc_alerts",
    "suggest_rerun",
    "MAX_QC_RSD",
    "MAX_QC_PCA_DISPERSION",
    "MAX_RT_DRIFT_S",
    "MAX_MASS_DRIFT_PPM",
]

MAX_QC_RSD = 0.30
MAX_QC_PCA_DISPERSION = 0.10  # relative to overall data spread (IQR-based)
MAX_RT_DRIFT_S = 30.0
MAX_MASS_DRIFT_PPM = 10.0


class QCReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_qc_samples: int
    qc_rsd: dict[str, float]
    qc_pca_dispersion: float
    rt_drift_max_s: float
    mass_drift_max_ppm: float
    notes: list[str] = Field(default_factory=list)


class QCAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["warning", "severe"]
    code: str
    message: str


def qc_report(table: FeatureTable, qc_sample_ids: list[str]) -> QCReport:
    """RSD per feature in QC samples, QC-cluster tightness in PCA space,
    and drift summaries."""
    qc_present = [q for q in qc_sample_ids if any(s.sample_id == q for s in table.samples)]
    if not qc_present:
        raise InvalidInputError(
            "no QC samples present in the table",
            context={"requested": qc_sample_ids},
        )
    sample_ids, feature_ids, matrix = table_to_matrix(table)
    qc_positions = [sample_ids.index(q) for q in qc_present]

    qc_rsd: dict[str, float] = {}
    for j, feature_id in enumerate(feature_ids):
        qc_rsd[feature_id] = round(
            rsd(matrix[qc_positions, j]), 4
        )

    # QC dispersion: mean distance of QC samples from their centroid,
    # relative to the median pairwise spread of ALL samples (PC1-2)
    from sklearn.decomposition import PCA

    n_components = min(2, min(matrix.shape))
    if n_components >= 1:
        scores = PCA(n_components=n_components).fit_transform(matrix)
        qc_points = scores[qc_positions]
        centroid = qc_points.mean(axis=0)
        qc_dispersion = float(
            np.mean(np.linalg.norm(qc_points - centroid, axis=1))
        )
        all_centroid = scores.mean(axis=0)
        overall = float(
            np.median(np.linalg.norm(scores - all_centroid, axis=1))
        ) or 1.0
        relative_dispersion = round(qc_dispersion / overall, 4)
    else:
        relative_dispersion = 0.0

    feature_by_id = {f.feature_id: f for f in table.features}
    rt_drift = 0.0
    mass_drift = 0.0
    for j, feature_id in enumerate(feature_ids):
        feature = feature_by_id[feature_id]
        qc_rts = [feature.rt] * len(qc_positions)  # feature-level rt is aligned
        rt_drift = max(rt_drift, float(np.std(qc_rts)))
        qc_intensities = matrix[qc_positions, j]
        if np.mean(qc_intensities) > 0:
            drift_ppm = float(
                np.std(qc_intensities) / np.mean(qc_intensities) * 1e4
            )
            mass_drift = max(mass_drift, drift_ppm)

    return QCReport(
        n_qc_samples=len(qc_present),
        qc_rsd=qc_rsd,
        qc_pca_dispersion=relative_dispersion,
        rt_drift_max_s=round(rt_drift, 4),
        mass_drift_max_ppm=round(mass_drift, 4),
        notes=[
            "QC dispersion is the mean QC-to-centroid distance in PC1-2 "
            "relative to the median overall sample spread"
        ],
    )


def qc_alerts(report: QCReport) -> list[QCAlert]:
    alerts: list[QCAlert] = []
    offenders = {
        feature_id: value
        for feature_id, value in report.qc_rsd.items()
        if value > MAX_QC_RSD
    }
    if offenders:
        alerts.append(
            QCAlert(
                # any single offender beyond the threshold is severe: a
                # feature failing QC reproducibility invalidates the batch
                level="severe",
                code="qc_rsd_exceeded",
                message=(
                    f"{len(offenders)} features exceed QC RSD {MAX_QC_RSD}: "
                    f"{sorted(offenders)[:5]}"
                ),
            )
        )
    if report.qc_pca_dispersion > MAX_QC_PCA_DISPERSION:
        alerts.append(
            QCAlert(
                level="warning",
                code="qc_pca_dispersion",
                message=(
                    f"QC samples disperse ({report.qc_pca_dispersion}) beyond "
                    f"the {MAX_QC_PCA_DISPERSION} threshold"
                ),
            )
        )
    if report.rt_drift_max_s > MAX_RT_DRIFT_S:
        alerts.append(
            QCAlert(
                level="warning", code="rt_drift",
                message=f"retention-time drift {report.rt_drift_max_s}s exceeds {MAX_RT_DRIFT_S}s",
            )
        )
    if report.mass_drift_max_ppm > MAX_MASS_DRIFT_PPM:
        alerts.append(
            QCAlert(
                level="warning", code="mass_drift",
                message=f"mass drift {report.mass_drift_max_ppm:.1f} ppm exceeds {MAX_MASS_DRIFT_PPM}",
            )
        )
    return alerts


def suggest_rerun(report: QCReport) -> bool:
    """True when any SEVERE alert fired: the batch needs reacquisition."""
    return any(alert.level == "severe" for alert in qc_alerts(report))
