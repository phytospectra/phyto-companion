"""Fused MS2-HSQC network tests: fusion, cross-modal propagation, reporting."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from networking.fused_network import (
    CROSS_MODAL_PENALTY,
    FusedEdge,
    FusedNetwork,
    FusedNode,
    cross_modal_annotation,
    fuse_networks,
    modality_overlap_report,
)
from networking.gnps_client import Annotation
from networking.gnps_result_parser import GNPSEdge, GNPSNode, ParsedNetwork
from networking.nmr_networking import HSQCEdge, HSQCNetwork, HSQCNode


def _annotation(node_id: str, name: str = "quercetin", cosine: float = 0.9) -> Annotation:
    return Annotation(
        node_id=node_id,
        compound_name=name,
        smiles="Oc1cc(O)c(c(c1)O)",
        inchikey="REFJWTPEDVJJIY-UHFFFAOYSA-N",
        library="test",
        cosine=cosine,
        msi_level=2,
    )


def _ms2_network(annotate: str | None = None) -> ParsedNetwork:
    nodes = []
    for scan in ("scan=1", "scan=2", "scan=3"):
        nodes.append(
            GNPSNode(
                id=scan,
                candidate_or_feature=scan,
                modality="MS2",
                node_id=scan,
                feature_id=scan,
                parent_mass=500.0,
                annotation=_annotation(scan) if scan == annotate else None,
            )
        )
    edges = [
        GNPSEdge(
            source="scan=1", target="scan=2", modality="MS2",
            score=0.9, evidence_type="ms2_cosine",
            cosine=0.9, matched_peaks=8,
        )
    ]
    return ParsedNetwork(nodes=nodes, edges=edges)


def _hsqc_network() -> HSQCNetwork:
    nodes = [
        HSQCNode(
            id=f"H{i}", candidate_or_feature=f"H{i}", modality="HSQC",
            spectrum_id=f"H{i}", sample_id="S",
        )
        for i in (1, 2, 3)
    ]
    edges = [
        HSQCEdge(
            source="H1", target="H2", modality="HSQC", score=0.8,
            evidence_type="hsqc_modified_hungarian",
            modified_hungarian_distance=0.2,
        )
    ]
    return HSQCNetwork(nodes=nodes, edges=edges)


def _unannotated(fused: FusedNetwork) -> list[str]:
    return [
        node_id
        for node_id, node in fused.nodes.items()
        if node.merged_annotation is None
    ]


class TestFusion:
    def test_zero_overlap_keeps_nodes_separate(self):
        fused = fuse_networks(_ms2_network(), _hsqc_network(), {})
        report = modality_overlap_report(fused)
        assert report == {
            "total_nodes": 6,
            "ms2_only_nodes": 3,
            "hsqc_only_nodes": 3,
            "both_modality_nodes": 0,
            "total_edges": 2,
            "edges_ms2": 1,
            "edges_hsqc": 1,
            "edges_both": 0,
        }
        assert all(node.compound_id is None for node in fused.nodes.values())

    def test_full_overlap_merges_everything(self):
        links = {"scan=1": "H1", "scan=2": "H2", "scan=3": "H3"}
        fused = fuse_networks(_ms2_network(), _hsqc_network(), links)
        report = modality_overlap_report(fused)
        assert report["total_nodes"] == 3
        assert report["both_modality_nodes"] == 3
        # the pair connected in BOTH modalities becomes one "both" edge
        both_edges = [e for e in fused.edges if e.modality == "both"]
        assert len(both_edges) == 1
        assert both_edges[0].ms2_cosine == pytest.approx(0.9)
        assert both_edges[0].hsqc_distance == pytest.approx(0.2)

    def test_partial_overlap_counts(self):
        fused = fuse_networks(_ms2_network(), _hsqc_network(), {"scan=2": "H2"})
        report = modality_overlap_report(fused)
        assert report["both_modality_nodes"] == 1
        assert report["ms2_only_nodes"] == 2
        assert report["hsqc_only_nodes"] == 2

    def test_merged_node_inherits_ms2_annotation(self):
        fused = fuse_networks(
            _ms2_network(annotate="scan=2"), _hsqc_network(), {"scan=2": "H2"}
        )
        merged = [
            node
            for node in fused.nodes.values()
            if node.has_ms2 and node.has_hsqc
        ]
        assert len(merged) == 1
        assert merged[0].merged_annotation.compound_name == "quercetin"
        assert merged[0].annotation_source == "MS2"
        assert merged[0].compound_id == "REFJWTPEDVJJIY-UHFFFAOYSA-N"
        assert merged[0].ms2_node_id == "scan=2"
        assert merged[0].hsqc_node_id == "H2"

    def test_unlinked_annotated_ms2_node_stays_ms2(self):
        fused = fuse_networks(
            _ms2_network(annotate="scan=3"), _hsqc_network(), {}
        )
        node = next(
            n for n in fused.nodes.values() if n.ms2_node_id == "scan=3"
        )
        assert node.annotation_source == "MS2"
        assert node.merged_annotation is not None

    def test_unknown_link_key_raises(self):
        with pytest.raises(ValueError):
            fuse_networks(_ms2_network(), _hsqc_network(), {"ghost": "H1"})
        with pytest.raises(ValueError):
            fuse_networks(_ms2_network(), _hsqc_network(), {"scan=1": "ghost"})


class TestCrossModalPropagation:
    def _fused(self) -> FusedNetwork:
        # F(merged: scan=2 + H1, annotated from MS2) --HSQC-edge--> F(H2)
        # F(scan=1, unannotated) --MS2-edge--> F(merged)
        ms2 = _ms2_network(annotate="scan=2")
        hsqc = _hsqc_network()
        return fuse_networks(ms2, hsqc, {"scan=2": "H1"})

    def test_annotation_crosses_modality_with_penalty(self):
        fused = self._fused()
        before = _unannotated(fused)
        h2_node = next(n for n in fused.nodes.values() if n.hsqc_node_id == "H2")
        assert h2_node.node_id in before

        propagated = cross_modal_annotation(fused)
        node = propagated.nodes[h2_node.node_id]
        assert node.merged_annotation.compound_name == "quercetin"
        assert node.annotation_source == "cross_modal"
        assert node.confidence == pytest.approx(round(0.9 * CROSS_MODAL_PENALTY, 4))
        assert len(node.evidence) == 1
        assert node.evidence[0].relation == "cross_modal_propagation"
        assert node.evidence[0].strength == node.confidence

    def test_same_modality_edge_does_not_propagate(self):
        fused = self._fused()
        propagated = cross_modal_annotation(fused)
        ms2_only = next(
            n for n in propagated.nodes.values() if n.ms2_node_id == "scan=1"
        )
        # connected to the annotated node only via an MS2 edge -> stays clean
        assert ms2_only.merged_annotation is None

    def test_propagation_reduces_unannotated_count(self):
        fused = self._fused()
        before = len(_unannotated(fused))
        after = len(_unannotated(cross_modal_annotation(fused)))
        assert after == before - 1

    def test_fusion_beats_single_modality_coverage(self):
        """Design principle 2: fusion must annotate more than either
        modality alone on synthetic data."""
        fused = cross_modal_annotation(self._fused())
        annotated_fused = len(fused.nodes) - len(_unannotated(fused))
        ms2_alone = 1  # only scan=2 was annotated in the MS2 network
        hsqc_alone = 0
        assert annotated_fused > max(ms2_alone, hsqc_alone)

    def test_no_chained_propagation(self):
        # a propagated annotation must not donate further
        fused = FusedNetwork(
            nodes={
                "A": FusedNode(
                    node_id="A", has_ms2=True, has_hsqc=False,
                    merged_annotation=_annotation("A"),
                    annotation_source="cross_modal", confidence=0.5,
                ),
                "B": FusedNode(node_id="B", has_ms2=False, has_hsqc=True),
            },
            edges=[],
        )
        result = cross_modal_annotation(fused)
        assert result.nodes["B"].merged_annotation is None


class TestFusedEdgeValidation:
    def test_ms2_edge_requires_cosine(self):
        with pytest.raises(ValidationError):
            FusedEdge(source="a", target="b", modality="MS2")

    def test_hsqc_edge_requires_distance(self):
        with pytest.raises(ValidationError):
            FusedEdge(source="a", target="b", modality="HSQC")

    def test_both_edge_requires_both_weights(self):
        with pytest.raises(ValidationError):
            FusedEdge(source="a", target="b", modality="both", ms2_cosine=0.9)

    def test_self_loop_rejected(self):
        with pytest.raises(ValidationError):
            FusedEdge(source="a", target="a", modality="MS2", ms2_cosine=0.9)


class TestFusedNetworkModel:
    def test_edge_to_unknown_node_rejected(self):
        with pytest.raises(ValidationError):
            FusedNetwork(
                nodes={
                    "A": FusedNode(node_id="A", has_ms2=True, has_hsqc=False),
                },
                edges=[
                    FusedEdge(source="A", target="ghost", modality="MS2", ms2_cosine=0.9)
                ],
            )

    def test_stats_autofilled(self):
        fused = FusedNetwork(
            nodes={
                "A": FusedNode(node_id="A", has_ms2=True, has_hsqc=False),
                "B": FusedNode(node_id="B", has_ms2=False, has_hsqc=True),
            },
            edges=[
                FusedEdge(source="A", target="B", modality="MS2", ms2_cosine=0.9)
            ],
        )
        assert fused.modality_overlap_stats["total_nodes"] == 2
        assert fused.modality_overlap_stats["ms2_only_nodes"] == 1
