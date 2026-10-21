"""Parse GNPS output artifacts (GraphML, TSV) into validated Pydantic models.

The parsers validate against the GNPS2 output schema and raise
``InvalidInputError`` naming the missing column/attribute when a required
field is absent -- a truncated export should fail loudly, not parse to
``None``.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import NetworkEdge, NetworkNode

from .gnps_client import Annotation, Cluster

__all__ = [
    "GNPSNode",
    "GNPSEdge",
    "ParsedNetwork",
    "parse_graphml",
    "parse_annotations",
    "parse_clusters",
]

_GRAPHML_NS = "{http://graphml.graphdrawing.org/xmlns}"

# GNPS2 GraphML attribute names (node/edge ``data`` keys resolved by their
# ``attr.name`` declared in the header ``key`` elements).
_REQUIRED_NODE_ATTRS = ("label", "precursor mass")
_REQUIRED_EDGE_ATTRS = ("cosine score",)

# TSV schemas: GNPS2 library-search and cluster-info outputs.
_ANNOTATION_COLUMNS = (
    "#Scan#",
    "LibraryName",
    "SMILES",
    "MQScore",
    "SharedPeaks",
)
_CLUSTER_COLUMNS = ("cluster_id", "member_scans", "consensus_mz", "consensus_intensity")


class GNPSNode(NetworkNode):
    """A node in a GNPS MS2 network (extends the shared NetworkNode)."""

    node_id: str
    feature_id: str
    parent_mass: float = Field(..., ge=0.0)
    rt: float | None = None
    mz: float | None = None
    sample_counts: dict[str, float] = Field(default_factory=dict)
    annotation: Annotation | None = None
    ion_identity: str | None = None  # IIMN only


class GNPSEdge(NetworkEdge):
    """An edge in a GNPS MS2 network (extends the shared NetworkEdge)."""

    cosine: float = Field(..., ge=0.0, le=1.0)
    matched_peaks: int = Field(..., ge=0)
    forward_cosine: float | None = None
    reverse_cosine: float | None = None


class ParsedNetwork(BaseModel):
    """A GNPS network fully parsed into domain models."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GNPSNode] = Field(default_factory=list)
    edges: list[GNPSEdge] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


