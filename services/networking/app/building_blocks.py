"""Building-blocks molecular networking (He et al. 2021).

He, et al. "Building Blocks Molecule Networking Shimmed by In Silico
Fragmentation", Angew. Chem. 123(38):  - diagnostic fragment patterns
(characteristic ion series) reveal which substructures ("building
blocks") a spectrum contains, enabling class-level annotation of unknown
nodes.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BuildingBlock",
    "BUILDING_BLOCK_CATALOGUE",
    "detect_building_block",
    "annotate_building_blocks",
]


class BuildingBlock(BaseModel):
    """A conserved substructure detected by its diagnostic ion pattern."""

    model_config = ConfigDict(extra="forbid")

    name: str
    fragment_mz: list[float] = Field(..., min_length=1)
    smarts_pattern: str = Field(
        ...,
        description="SMARTS indicating the substructure; used by the RDKit-"
        "based confirmation in the elucidation service (Step 5), not here",
    )


# Catalogue of common natural-product building blocks: diagnostic fragment
# m/z (positive mode) + SMARTS for later structural confirmation.  The
# SMARTS patterns are documentation here; they are executed only by
# RDKit in the cloud plane.
BUILDING_BLOCK_CATALOGUE: list[BuildingBlock] = [
    BuildingBlock(
        name="hexose",
        fragment_mz=[145.0495, 163.0601],
        smarts_pattern="[CX4]1([OX2H0,1])[CX4]([OX2H0,1])[CX4]([OX2H0,1])"
        "[CX4]([OX2H0,1])[CX4](CO)[CX4]1",
    ),
    BuildingBlock(
        name="deoxyhexose",
        fragment_mz=[147.0652, 129.0546],
        smarts_pattern="[CX4]1[CX4][CX4]([OX2H0,1])[CX4]([OX2H0,1])[CX4]C1",
    ),
    BuildingBlock(
        name="pentose",
        fragment_mz=[133.0495, 115.0389],
        smarts_pattern="[CX4]1[CX4]([OX2H0,1])[CX4]([OX2H0,1])[CX4]C1",
    ),
    BuildingBlock(
        name="coumaroyl",
        fragment_mz=[147.0441, 163.0390],
        smarts_pattern="c1ccc(cc1)/C=C/C(=O)",
    ),
    BuildingBlock(
        name="malonyl",
        fragment_mz=[87.0082, 129.0183],
        smarts_pattern="C(=O)CC(=O)",
    ),
]


def detect_building_block(
    spectrum_peaks: np.ndarray | list[tuple[float, float]],
    block: BuildingBlock,
    tol_da: float = 0.02,
) -> bool:
    """True when ALL diagnostic ions of ``block`` appear in the spectrum.

    ``spectrum_peaks`` is an (N, 2) array of (mz, intensity).
    """
    peaks = np.asarray(spectrum_peaks, dtype=float).reshape(-1, 2)
    if peaks.size == 0:
        return False
    mz = peaks[:, 0]
    for target in block.fragment_mz:
        if not np.any(np.abs(mz - target) <= tol_da):
            return False
    return True


def annotate_building_blocks(
    mn_results, blocks: list[BuildingBlock] | None = None
) -> dict[str, list[str]]:
    """Map each node to the building blocks its cluster spectrum contains.

    Detection runs on the cluster consensus spectra (a node inherits its
    cluster's building blocks; He et al. assign blocks at the family
    level).
    """
    from .gnps_client import MNResults  # local import: avoid cycle at module load

    if not isinstance(mn_results, MNResults):
        raise TypeError("annotate_building_blocks expects MNResults")
    if blocks is None:
        blocks = BUILDING_BLOCK_CATALOGUE

    node_to_cluster: dict[str, object] = {}
    for cluster in mn_results.clusters:
        for node_id in cluster.node_ids:
            node_to_cluster[node_id] = cluster

    annotations: dict[str, list[str]] = {}
    for cluster in mn_results.clusters:
        spectrum = np.column_stack(
            [
                np.asarray(cluster.consensus_spectrum_mz, dtype=float),
                np.asarray(cluster.consensus_spectrum_intensity, dtype=float),
            ]
        )
        detected = [
            block.name for block in blocks if detect_building_block(spectrum, block)
        ]
        for node_id in cluster.node_ids:
            annotations[node_id] = list(detected)
    return annotations
