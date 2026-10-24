"""Network visualization tests: Cytoscape JSON, GraphML, summary, PNG."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path


from networking.fused_network import fuse_networks
from networking.gnps_result_parser import parse_graphml
from networking.network_visualization import (
    network_summary,
    plot_network_matplotlib,
    to_cytoscape_json,
    to_graphml,
)

from test_gnps_result_parser import CLASSICAL, FBMN  # fixture texts

NETWORK = parse_graphml(CLASSICAL)


def _fused():
    from test_fused_network import _hsqc_network, _ms2_network

    return fuse_networks(_ms2_network(), _hsqc_network(), {"scan=2": "H2"})


class TestCytoscapeJSON:
    def test_round_trips_through_json(self):
        payload = to_cytoscape_json(NETWORK)
        assert json.loads(json.dumps(payload)) == payload

    def test_node_fields(self):
        payload = to_cytoscape_json(NETWORK)
        node = payload["elements"]["nodes"][0]["data"]
        for key in (
            "id", "label", "modality", "score", "annotation_name",
            "msi_level", "sample_counts",
        ):
            assert key in node

    def test_edge_fields(self):
        payload = to_cytoscape_json(NETWORK)
        edge = payload["elements"]["edges"][0]["data"]
        for key in ("id", "source", "target", "modality", "cosine", "hsqc_distance"):
            assert key in edge

    def test_style_hints_present(self):
        hints = to_cytoscape_json(NETWORK)["style_hints"]
        for key in ("node_size", "node_color", "edge_color", "edge_width"):
            assert key in hints

    def test_fused_nodes_carry_modality(self):
        payload = to_cytoscape_json(_fused())
        modalities = {node["data"]["modality"] for node in payload["elements"]["nodes"]}
        assert "both" in modalities
        assert "MS2" in modalities
        assert "HSQC" in modalities


class TestGraphML:
    def test_well_formed_xml(self):
        graphml = to_graphml(NETWORK)
        root = ET.fromstring(graphml)
        graph = root.find("{http://graphml.graphdrawing.org/xmlns}graph")
        assert len(graph.findall("{http://graphml.graphdrawing.org/xmlns}node")) == 5
        assert len(graph.findall("{http://graphml.graphdrawing.org/xmlns}edge")) == 4

    def test_networkx_can_read_it(self):
        import networkx as nx

        graph = nx.read_graphml(io.StringIO(to_graphml(NETWORK)))
        assert graph.number_of_nodes() == 5
        assert graph.number_of_edges() == 4


class TestSummary:
    def test_human_readable_counts(self):
        summary = network_summary(NETWORK)
        assert "Network has 5 nodes and 4 edges" in summary["summary"]
        assert "MS2-only" in summary["summary"]
        assert summary["n_nodes"] == 5
        assert summary["n_edges"] == 4

    def test_no_annotations_reports_none(self):
        summary = network_summary(parse_graphml(FBMN))
        assert "none" in summary["summary"]


class TestMatplotlibPlot:
    def test_renders_png(self, tmp_path: Path):
        output = tmp_path / "network.png"
        plot_network_matplotlib(NETWORK, output)
        assert output.exists()
        assert output.stat().st_size > 1000

    def test_fused_network_renders_png(self, tmp_path: Path):
        output = tmp_path / "fused.png"
        plot_network_matplotlib(_fused(), output)
        assert output.exists()
