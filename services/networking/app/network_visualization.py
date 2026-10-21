"""Network visualization and export: Cytoscape.js JSON, GraphML, summaries.

``plot_network_matplotlib`` imports matplotlib lazily so the rest of the
service stays light; the JSON/GraphML exports are dependency-free.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal

from phyto_shared.exceptions import InvalidInputError

from .bioactivity_networking import BioactivityNetwork
from .fused_network import FusedNetwork
from .gnps_result_parser import ParsedNetwork

__all__ = [
    "to_cytoscape_json",
    "to_graphml",
    "network_summary",
    "plot_network_matplotlib",
]

NetworkLike = ParsedNetwork | FusedNetwork | BioactivityNetwork

STYLE_HINTS: dict[str, str] = {
    "node_size": "map by total sample abundance (sqrt scale)",
    "node_color": "by modality: MS2-only, HSQC-only, both",
    "edge_color": "by edge modality",
    "edge_width": "by score (cosine or 1 - hsqc distance)",
    "label": "compound name when annotated, else node id",
}


def _node_payload(node: Any) -> dict[str, Any]:
    from .fused_network import FusedNode

    if isinstance(node, FusedNode):
        annotation = node.merged_annotation
        return {
            "id": node.node_id,
            "label": annotation.compound_name if annotation else node.node_id,
            "modality": "both" if node.has_ms2 and node.has_hsqc else ("MS2" if node.has_ms2 else "HSQC"),
            "score": node.confidence,
            "annotation_name": annotation.compound_name if annotation else None,
            "msi_level": annotation.msi_level if annotation else None,
            "sample_counts": {},
        }
    annotation = getattr(node, "annotation", None)
    return {
        "id": getattr(node, "node_id", node.id),
        "label": annotation.compound_name if annotation else str(getattr(node, "feature_id", node.id)),
        "modality": "MS2",
        "score": annotation.cosine if annotation else None,
        "annotation_name": annotation.compound_name if annotation else None,
        "msi_level": annotation.msi_level if annotation else None,
        "sample_counts": dict(getattr(node, "sample_counts", {}) or {}),
    }


def _edge_payload(edge: Any) -> dict[str, Any]:
    return {
        "id": f"{edge.source}->{edge.target}",
        "source": edge.source,
        "target": edge.target,
        "modality": str(getattr(edge, "modality", "MS2")),
        "cosine": getattr(edge, "cosine", None) or getattr(edge, "ms2_cosine", None),
        "hsqc_distance": getattr(edge, "hsqc_distance", None) or getattr(
            edge, "modified_hungarian_distance", None
        ),
    }


def _iter_nodes(network: NetworkLike):
    if isinstance(network, FusedNetwork):
        return list(network.nodes.values())
    if isinstance(network, BioactivityNetwork):
        return network.network.nodes
    return network.nodes


def _iter_edges(network: NetworkLike):
    if isinstance(network, BioactivityNetwork):
        return network.network.edges
    return network.edges


def to_cytoscape_json(network: NetworkLike) -> dict[str, Any]:
    """Cytoscape.js elements plus recommended visual encoding hints."""
    return {
        "elements": {
            "nodes": [
                {"data": _node_payload(node)} for node in _iter_nodes(network)
            ],
            "edges": [{"data": _edge_payload(edge)} for edge in _iter_edges(network)],
        },
        "style_hints": dict(STYLE_HINTS),
    }


def _flatten(value: Any) -> str | float | int | None:
    if value is None or isinstance(value, (str, float, int, bool)):
        if isinstance(value, bool):
            return str(value)
        return value
    return json.dumps(value, default=str)


def to_graphml(network: NetworkLike) -> str:
    """Standard GraphML (Cytoscape Desktop / Gephi compatible).

    Complex attribute values (dicts, annotations) are serialized to JSON
    strings -- GraphML attributes must be primitives.
    """
    root = ET.Element("graphml", {"xmlns": "http://graphml.graphdrawing.org/xmlns"})
    node_attr_keys: set[str] = set()
    edge_attr_keys: set[str] = set()
    for node in _iter_nodes(network):
        node_attr_keys.update(_node_payload(node).keys())
    for edge in _iter_edges(network):
        edge_attr_keys.update(_edge_payload(edge).keys())

    keys: dict[str, str] = {}
    for index, attr in enumerate(sorted(node_attr_keys)):
        key_id = f"n{index}"
        keys[attr] = key_id
        ET.SubElement(
            root,
            "key",
            {"id": key_id, "for": "node", "attr.name": attr, "attr.type": "string"},
        )
    for index, attr in enumerate(sorted(edge_attr_keys)):
        key_id = f"e{index}"
        keys[attr] = key_id
        ET.SubElement(
            root,
            "key",
            {"id": key_id, "for": "edge", "attr.name": attr, "attr.type": "string"},
        )

    graph = ET.SubElement(root, "graph", {"edgedefault": "undirected"})
    for node in _iter_nodes(network):
        element = ET.SubElement(graph, "node", {"id": str(_node_payload(node)["id"])})
        for attr, value in _node_payload(node).items():
            if value is None:
                continue
            data = ET.SubElement(element, "data", {"key": keys[attr]})
            data.text = str(_flatten(value))
    for index, edge in enumerate(_iter_edges(network)):
        payload = _edge_payload(edge)
        element = ET.SubElement(
            graph,
            "edge",
            {"id": f"e{index}", "source": str(payload["source"]), "target": str(payload["target"])},
        )
        for attr, value in payload.items():
            if value is None or attr in ("id", "source", "target"):
                continue
            data = ET.SubElement(element, "data", {"key": keys[attr]})
            data.text = str(_flatten(value))
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def network_summary(network: NetworkLike) -> dict[str, Any]:
    """Human-readable summary plus machine-readable counts."""
    nodes = _iter_nodes(network)
    edges = _iter_edges(network)
    modality_counts: dict[str, int] = {}
    for node in nodes:
        payload = _node_payload(node)
        modality_counts[payload["modality"]] = (
            modality_counts.get(payload["modality"], 0) + 1
        )
    annotated = [
        _node_payload(node)
        for node in nodes
        if _node_payload(node)["annotation_name"]
    ]
    annotated.sort(key=lambda payload: -(payload["score"] or 0.0))
    top = [
        f"{payload['label']} (score {payload['score']:.2f})"
        if payload["score"] is not None
        else payload["label"]
        for payload in annotated[:5]
    ]
    summary = (
        f"Network has {len(nodes)} nodes and {len(edges)} edges. "
        f"Modality breakdown: {modality_counts.get('MS2', 0)} MS2-only, "
        f"{modality_counts.get('HSQC', 0)} HSQC-only, "
        f"{modality_counts.get('both', 0)} both. "
        f"Top {len(top)} annotated nodes: {', '.join(top) if top else 'none'}."
    )
    return {"summary": summary, "n_nodes": len(nodes), "n_edges": len(edges)}


def plot_network_matplotlib(network: NetworkLike, output_path: Path) -> None:
    """Static PNG (spring layout) for reports and preprints.

    Uses networkx for layout and matplotlib for rendering; both import
    lazily so this function's cost is paid only when plotting.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError as exc:  # pragma: no cover - deps installed in dev venv
        raise InvalidInputError(
            "plot_network_matplotlib requires networkx and matplotlib",
            context={"missing": str(exc)},
        ) from exc

    graph = nx.Graph()
    color_map: list[str] = []
    for node in _iter_nodes(network):
        payload = _node_payload(node)
        graph.add_node(payload["id"])
        if payload["modality"] == "both":
            color_map.append("#7b3294")
        elif payload["modality"] == "HSQC":
            color_map.append("#4393c3")
        else:
            color_map.append("#d6604d")
    for edge in _iter_edges(network):
        payload = _edge_payload(edge)
        graph.add_edge(payload["source"], payload["target"])

    positions = nx.spring_layout(graph, seed=42)
    figure, axes = plt.subplots(figsize=(8, 6))
    nx.draw_networkx_nodes(graph, positions, ax=axes, node_color=color_map, node_size=220)
    nx.draw_networkx_edges(graph, positions, ax=axes, alpha=0.5)
    nx.draw_networkx_labels(
        graph, positions, ax=axes, labels={n: str(n) for n in graph.nodes}, font_size=7
    )
    axes.set_axis_off()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(output_path, dpi=150, format="png")
    plt.close(figure)
