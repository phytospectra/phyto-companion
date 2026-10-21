"""HSQC-based molecular networking (Stienstra et al., 2025).

Implements the method of Stienstra, Miller, et al., "NMR molecular
networking", Communications Chemistry 9:28 (2025):

* ``ModifiedHungarianDistance.compute`` -- eq. 1-2 of the paper: a cost
  matrix of normalized 1H/13C shift deviations (capped at 1.0 for pairs
  outside tolerance) solved with the Hungarian assignment
  (``scipy.optimize.linear_sum_assignment``), normalized by
  ``max(len(a), len(b))`` so the distance lies in [0, 1].
* ``algorithmic_molecular_networking`` -- section 3.2: transitive edges
  are added between nodes sharing a neighbor.
* ``annotation_propagation`` -- annotations spread through the network by
  majority vote with a distance-decayed confidence.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.optimize import linear_sum_assignment

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import NetworkEdge, NetworkNode

from .gnps_client import Annotation

__all__ = [
    "HSQCSpectrum",
    "HSQCNode",
    "HSQCEdge",
    "HSQCNetwork",
    "ModifiedHungarianDistance",
    "build_hsqc_network",
    "algorithmic_molecular_networking",
    "annotation_propagation",
    "ANNOTATION_CONFIDENCE_DECAY",
]


ANNOTATION_CONFIDENCE_DECAY = 0.8
"""Per-hop confidence multiplier for propagated annotations.

