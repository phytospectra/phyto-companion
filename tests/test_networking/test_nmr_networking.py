"""HSQC networking tests: Modified Hungarian distance, transitive edges,
annotation propagation (Stienstra et al. 2025)."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from networking.gnps_client import Annotation
from networking.nmr_networking import (
    ANNOTATION_CONFIDENCE_DECAY,
    HSQCEdge,
    HSQCNetwork,
    HSQCNode,
    HSQCSpectrum,
    ModifiedHungarianDistance,
    algorithmic_molecular_networking,
    annotation_propagation,
    build_hsqc_network,
)


def _annotation(node_id: str, name: str, cosine: float) -> Annotation:
    return Annotation(
        node_id=node_id,
        compound_name=name,
        smiles="C",
        library="test",
        cosine=cosine,
        msi_level=2,
    )


class TestModifiedHungarianDistance:
    metric = ModifiedHungarianDistance()
    A = [(1.00, 50.0), (2.00, 60.0), (3.00, 70.0)]
    B = [(1.01, 50.2), (2.02, 60.1)]

    def test_identical_spectra_distance_zero(self):
        assert self.metric.compute(self.A, self.A) == 0.0

    def test_symmetric(self):
        assert self.metric.compute(self.A, self.B) == pytest.approx(
            self.metric.compute(self.B, self.A)
        )

    def test_disjoint_equal_length_distance_one(self):
        a = [(1.0, 50.0), (2.0, 60.0)]
        b = [(5.0, 150.0), (6.0, 160.0)]
        assert self.metric.compute(a, b) == 1.0

    def test_disjoint_unequal_length_normalized_by_max(self):
        a = [(1.0, 50.0), (2.0, 60.0)]
        b = [(5.0, 150.0), (6.0, 160.0), (7.0, 170.0)]
        assert self.metric.compute(a, b) == pytest.approx(2.0 / 3.0)

    def test_outside_tolerance_caps_cost_at_one(self):
        a = [(1.0, 50.0)]
        b = [(1.06, 50.0)]  # 1H off by more than tol_h=0.05
        assert self.metric.compute(a, b) == 1.0

    def test_partial_deviation_half_tolerance_each_axis(self):
        # dh = 0.025 = tol_h/2, dc = 0.25 = tol_c/2 -> cost 0.5 (eq. 1)
        a = [(1.0, 50.0)]
        b = [(1.025, 50.25)]
        assert self.metric.compute(a, b) == pytest.approx(0.5)

    def test_empty_peak_list_raises(self):
        with pytest.raises(InvalidInputError):
            self.metric.compute([], self.A)

    def test_non_positive_tolerance_raises(self):
        with pytest.raises(InvalidInputError):
            self.metric.compute(self.A, self.B, tol_h=0.0)

    def test_modified_hungarian_matches_hand_computation(self):
        """Equation verification (ADR-0011.2): the full derivation below
        must be reproduced exactly by the implementation.

        A = [(1.00, 50.0), (2.00, 60.0)]   B = [(1.02, 50.4), (2.00, 60.0), (9.00, 90.0)]
        tol_h = 0.05, tol_c = 0.5 (defaults)

        Cost matrix (eq. 1: normalized deviation, capped at 1.0 outside
        tolerance):
          A1-B1: dh=0.02, dc=0.40 -> sqrt((0.02/0.05)^2 + (0.40/0.5)^2)/sqrt(2)
                                    = sqrt(0.16 + 0.64)/sqrt(2)
                                    = 0.894427/1.414214 = 0.632456
          A1-B2: dh=1.00 > tol   -> 1.0        A1-B3: dh=8.00 > tol -> 1.0
          A2-B1: dh=0.98 > tol   -> 1.0
          A2-B2: dh=0.00, dc=0.00 -> 0.0        A2-B3: dh=7.00 > tol -> 1.0

        Optimal assignment (Hungarian, 2x3): A1->B1 (0.632456) and A2->B2
        (0.0); any alternative costs >= 1.0 + something.

        Distance (eq. 2): sum(assigned) / max(len(a), len(b))
                        = 0.632456 / 3 = 0.210819
        """
        a = [(1.00, 50.0), (2.00, 60.0)]
        b = [(1.02, 50.4), (2.00, 60.0), (9.00, 90.0)]
        assert self.metric.compute(a, b) == pytest.approx(0.6324555 / 3.0, abs=1e-6)


class TestBuildHSQCNetwork:
    def _spectra(self):
        return [
            HSQCSpectrum(
                spectrum_id="A", sample_id="S1", peaks=[(1.0, 50.0), (2.0, 60.0)]
            ),
            HSQCSpectrum(
                spectrum_id="B", sample_id="S2", peaks=[(1.01, 50.2), (2.0, 60.0)]
            ),
            HSQCSpectrum(
                spectrum_id="C", sample_id="S3", peaks=[(7.0, 180.0), (8.0, 190.0)]
            ),
        ]

    def test_edges_below_threshold_only(self):
        network = build_hsqc_network(self._spectra(), distance_threshold=0.5)
        assert [(edge.source, edge.target) for edge in network.edges] == [("A", "B")]
        assert all(edge.modality == "HSQC" for edge in network.edges)
        assert network.edges[0].modified_hungarian_distance < 0.5

    def test_metadata_stores_distance_matrix(self):
        network = build_hsqc_network(self._spectra(), distance_threshold=0.5)
        assert network.metadata["spectrum_order"] == ["A", "B", "C"]
        matrix = network.metadata["distance_matrix"]
        assert matrix[0][0] == 0.0
        assert matrix[0][2] == 1.0  # A vs C: no pairs within tolerance

    def test_nodes_reference_spectra(self):
        network = build_hsqc_network(self._spectra(), distance_threshold=0.5)
        assert {node.spectrum_id for node in network.nodes} == {"A", "B", "C"}


class TestAlgorithmicNetworking:
    def test_transitive_edge_added_with_true_distance(self):
        # A shares two peaks with B; B shares two OTHER peaks with C; A and
        # C have nothing within tolerance (equal lengths -> distance 1.0).
        spectra = [
            HSQCSpectrum(
                spectrum_id="A", sample_id="S", peaks=[(1.0, 50.0), (2.0, 60.0)]
            ),
            HSQCSpectrum(
                spectrum_id="B",
                sample_id="S",
                peaks=[(1.01, 50.1), (2.01, 60.1), (6.99, 149.9), (8.01, 169.9)],
            ),
            HSQCSpectrum(
                spectrum_id="C", sample_id="S", peaks=[(7.0, 150.0), (8.0, 170.0)]
            ),
        ]
        network = build_hsqc_network(spectra, distance_threshold=0.5)
        direct = {(e.source, e.target) for e in network.edges}
        assert ("A", "B") in direct and ("B", "C") in direct
        assert ("A", "C") not in direct  # true distance is 1.0

        expanded = algorithmic_molecular_networking(network)
        transitive = [e for e in expanded.edges if e.is_transitive]
        assert len(transitive) == 1
        assert {transitive[0].source, transitive[0].target} == {"A", "C"}
        assert transitive[0].modified_hungarian_distance == pytest.approx(1.0)
        assert expanded.metadata["n_transitive_edges_added"] == 1


class TestAnnotationPropagation:
    def _line_network(self) -> HSQCNetwork:
        nodes = [
            HSQCNode(
                id=nid, candidate_or_feature=nid, modality="HSQC",
                spectrum_id=nid, sample_id="S",
            )
            for nid in ("N1", "N2", "N3")
        ]
        edges = [
            HSQCEdge(
                source="N1", target="N2", modality="HSQC", score=0.9,
                evidence_type="hsqc_modified_hungarian",
                modified_hungarian_distance=0.1,
            ),
            HSQCEdge(
                source="N2", target="N3", modality="HSQC", score=0.8,
                evidence_type="hsqc_modified_hungarian",
                modified_hungarian_distance=0.2,
            ),
        ]
        return HSQCNetwork(nodes=nodes, edges=edges)

    def test_propagation_with_decay_and_distance(self):
        network = annotation_propagation(
            self._line_network(), {"N1": _annotation("N1", "Compound P", 0.95)}
        )
        by_id = {node.id: node for node in network.nodes}
        assert by_id["N1"].annotation_source == "seed"
        assert by_id["N1"].propagation_distance == 0
        assert by_id["N2"].annotation.compound_name == "Compound P"
        assert by_id["N2"].annotation_confidence == pytest.approx(
            round(0.95 * ANNOTATION_CONFIDENCE_DECAY, 4)
        )
        assert by_id["N2"].propagation_distance == 1
        assert by_id["N3"].annotation_confidence == pytest.approx(
            round(0.95 * ANNOTATION_CONFIDENCE_DECAY**2, 4)
        )
        assert by_id["N3"].propagation_distance == 2

    def test_majority_vote(self):
        nodes = [
            HSQCNode(
                id=nid, candidate_or_feature=nid, modality="HSQC",
                spectrum_id=nid, sample_id="S",
            )
            for nid in ("A1", "A2", "B", "C")
        ]
        edges = [
            HSQCEdge(
                source="C", target=other, modality="HSQC", score=0.8,
                evidence_type="hsqc_modified_hungarian",
                modified_hungarian_distance=0.2,
            )
            for other in ("A1", "A2", "B")
        ]
        network = HSQCNetwork(nodes=nodes, edges=edges)
        seeds = {
            "A1": _annotation("A1", "Compound P", 0.9),
            "A2": _annotation("A2", "Compound P", 0.8),
            "B": _annotation("B", "Compound Q", 0.95),
        }
        result = annotation_propagation(network, seeds)
        center = {node.id: node for node in result.nodes}["C"]
        # 2 votes for P beat 1 vote for Q; confidence from the best P voter
        assert center.annotation.compound_name == "Compound P"
        assert center.annotation_confidence == pytest.approx(
            round(0.9 * ANNOTATION_CONFIDENCE_DECAY, 4)
        )
        assert center.annotation_source == "propagated"

    def test_unknown_seed_raises(self):
        with pytest.raises(InvalidInputError):
            annotation_propagation(
                self._line_network(), {"ghost": _annotation("ghost", "X", 0.9)}
            )

    def test_no_seeds_leaves_nodes_clean(self):
        result = annotation_propagation(self._line_network(), {})
        assert all(node.annotation is None for node in result.nodes)
