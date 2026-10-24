"""VIP feature selection as a first-class output (ADR-0070).

VIP scores generalise over any PLS/O-PLS model this service produces:

* ``PLSDAResult`` / fitted ``PLSRegression`` (statistics.py) -- the
  existing sum-of-squares VIP formula (statistics.vip_scores):
  VIP_j = sqrt(p * sum_a w_ja^2 s_a / sum_a s_a), s_a = (t_a't_a)(q_a'q_a).
* ``OPLSResult`` (opls_regression.py) -- VIP over the PREDICTIVE
  component(s) only; Y-orthogonal components are excluded from VIP by
  the O-PLS definition (they are structured noise by construction).

Threshold convention: VIP > 1.0 by default (the field convention,
since the average squared VIP equals 1 when all features contribute
equally).  KNOWN LIMITATION, stated rather than hidden: a VIP score is
not a p-value -- it scales with model dimensionality and the number of
features, and a VIP-passing feature still needs univariate or chemical
corroboration.  That corroboration is what the optional FDR/p pairing
records; when no univariate test was supplied, every feature carries
an explicit ``pairing_note`` saying so.
"""

from __future__ import annotations


import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.metabolomics import VIPFeature

from .opls_regression import OPLSResult
from .statistics import PLSDAResult

__all__ = [
    "VIPSelection",
    "vip_from_pls_da",
    "vip_from_opls",
    "select_vip_features",
]

NO_UNIVARIATE_NOTE = "no univariate test supplied; VIP reported unpaired"


class VIPSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: list[VIPFeature]
    threshold: float
    empty_reason: str | None = Field(
        default=None,
        description="Set when no feature passes: states the max VIP seen",
    )


def vip_from_pls_da(pls: PLSDAResult) -> list[float]:
    """VIPs from a PLSDAResult (scores already returned by pls_da)."""
    return list(pls.vip)


def vip_from_opls(opls: OPLSResult) -> list[float]:
    """VIP over the O-PLS predictive component(s) only.

    With a single predictive component and a normalised weight vector,
    the general formula collapses to VIP_j = sqrt(p) * |w_j| -- features
    whose weight magnitude exceeds 1/sqrt(p) pass the 1.0 threshold.
    """
    w = np.abs(np.asarray(opls.predictive_weights, dtype=float))
    p = w.size
    if p == 0:
        return []
    return [float(np.sqrt(p) * value) for value in w]


def select_vip_features(
    vips: list[float],
    feature_ids: list[str],
    threshold: float = 1.0,
    p_values: list[float] | None = None,
    fdr_p_values: list[float] | None = None,
) -> VIPSelection:
    """Attach the threshold verdict and optional univariate pairing.

    ``empty_reason`` is set (and the feature list still returned) when
    nothing passes -- an empty selection is evidence, not silence.
    """
    if len(vips) != len(feature_ids):
        raise ValueError(
            f"vips and feature_ids lengths differ: {len(vips)} vs {len(feature_ids)}"
        )
    paired = p_values is not None and fdr_p_values is not None
    if paired and (len(p_values) != len(feature_ids) or len(fdr_p_values) != len(feature_ids)):
        paired = False

    features: list[VIPFeature] = []
    for index, feature_id in enumerate(feature_ids):
        vip = float(vips[index])
        features.append(
            VIPFeature(
                feature_id=feature_id,
                vip_score=round(vip, 4),
                threshold=threshold,
                passes_threshold=bool(vip > threshold),
                p_value=float(p_values[index]) if paired else None,
                fdr_p_value=float(fdr_p_values[index]) if paired else None,
                pairing_note=(
                    "paired with the univariate test's p/FDR"
                    if paired
                    else NO_UNIVARIATE_NOTE
                ),
            )
        )
    passing = [f for f in features if f.passes_threshold]
    empty_reason = (
        None
        if passing
        else (
            f"no feature exceeds the VIP threshold {threshold} "
            f"(max VIP {max((f.vip_score for f in features), default=0.0):.4f})"
        )
    )
    return VIPSelection(features=features, threshold=threshold, empty_reason=empty_reason)
