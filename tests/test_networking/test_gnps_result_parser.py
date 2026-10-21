"""GNPS result parser tests against the (synthetic) schema-matched fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from phyto_shared.exceptions import InvalidInputError
from networking.gnps_result_parser import (
    parse_annotations,
    parse_clusters,
    parse_graphml,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gnps"
CLASSICAL = (FIXTURES / "classical_mn.graphml").read_text()
FBMN = (FIXTURES / "fbmn.graphml").read_text()
IIMN = (FIXTURES / "iimn.graphml").read_text()
ANNOTATIONS_TSV = (FIXTURES / "library_annotations.tsv").read_text()
CLUSTERS_TSV = (FIXTURES / "clusters.tsv").read_text()


class TestParseGraphML:
    def test_classical_network(self):
        network = parse_graphml(CLASSICAL)
        assert len(network.nodes) == 5
        assert len(network.edges) == 4
        first = network.nodes[0]
        assert first.node_id == "scan=1"
        assert first.parent_mass == pytest.approx(571.3012)
        assert first.rt == pytest.approx(12.4)
        assert first.sample_counts == {
            "ABUNDANCE_F1": 125000.0,
            "ABUNDANCE_F2": 98000.0,
        }
        assert first.modality == "MS2"

    def test_edge_fields(self):
        network = parse_graphml(CLASSICAL)
        edge = network.edges[0]
        assert edge.source == "scan=1"
        assert edge.target == "scan=2"
        assert edge.cosine == pytest.approx(0.92)
        assert edge.matched_peaks == 11
        assert edge.evidence_type == "ms2_cosine"

    def test_fbmn_feature_ids(self):
        network = parse_graphml(FBMN)
        assert {node.feature_id for node in network.nodes} == {"FT1", "FT2", "FT3", "FT4"}

    def test_iimn_ion_identity(self):
        network = parse_graphml(IIMN)
        by_id = {node.node_id: node for node in network.nodes}
        assert by_id["scan=20"].ion_identity == "M+H/M+Na-group-1"
        assert not by_id["scan=22"].ion_identity

    def test_missing_required_node_attribute_raises(self):
        graphml = """<?xml version="1.0"?>
        <graphml xmlns="http://graphml.graphdrawing.org/xmlns">
          <key id="d0" for="node" attr.name="label" attr.type="string"/>
          <graph edgedefault="undirected">
            <node id="x"><data key="d0">x</data></node>
          </graph>
        </graphml>"""
        with pytest.raises(InvalidInputError) as excinfo:
            parse_graphml(graphml)
        assert "precursor mass" in str(excinfo.value.context["missing"])

    def test_missing_required_edge_attribute_raises(self):
        graphml = """<?xml version="1.0"?>
        <graphml xmlns="http://graphml.graphdrawing.org/xmlns">
          <key id="d0" for="node" attr.name="label" attr.type="string"/>
          <key id="d1" for="node" attr.name="precursor mass" attr.type="double"/>
          <key id="d2" for="edge" attr.name="matched peaks" attr.type="int"/>
          <graph edgedefault="undirected">
            <node id="x"><data key="d0">x</data><data key="d1">100.0</data></node>
            <node id="y"><data key="d0">y</data><data key="d1">200.0</data></node>
            <edge id="e0" source="x" target="y"><data key="d2">5</data></edge>
          </graph>
        </graphml>"""
        with pytest.raises(InvalidInputError) as excinfo:
            parse_graphml(graphml)
        assert "cosine score" in str(excinfo.value.context["missing"])

    def test_malformed_xml_raises(self):
        with pytest.raises(InvalidInputError):
            parse_graphml("<graphml this is not xml")

    def test_graph_without_graph_element_raises(self):
        with pytest.raises(InvalidInputError):
            parse_graphml(
                '<?xml version="1.0"?><graphml xmlns="http://graphml.graphdrawing.org/xmlns"/>'
            )


class TestParseAnnotations:
    def test_fixture_round_numbers(self):
        annotations = parse_annotations(ANNOTATIONS_TSV)
        assert len(annotations) == 3
        first = annotations[0]
        assert first.node_id == "scan=1"
        assert first.compound_name == "Cyclo(Pro-Val)"
        assert first.cosine == pytest.approx(0.95)
        assert first.library == "sample_a.mzML"
        assert {a.msi_level for a in annotations} == {2, 3}

    def test_missing_column_raises(self):
        broken = ANNOTATIONS_TSV.replace("MQScore\t", "")
        with pytest.raises(InvalidInputError) as excinfo:
            parse_annotations(broken)
        assert "MQScore" in str(excinfo.value.context["missing"])

    def test_malformed_value_raises(self):
        broken = ANNOTATIONS_TSV.replace("0.95", "not-a-number")
        with pytest.raises(InvalidInputError):
            parse_annotations(broken)


class TestParseClusters:
    def test_fixture(self):
        clusters = parse_clusters(CLUSTERS_TSV)
        assert len(clusters) == 2
        first = clusters[0]
        assert first.cluster_id == "cluster-1"
        assert first.node_ids == ["scan=1", "scan=2", "scan=3"]
        assert len(first.consensus_spectrum_mz) == 3
        assert len(first.consensus_spectrum_intensity) == 3

    def test_length_mismatch_raises(self):
        broken = CLUSTERS_TSV.replace(
            "571.3012;628.3323;699.3689", "571.3012;628.3323"
        )
        with pytest.raises(InvalidInputError):
            parse_clusters(broken)

    def test_missing_column_raises(self):
        broken = CLUSTERS_TSV.replace("member_scans\t", "")
        with pytest.raises(InvalidInputError):
            parse_clusters(broken)
