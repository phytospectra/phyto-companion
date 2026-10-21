"""Bioactivity-based molecular networking (Nothias et al. 2018).

Nothias, et al. "Bioactivity-Based Molecular Networking for the Discovery
of Drug Leads in Natural Product Extracts", J. Nat. Prod. 81:758-767:
each MS2 node's feature abundance across fractions is correlated
(Pearson) with the fractions' measured bioactivity; features whose
abundance tracks activity are prioritized as the likely bioactive
principles.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from scipy import stats

from phyto_shared.exceptions import InvalidInputError

from .gnps_client import MNResults
from .gnps_result_parser import ParsedNetwork, parse_graphml

__all__ = [
    "BioactivityNetwork",
    "bioactivity_networking",
    "correlate_network",
    "prioritize_bioactive_nodes",
    "BIOACTIVITY_MIN_ABS_R",
    "BIOACTIVITY_MAX_P",
]

BIOACTIVITY_MIN_ABS_R = 0.5
"""Minimum |Pearson r| for a node to count as potentially bioactive
(Nothias et al. 2018 use |r| > 0.5 by default)."""

BIOACTIVITY_MAX_P = 0.05
"""Maximum p-value (t-distribution) for the correlation."""


class BioactivityNetwork(BaseModel):
    """An MS2 network enriched with per-node bioactivity correlations."""

    model_config = ConfigDict(extra="forbid")

    network: ParsedNetwork
    bioactivity_scores: dict[str, float] = Field(
        default_factory=dict, description="node_id -> Pearson r in [-1, 1]"
    )
    p_values: dict[str, float] = Field(default_factory=dict)
    bioactivity_metadata: dict[str, object] = Field(default_factory=dict)


def _pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Pearson r and two-sided p-value; constant series -> (0.0, 1.0)."""
    if len(x) < 3:
        raise InvalidInputError(
            "Need at least 3 fractions for a meaningful Pearson correlation",
            context={"n": len(x)},
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # ConstantInputWarning -> handled below
        result = stats.pearsonr(x, y)
    r, p = float(result.statistic), float(result.pvalue)
    if np.isnan(r) or np.isnan(p):
        return 0.0, 1.0
    return r, p


def bioactivity_networking(
    mn_results: MNResults,
    feature_abundances: pd.DataFrame,
    bioactivities: pd.Series,
) -> BioactivityNetwork:
    """Correlate every node's abundance profile with measured bioactivity.

    ``feature_abundances``: fractions x features (columns are GNPS feature
    ids).  ``bioactivities``: fraction index -> activity value.
    """
    network = parse_graphml(mn_results.graphml)
    return correlate_network(network, feature_abundances, bioactivities)


def correlate_network(
    network: ParsedNetwork,
    feature_abundances: pd.DataFrame,
    bioactivities: pd.Series,
) -> BioactivityNetwork:
    """The correlation core, operating on an already-parsed network."""
    if feature_abundances.empty:
        raise InvalidInputError("feature_abundances is empty", context={})
    if bioactivities.isna().any():
        raise InvalidInputError(
            "bioactivities contains NaN entries",
            context={"n_nan": int(bioactivities.isna().sum())},
        )

    fractions = [f for f in bioactivities.index if f in feature_abundances.index]
    if len(fractions) < 3:
        raise InvalidInputError(
            "Fewer than 3 fractions overlap between abundances and bioactivities",
            context={"overlap": len(fractions)},
        )
    y = bioactivities.loc[fractions].to_numpy(dtype=float)

    scores: dict[str, float] = {}
    p_values: dict[str, float] = {}
    missing_features: list[str] = []
    for node in network.nodes:
        column = node.feature_id
        if column not in feature_abundances.columns:
            missing_features.append(column)
            continue
        x = feature_abundances.loc[fractions, column].to_numpy(dtype=float)
        r, p = _pearson(x, y)
        scores[node.id] = r
        p_values[node.id] = p

    flagged = prioritize_bioactive_nodes(
        BioactivityNetwork(network=network, bioactivity_scores=scores, p_values=p_values)
    )

    return BioactivityNetwork(
        network=network,
        bioactivity_scores=scores,
        p_values=p_values,
        bioactivity_metadata={
            "assay_name": "unspecified",
            "n_fractions": len(fractions),
            "n_features_with_abundance": len(scores),
            "features_without_abundance": missing_features,
            "flagged_nodes": flagged,
            "method": "Pearson correlation networking (Nothias et al. 2018)",
        },
    )


def prioritize_bioactive_nodes(
    network: BioactivityNetwork,
    min_r: float = BIOACTIVITY_MIN_ABS_R,
    max_p: float = BIOACTIVITY_MAX_P,
) -> list[str]:
    """Node ids with |r| > min_r and p < max_p, sorted by r descending."""
    selected = [
        node_id
        for node_id, r in network.bioactivity_scores.items()
        if abs(r) > min_r and network.p_values.get(node_id, 1.0) < max_p
    ]
    return sorted(selected, key=lambda node_id: -network.bioactivity_scores[node_id])
