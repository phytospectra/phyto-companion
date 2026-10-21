"""The fused MS2-HSQC molecular network: the platform's core contribution.

Nodes are compounds; edges carry MS2 similarity, HSQC similarity, or
both.  ``compound_links`` (from the ingestion service's paired NMR-MS
samples) fuses the two modality-specific networks into one graph, after
which annotations cross the modality boundary with an explicit
confidence penalty -- every propagation is logged as an EvidenceItem
with ``relation="cross_modal_propagation"``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phyto_shared.models import (
    EvidenceItem,
    FusedNetwork as FusedNetworkBase,
)
from phyto_shared.provenance import sha256_dict

from .gnps_client import Annotation
from .gnps_result_parser import ParsedNetwork
from .nmr_networking import HSQCNetwork

__all__ = [
    "FusedNode",
    "FusedEdge",
    "FusedNetwork",
    "fuse_networks",
    "cross_modal_annotation",
    "modality_overlap_report",
    "CROSS_MODAL_PENALTY",
]

CROSS_MODAL_PENALTY = 0.7
"""Confidence multiplier applied when an annotation crosses the modality
boundary (MS2 -> HSQC or HSQC -> MS2).  A cross-modal propagation is
weaker evidence than a direct library hit in the same modality."""


class FusedNode(BaseModel):
    """One compound in the fused network, possibly seen by both modalities."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    compound_id: str | None = None  # canonical InChIKey when assigned
    has_ms2: bool
    has_hsqc: bool
    ms2_node_id: str | None = None
    hsqc_node_id: str | None = None
    merged_annotation: Optional[Annotation] = None
    annotation_source: Literal["MS2", "HSQC", "fused", "cross_modal"] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class FusedEdge(BaseModel):
    """One edge in the fused network, possibly carrying BOTH weights.

    CONVENTION WARNING (documented per ADR-0007/0008, Step 5 review):
    the two weights use OPPOSITE conventions.  ``ms2_cosine`` is a
    SIMILARITY (higher is better, 0-1); ``hsqc_distance`` is the Modified
    Hungarian DISTANCE (lower is better, 0-1).  Consumers must never
    compare or combine the raw numbers; convert first (e.g.
    ``1 - hsqc_distance`` for a similarity view).
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    modality: Literal["MS2", "HSQC", "both"]
    ms2_cosine: float | None = Field(default=None, ge=0.0, le=1.0)
    hsqc_distance: float | None = Field(default=None, ge=0.0, le=1.0)
    is_transitive: bool = False

    @model_validator(mode="after")
    def _no_self_loop(self) -> "FusedEdge":
        if self.source == self.target:
            raise ValueError("self-loops are not allowed in fused networks")
        if self.modality == "MS2" and self.ms2_cosine is None:
            raise ValueError("MS2 edges must carry ms2_cosine")
        if self.modality == "HSQC" and self.hsqc_distance is None:
            raise ValueError("HSQC edges must carry hsqc_distance")
        if self.modality == "both" and (self.ms2_cosine is None or self.hsqc_distance is None):
            raise ValueError("edges marked 'both' must carry both weights")
        return self


class FusedNetwork(FusedNetworkBase):
    """Multi-modal fused network (extends the Step 2 model).

    ``nodes`` becomes an id-keyed mapping of ``FusedNode`` and edges are
    ``FusedEdge``; the topology validator and overlap stats are adapted
    accordingly.  ``modality_overlap_stats`` is filled from
    ``modality_overlap_report``.
    """

    nodes: dict[str, FusedNode] = Field(default_factory=dict)
    edges: list[FusedEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _topology_consistent(self) -> "FusedNetwork":  # overrides base
        known = set(self.nodes)
        if len(known) != len(self.nodes):
            raise ValueError("fused node ids must be unique (dict keys)")
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(
                    f"fused edge {edge.source}->{edge.target} references an "
                    "unknown node"
                )
        if self.modality_overlap_stats == {}:
            object.__setattr__(self, "modality_overlap_stats", modality_overlap_report(self))
        return self

    # The base class's list-based stats are replaced by the fused report.
    def compute_overlap_stats(self) -> dict[str, int]:  # overrides base
        return modality_overlap_report(self)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def fuse_networks(
    ms2: ParsedNetwork,
    hsqc: HSQCNetwork,
    compound_links: dict[str, str],
) -> FusedNetwork:
    """Fuse an MS2 network and an HSQC network via ``compound_links``.

    ``compound_links`` maps MS2 feature ids to HSQC spectrum ids for
    compounds known (from paired samples) to appear in both modalities.
    Linked nodes merge into one ``FusedNode``; unlinked nodes stay
    single-modality.  Edges from both modalities are carried into the
    fused id space; a pair connected in both modalities becomes one
    ``modality="both"`` edge with both weights recorded.
    """
    ms2_nodes = {node.feature_id: node for node in ms2.nodes}
    ms2_nodes.update({node.node_id: node for node in ms2.nodes})
    hsqc_nodes = {node.spectrum_id: node for node in hsqc.nodes}

    # group MS2 features by the HSQC spectrum they link to
    groups: dict[str, list[str]] = {}
    for ms2_feature_id, hsqc_spectrum_id in compound_links.items():
        if ms2_feature_id not in ms2_nodes:
            raise ValueError(
                f"compound_links references unknown MS2 feature {ms2_feature_id!r}"
            )
        if hsqc_spectrum_id not in hsqc_nodes:
            raise ValueError(
                f"compound_links references unknown HSQC spectrum "
                f"{hsqc_spectrum_id!r}"
            )
        groups.setdefault(hsqc_spectrum_id, []).append(ms2_feature_id)

    fused_nodes: dict[str, FusedNode] = {}
    ms2_to_fused: dict[str, str] = {}
    hsqc_to_fused: dict[str, str] = {}

    def _annotation_of(ms2_node=None, hsqc_node=None) -> tuple[Annotation | None, str | None, float]:
        ms2_ann = ms2_node.annotation if ms2_node is not None else None
        hsqc_ann = hsqc_node.annotation if hsqc_node is not None else None
        if ms2_ann is not None and hsqc_ann is not None:
            # both modalities annotated the same compound: keep the MS2
            # library hit (typically more specific) but mark the source
            return ms2_ann, "fused", ms2_ann.cosine
        if ms2_ann is not None:
            return ms2_ann, "MS2", ms2_ann.cosine
        if hsqc_ann is not None:
            return hsqc_ann, "HSQC", hsqc_ann.cosine
        return None, None, 0.0

    counter = 0

    # merged (both-modality) nodes first, deterministic order
    for hsqc_spectrum_id in sorted(groups):
        members = sorted(groups[hsqc_spectrum_id])
        representative = ms2_nodes[members[0]]
        hsqc_node = hsqc_nodes[hsqc_spectrum_id]
        annotation, source, confidence = _annotation_of(representative, hsqc_node)
        counter += 1
        fused_id = f"F{counter}"
        fused_nodes[fused_id] = FusedNode(
            node_id=fused_id,
            compound_id=annotation.inchikey if annotation else None,
            has_ms2=True,
            has_hsqc=True,
            ms2_node_id=representative.feature_id,
            hsqc_node_id=hsqc_spectrum_id,
            merged_annotation=annotation,
            annotation_source=source,
            confidence=confidence,
        )
        for member in members:
            ms2_to_fused[member] = fused_id
        hsqc_to_fused[hsqc_spectrum_id] = fused_id

    # remaining MS2-only nodes
    for key in sorted(ms2_nodes):
        node = ms2_nodes[key]
        if node.feature_id in ms2_to_fused:
            continue
        counter += 1
        fused_id = f"F{counter}"
        annotation, source, confidence = _annotation_of(ms2_node=node)
        fused_nodes[fused_id] = FusedNode(
            node_id=fused_id,
            compound_id=annotation.inchikey if annotation else None,
            has_ms2=True,
            has_hsqc=False,
            ms2_node_id=node.feature_id,
            merged_annotation=annotation,
            annotation_source=source,
            confidence=confidence,
        )
        ms2_to_fused[node.feature_id] = fused_id
        ms2_to_fused[node.node_id] = fused_id

    # remaining HSQC-only nodes
    for spectrum_id in sorted(hsqc_nodes):
        if spectrum_id in hsqc_to_fused:
            continue
        node = hsqc_nodes[spectrum_id]
        counter += 1
        fused_id = f"F{counter}"
        annotation, source, confidence = _annotation_of(hsqc_node=node)
        fused_nodes[fused_id] = FusedNode(
            node_id=fused_id,
            compound_id=annotation.inchikey if annotation else None,
            has_ms2=False,
            has_hsqc=True,
            hsqc_node_id=spectrum_id,
            merged_annotation=annotation,
            annotation_source=source,
            confidence=confidence,
        )
        hsqc_to_fused[spectrum_id] = fused_id

    # carry edges into the fused id space; merge same-pair edges
    candidates: dict[tuple[str, str], dict] = {}

    def _edge_key(source: str, target: str) -> tuple[str, str]:
        return (source, target) if source <= target else (target, source)

    for edge in ms2.edges:
        source = ms2_to_fused.get(edge.source)
        target = ms2_to_fused.get(edge.target)
        if source is None or target is None or source == target:
            continue
        key = _edge_key(source, target)
        slot = candidates.setdefault(key, {})
        if slot.get("ms2_cosine") is None or edge.cosine > slot["ms2_cosine"]:
            slot["ms2_cosine"] = edge.cosine

    for edge in hsqc.edges:
        source = hsqc_to_fused.get(edge.source)
        target = hsqc_to_fused.get(edge.target)
        if source is None or target is None or source == target:
            continue
        key = _edge_key(source, target)
        slot = candidates.setdefault(key, {})
        distance = edge.modified_hungarian_distance
        if slot.get("hsqc_distance") is None or distance < slot["hsqc_distance"]:
            slot["hsqc_distance"] = distance
            slot["is_transitive"] = edge.is_transitive

    fused_edges: list[FusedEdge] = []
    for (source, target), slot in sorted(candidates.items()):
        has_ms2_weight = slot.get("ms2_cosine") is not None
        has_hsqc_weight = slot.get("hsqc_distance") is not None
        if has_ms2_weight and has_hsqc_weight:
            modality = "both"
        elif has_ms2_weight:
            modality = "MS2"
        else:
            modality = "HSQC"
        fused_edges.append(
            FusedEdge(
                source=source,
                target=target,
                modality=modality,
                ms2_cosine=slot.get("ms2_cosine"),
                hsqc_distance=slot.get("hsqc_distance"),
                is_transitive=bool(slot.get("is_transitive", False)),
            )
        )

    return FusedNetwork(
        nodes=fused_nodes,
        edges=fused_edges,
        modality_overlap_stats=modality_overlap_report_from(fused_nodes, fused_edges),
    )


# ---------------------------------------------------------------------------
# Cross-modal annotation propagation
# ---------------------------------------------------------------------------


def cross_modal_annotation(fused: FusedNetwork) -> FusedNetwork:
    """Propagate annotations ACROSS modalities with a confidence penalty.

    A node annotated in one modality (``annotation_source`` in
    {"MS2", "HSQC", "fused"}) connected to an unannotated node by an edge
    of the OTHER modality donates its annotation: confidence is
    ``CROSS_MODAL_PENALTY * donor confidence``, ``annotation_source``
    becomes ``"cross_modal"``, and an EvidenceItem with
    ``relation="cross_modal_propagation"`` is appended to the recipient.
    Same-modality propagation is the Stienstra algorithm's domain and is
    deliberately NOT performed here.
    """
    proposals: dict[str, tuple[float, str, str]] = {}  # node_id -> (conf, donor, name)

    for edge in sorted(fused.edges, key=lambda e: (e.source, e.target)):
        for donor_id, recipient_id in ((edge.source, edge.target), (edge.target, edge.source)):
            donor = fused.nodes[donor_id]
            if donor.merged_annotation is None:
                continue
            if donor.annotation_source == "cross_modal":
                continue  # no chains of propagated annotations
            recipient = fused.nodes[recipient_id]
            if recipient.merged_annotation is not None:
                continue
            if edge.modality == "both":
                continue  # ambiguous provenance: require a single-modality edge
            if edge.modality == donor.annotation_source:
                continue  # same-modality edge: not cross-modal
            confidence = round(donor.confidence * CROSS_MODAL_PENALTY, 4)
            name = donor.merged_annotation.compound_name
            current = proposals.get(recipient_id)
            if current is None or confidence > current[0]:
                proposals[recipient_id] = (confidence, donor_id, name)

    for recipient_id, (confidence, donor_id, name) in sorted(proposals.items()):
        recipient = fused.nodes[recipient_id]
        donor = fused.nodes[donor_id]
        annotation = donor.merged_annotation.model_copy(update={"node_id": recipient_id})
        recipient.merged_annotation = annotation
        recipient.annotation_source = "cross_modal"
        recipient.confidence = confidence
        recipient.evidence.append(
            EvidenceItem(
                source_spectrum=donor_id,
                spectral_feature=f"cross-modal edge {donor_id}--{recipient_id}",
                relation="cross_modal_propagation",
                strength=confidence,
            )
        )

    fused.modality_overlap_stats = modality_overlap_report(fused)
    return fused


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def modality_overlap_report_from(
    nodes: dict[str, FusedNode], edges: list[FusedEdge]
) -> dict[str, int]:
    return {
        "total_nodes": len(nodes),
        "ms2_only_nodes": sum(1 for n in nodes.values() if n.has_ms2 and not n.has_hsqc),
        "hsqc_only_nodes": sum(1 for n in nodes.values() if n.has_hsqc and not n.has_ms2),
        "both_modality_nodes": sum(1 for n in nodes.values() if n.has_ms2 and n.has_hsqc),
        "total_edges": len(edges),
        "edges_ms2": sum(1 for e in edges if e.modality == "MS2"),
        "edges_hsqc": sum(1 for e in edges if e.modality == "HSQC"),
        "edges_both": sum(1 for e in edges if e.modality == "both"),
    }


def modality_overlap_report(fused: FusedNetwork) -> dict[str, int]:
    """Counts behind the 'how much does fusion help' analysis."""
    return modality_overlap_report_from(fused.nodes, fused.edges)


def fused_network_digest(fused: FusedNetwork) -> str:
    """SHA256 over the network content (for provenance manifests)."""
    return sha256_dict(
        {
            "nodes": {k: v.model_dump() for k, v in fused.nodes.items()},
            "edges": [e.model_dump() for e in fused.edges],
        }
    )