def parse_graphml(graphml: str) -> ParsedNetwork:
    """Parse a GNPS2 GraphML document into ``ParsedNetwork``."""
    try:
        root = ET.fromstring(graphml)
    except ET.ParseError as exc:
        raise InvalidInputError(
            "GraphML is not well-formed XML", context={"error": str(exc)}
        ) from exc

    attr_names: dict[str, str] = {}
    for key in root.findall(f"{_GRAPHML_NS}key"):
        key_id = key.get("id")
        attr_names[key_id] = key.get("attr.name", key_id)

    graph = root.find(f"{_GRAPHML_NS}graph")
    if graph is None:
        raise InvalidInputError("GraphML contains no <graph> element", context={})

    nodes: list[GNPSNode] = []
    node_attrs_seen: set[str] = set()
    for element in graph.findall(f"{_GRAPHML_NS}node"):
        data: dict[str, str] = {}
        for data_element in element.findall(f"{_GRAPHML_NS}data"):
            data[attr_names.get(data_element.get("key"), data_element.get("key", ""))] = (
                data_element.text or ""
            )
        node_attrs_seen.update(data)
        label = data.get("label", element.get("id", ""))
        annotation = None
        if data.get("compound_name"):
            annotation = Annotation(
                node_id=element.get("id", label),
                compound_name=data["compound_name"],
                smiles=data.get("SMILES"),
                library=data.get("library", "GNPS2"),
                cosine=float(data.get("cosine score", 0.0) or 0.0),
                msi_level=int(float(data.get("msi_level", 2) or 2)),
            )
        nodes.append(
            GNPSNode(
                id=element.get("id", label),
                candidate_or_feature=data.get("feature_id", label),
                modality="MS2",
                node_id=element.get("id", label),
                feature_id=data.get("feature_id", label),
                parent_mass=float(data.get("precursor mass", 0.0) or 0.0),
                rt=float(data["RT"]) if data.get("RT") else None,
                mz=float(data["mz"]) if data.get("mz") else None,
                sample_counts={
                    key: float(value)
                    for key, value in data.items()
                    if key.startswith("ABUNDANCE_")
                    and _is_float(value)
                },
                annotation=annotation,
                ion_identity=data.get("ionidentity"),
            )
        )

    missing_node_attrs = [a for a in _REQUIRED_NODE_ATTRS if a not in node_attrs_seen]
    if nodes and missing_node_attrs:
        raise InvalidInputError(
            "GraphML nodes miss required GNPS attributes",
            context={"missing": missing_node_attrs},
        )

    edges: list[GNPSEdge] = []
    edge_attrs_seen: set[str] = set()
    for index, element in enumerate(graph.findall(f"{_GRAPHML_NS}edge")):
        data: dict[str, str] = {}
        for data_element in element.findall(f"{_GRAPHML_NS}data"):
            data[attr_names.get(data_element.get("key"), data_element.get("key", ""))] = (
                data_element.text or ""
            )
        edge_attrs_seen.update(data)
        cosine = float(data.get("cosine score", 0.0) or 0.0)
        edges.append(
            GNPSEdge(
                source=element.get("source", ""),
                target=element.get("target", ""),
                modality="MS2",
                score=cosine,
                evidence_type="ms2_cosine",
                cosine=cosine,
                matched_peaks=int(float(data.get("matched peaks", 0) or 0)),
                forward_cosine=(
                    float(data["forward cosine"]) if data.get("forward cosine") else None
                ),
                reverse_cosine=(
                    float(data["reverse cosine"]) if data.get("reverse cosine") else None
                ),
            )
        )

    if edges:
        missing_edge_attrs = [a for a in _REQUIRED_EDGE_ATTRS if a not in edge_attrs_seen]
        if missing_edge_attrs:
            raise InvalidInputError(
                "GraphML edges miss required GNPS attributes",
                context={"missing": missing_edge_attrs},
            )

    return ParsedNetwork(nodes=nodes, edges=edges)


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _read_tsv(tsv: str, required: tuple[str, ...], name: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    if reader.fieldnames is None:
        raise InvalidInputError(f"{name} TSV is empty", context={})
    missing = [column for column in required if column not in reader.fieldnames]
    if missing:
        raise InvalidInputError(
            f"{name} TSV misses required columns",
            context={"missing": missing, "found": list(reader.fieldnames)},
        )
    return [dict(row) for row in reader]


def parse_annotations(tsv: str) -> list[Annotation]:
    """Parse a GNPS2 library-search TSV.

    Preserves the GNPS2 library-search fields (``#Scan#``, ``LibraryName``,
    ``MQScore``, ``SharedPeaks``); ``MQScore`` becomes the annotation
    cosine.
    """
    rows = _read_tsv(tsv, _ANNOTATION_COLUMNS, "library annotations")
    annotations: list[Annotation] = []
    for row in rows:
        try:
            annotations.append(
                Annotation(
                    node_id=f"scan={row['#Scan#']}",
                    compound_name=row["LibraryName"],
                    smiles=row.get("SMILES") or None,
                    inchikey=row.get("InChIKey") or None,
                    library=row.get("SpecOnFile", "GNPS2"),
                    cosine=float(row["MQScore"]),
                    msi_level=int(float(row.get("MSI_LEVEL", 2) or 2)),
                )
            )
        except (ValueError, KeyError) as exc:
            raise InvalidInputError(
                "Annotation row has malformed values",
                context={"row": row, "error": str(exc)},
            ) from exc
    return annotations


def parse_clusters(tsv: str) -> list[Cluster]:
    """Parse the cluster-info TSV (cluster id, members, consensus spectrum)."""
    rows = _read_tsv(tsv, _CLUSTER_COLUMNS, "cluster info")
    clusters: list[Cluster] = []
    for row in rows:
        members = [member.strip() for member in row["member_scans"].split(";") if member.strip()]
        mz_values = [float(value) for value in row["consensus_mz"].split(";") if value]
        intensities = [
            float(value) for value in row["consensus_intensity"].split(";") if value
        ]
        if len(mz_values) != len(intensities):
            raise InvalidInputError(
                "Consensus mz/intensity length mismatch",
                context={"cluster_id": row["cluster_id"]},
            )
        clusters.append(
            Cluster(
                cluster_id=row["cluster_id"],
                node_ids=[f"scan={member}" for member in members],
                consensus_spectrum_mz=mz_values,
                consensus_spectrum_intensity=intensities,
            )
        )
    return clusters