Stienstra et al. (2025) state qualitatively that propagated annotations
carry reduced confidence.  0.8/hop is our auditable instantiation: 80% of
the donor's confidence at 1 hop, 64% at 2 hops, 51% at 3 -- steep enough
that long chains cannot masquerade as near-seed evidence, shallow enough
that immediate structural relatives (the method's main use) remain
usable.  A documented default, not a fitted constant; recalibrate against
real annotated networks before production claims (ADR-0011.3)."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class HSQCSpectrum(BaseModel):
    """One HSQC peak list (peaks as (h_ppm, c_ppm) tuples for JSON)."""

    model_config = ConfigDict(extra="forbid")

    spectrum_id: str
    sample_id: str
    peaks: list[tuple[float, float]] = Field(..., min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class HSQCNode(NetworkNode):
    """A node in an HSQC network (one spectrum), possibly annotated."""

    spectrum_id: str
    sample_id: str
    annotation: Annotation | None = None
    annotation_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    annotation_source: str | None = None  # "seed" | "propagated" | None
    propagation_distance: int | None = Field(default=None, ge=0)


class HSQCEdge(NetworkEdge):
    """An HSQC edge weighted by the Modified Hungarian distance."""

    modified_hungarian_distance: float = Field(..., ge=0.0, le=1.0)
    is_transitive: bool = False


class HSQCNetwork(BaseModel):
    """An HSQC molecular network with its provenance metadata."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[HSQCNode] = Field(default_factory=list)
    edges: list[HSQCEdge] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Modified Hungarian distance (Stienstra et al. 2025, eq. 1-2)
# ---------------------------------------------------------------------------


class ModifiedHungarianDistance:
    """Distance between two HSQC peak lists.

    Cost matrix (eq. 1)::

        cost[i, j] = sqrt((dh / tol_h)^2 + (dc / tol_c)^2) / sqrt(2)
                     if dh <= tol_h and dc <= tol_c, else 1.0

    where dh/dc are the 1H/13C shift differences for peak pair (i, j).
    The optimal assignment (Hungarian algorithm) selects peak pairs; the
    distance (eq. 2) is::

        D(a, b) = sum(assigned costs) / max(len(a), len(b))

    giving 0 for identical spectra and 1 when no pair falls within
    tolerance (equal-length lists).
    """

    def compute(
        self,
        peaks_a: np.ndarray | list[tuple[float, float]],
        peaks_b: np.ndarray | list[tuple[float, float]],
        tol_h: float = 0.05,
        tol_c: float = 0.5,
    ) -> float:
        a = np.asarray(peaks_a, dtype=float).reshape(-1, 2)
        b = np.asarray(peaks_b, dtype=float).reshape(-1, 2)
        if a.size == 0 or b.size == 0:
            raise InvalidInputError(
                "HSQC peak lists must be non-empty", context={}
            )
        if tol_h <= 0 or tol_c <= 0:
            raise InvalidInputError(
                "tolerances must be positive", context={"tol_h": tol_h, "tol_c": tol_c}
            )

        dh = np.abs(a[:, None, 0] - b[None, :, 0])
        dc = np.abs(a[:, None, 1] - b[None, :, 1])
        within = (dh <= tol_h) & (dc <= tol_c)
        normalized = np.sqrt((dh / tol_h) ** 2 + (dc / tol_c) ** 2) / np.sqrt(2.0)
        cost = np.where(within, normalized, 1.0)

        rows, cols = linear_sum_assignment(cost)
        total = float(cost[rows, cols].sum())
        return total / max(len(a), len(b))


# ---------------------------------------------------------------------------
# Network construction
# ---------------------------------------------------------------------------


def build_hsqc_network(
    spectra: list[HSQCSpectrum], distance_threshold: float = 0.5
) -> HSQCNetwork:
    """Pairwise Modified Hungarian networking; edges below threshold."""
    metric = ModifiedHungarianDistance()
    nodes = [
        HSQCNode(
            id=spectrum.spectrum_id,
            candidate_or_feature=spectrum.spectrum_id,
            modality="HSQC",
            spectrum_id=spectrum.spectrum_id,
            sample_id=spectrum.sample_id,
        )
        for spectrum in spectra
    ]

    size = len(spectra)
    # distance matrix stored in metadata so transitive edges can use real
    # distances without recomputation (Stienstra et al., section 3.2)
    matrix: list[list[float | None]] = [[None] * size for _ in range(size)]
    edges: list[HSQCEdge] = []
    for i in range(size):
        matrix[i][i] = 0.0
        for j in range(i + 1, size):
            distance = metric.compute(spectra[i].peaks, spectra[j].peaks)
            matrix[i][j] = matrix[j][i] = distance
            if distance < distance_threshold:
                edges.append(
                    HSQCEdge(
                        source=spectra[i].spectrum_id,
                        target=spectra[j].spectrum_id,
                        modality="HSQC",
                        score=1.0 - distance,  # similarity form of the weight
                        evidence_type="hsqc_modified_hungarian",
                        modified_hungarian_distance=distance,
                    )
                )

    return HSQCNetwork(
        nodes=nodes,
        edges=edges,
        metadata={
            "distance_threshold": distance_threshold,
            "distance_matrix": matrix,
            "spectrum_order": [spectrum.spectrum_id for spectrum in spectra],
            "method": "Modified Hungarian distance (Stienstra et al. 2025, eq. 1-2)",
        },
    )


# ---------------------------------------------------------------------------
# Algorithmic molecular networking (section 3.2)
# ---------------------------------------------------------------------------


def algorithmic_molecular_networking(network: HSQCNetwork) -> HSQCNetwork:
    """Add transitive edges: nodes sharing a neighbor become connected.

    If A-B and B-C are edges and A-C is not, the direct A-C edge is added
    using the true pairwise distance from the stored matrix, marked
    ``is_transitive=True`` (Stienstra et al. 2025, section 3.2).
    """
    adjacency: dict[str, set[str]] = {node.id: set() for node in network.nodes}
    for edge in network.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    existing = {(edge.source, edge.target) for edge in network.edges}
    existing |= {(target, source) for source, target in existing}

    order = list(network.metadata.get("spectrum_order", []))
    matrix = network.metadata.get("distance_matrix")
    index_of = {spectrum_id: i for i, spectrum_id in enumerate(order)}

    new_edges: list[HSQCEdge] = []
    for center, neighbors in adjacency.items():
        neighbor_list = sorted(neighbors)
        for i in range(len(neighbor_list)):
            for j in range(i + 1, len(neighbor_list)):
                a, c = neighbor_list[i], neighbor_list[j]
                if (a, c) in existing:
                    continue
                distance = 1.0
                if (
                    isinstance(matrix, list)
                    and a in index_of
                    and c in index_of
                ):
                    value = matrix[index_of[a]][index_of[c]]
                    if value is not None:
                        distance = float(value)
                new_edges.append(
                    HSQCEdge(
                        source=a,
                        target=c,
                        modality="HSQC",
                        score=1.0 - distance,
                        evidence_type="hsqc_modified_hungarian",
                        modified_hungarian_distance=distance,
                        is_transitive=True,
                    )
                )
                existing.add((a, c))
                existing.add((c, a))

    network.edges = network.edges + new_edges
    network.metadata = {
        **network.metadata,
        "n_transitive_edges_added": len(new_edges),
    }
    return network


# ---------------------------------------------------------------------------
# Annotation propagation (majority vote, Stienstra et al.)
# ---------------------------------------------------------------------------


def annotation_propagation(
    network: HSQCNetwork,
    known_annotations: dict[str, Annotation],
) -> HSQCNetwork:
    """Propagate annotations through the network by neighbor majority vote.

    Seeds come from ``known_annotations`` (spectrum_id -> Annotation).
    Propagation proceeds in BFS layers; a node in the next layer takes
    the MAJORITY annotation among all its already-annotated neighbors
    (ties broken alphabetically by compound name, deterministically).
    Confidence decays ``ANNOTATION_CONFIDENCE_DECAY`` per hop from the
    donor chain, and the hop count is recorded as
    ``propagation_distance``.
    """
    nodes_by_id = {node.id: node for node in network.nodes}
    for spectrum_id, annotation in known_annotations.items():
        if spectrum_id not in nodes_by_id:
            raise InvalidInputError(
                "Annotation for unknown spectrum", context={"spectrum_id": spectrum_id}
            )
        node = nodes_by_id[spectrum_id]
        node.annotation = annotation
        node.annotation_confidence = annotation.cosine
        node.annotation_source = "seed"
        node.propagation_distance = 0
    if not known_annotations:
        return network

    adjacency: dict[str, set[str]] = {node.id: set() for node in network.nodes}
    for edge in network.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    # structural template for propagated annotations (fields other than
    # compound_name come from the first seed, deterministically)
    template = known_annotations[sorted(known_annotations)[0]]

    # node_id -> (compound_name, confidence, hop distance)
    annotated: dict[str, tuple[str, float, int]] = {
        spectrum_id: (annotation.compound_name, annotation.cosine, 0)
        for spectrum_id, annotation in known_annotations.items()
    }
    visited = set(annotated)

    layer = sorted(annotated)
    while layer:
        candidates: set[str] = set()
        for node_id in layer:
            candidates |= adjacency[node_id] - visited
        next_layer: list[str] = []
        for candidate in sorted(candidates):
            votes: dict[str, int] = {}
            best_conf: dict[str, float] = {}
            nearest_hop = None
            for voter in adjacency[candidate]:
                if voter not in annotated:
                    continue
                name, confidence, hop = annotated[voter]
                votes[name] = votes.get(name, 0) + 1
                best_conf[name] = max(best_conf.get(name, 0.0), confidence)
                if nearest_hop is None or hop < nearest_hop:
                    nearest_hop = hop
            if not votes:
                continue
            # majority vote; ties broken alphabetically by compound name
            winner = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            confidence = round(best_conf[winner] * ANNOTATION_CONFIDENCE_DECAY, 4)
            node = nodes_by_id[candidate]
            node.annotation = template.model_copy(update={"node_id": candidate, "compound_name": winner})
            node.annotation_confidence = confidence
            node.annotation_source = "propagated"
            node.propagation_distance = (nearest_hop or 0) + 1
            annotated[candidate] = (winner, confidence, node.propagation_distance)
            visited.add(candidate)
            next_layer.append(candidate)
        layer = next_layer

    return network
