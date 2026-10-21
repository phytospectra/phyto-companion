"""Knowledge graph: compound-target-pathway-disease-traditional use.

KEY INVARIANT (ADR-0016): PREDICTED_TARGET and REPORTED_TARGET are
SEPARATE edge types -- a computational prediction is never conflated
with a literature report.  The local plane uses an in-memory networkx
graph; production uses Neo4j (the interface is identical).
"""

from __future__ import annotations

import hashlib
from typing import Any

import networkx as nx
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import CloudServiceError, InvalidInputError

__all__ = [
    "Target",
    "Pathway",
    "KnowledgeGraph",
    "NetworkxKnowledgeGraph",
    "PREDICTED_TARGET",
    "REPORTED_TARGET",
]

PREDICTED_TARGET = "PREDICTED_TARGET"
REPORTED_TARGET = "REPORTED_TARGET"
EDGE_TYPES = (PREDICTED_TARGET, REPORTED_TARGET, "PART_OF", "ASSOCIATED_WITH", "REPORTED_IN")


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    name: str
    uniprot: str | None = None
    organism: str | None = None
    edge_type: str  # PREDICTED_TARGET or REPORTED_TARGET
    method: str | None = None
    confidence: float | None = None
    citation: str | None = None


class Pathway(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathway_id: str
    name: str
    source: str


class KnowledgeGraph:
    """Storage-agnostic interface (networkx locally, Neo4j in cloud)."""

    def add_compound(self, ik: str, name: str, formula: str = "", compound_class: str = "") -> None:
        raise NotImplementedError

    def add_target(self, target_id: str, name: str, uniprot: str | None = None, organism: str = "Homo sapiens") -> None:
        raise NotImplementedError

    def add_pathway(self, pathway_id: str, name: str, source: str = "kegg") -> None:
        raise NotImplementedError

    def add_traditional_use(self, plant_species: str, organ: str, use: str, source: str) -> str:
        raise NotImplementedError

    def add_compound_target(
        self,
        compound_ik: str,
        target_id: str,
        *,
        reported: bool,
        method: str | None = None,
        confidence: float | None = None,
        citation: str | None = None,
    ) -> None:
        """``reported=False`` creates a PREDICTED_TARGET edge;
        ``reported=True`` a REPORTED_TARGET edge.  Never both on one call."""
        raise NotImplementedError

    def add_target_pathway(self, target_id: str, pathway_id: str) -> None:
        raise NotImplementedError

    def query_targets(self, compound_ik: str) -> list[Target]:
        raise NotImplementedError

    def query_pathways(self, target_id: str) -> list[Pathway]:
        raise NotImplementedError

    def query_compound_context(self, compound_ik: str) -> dict[str, Any]:
        raise NotImplementedError

    def import_chembl(self, compound_ik: str) -> int:
        raise NotImplementedError

    def import_kegg(self) -> int:
        raise NotImplementedError

    def import_reactome(self) -> int:
        raise NotImplementedError

    def import_lotus(self, compound_ik: str) -> int:
        raise NotImplementedError


class NetworkxKnowledgeGraph(KnowledgeGraph):
    """In-memory mock with the full schema and idempotent imports.

    Imports record a version hash in the ``__import_metadata`` node so
    re-imports are detectable and version-controlled.
    """

    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()
        self.graph.add_node(
            "__import_metadata",
            kind="metadata",
            imports={},
        )

    # -- nodes -------------------------------------------------------------

    def add_compound(self, ik, name, formula="", compound_class=""):
        self.graph.add_node(
            ik, kind="compound", name=name, formula=formula,
            compound_class=compound_class,
        )

    def add_target(self, target_id, name, uniprot=None, organism="Homo sapiens"):
        self.graph.add_node(
            target_id, kind="target", name=name, uniprot=uniprot, organism=organism
        )

    def add_pathway(self, pathway_id, name, source="kegg"):
        self.graph.add_node(pathway_id, kind="pathway", name=name, source=source)

    def add_traditional_use(self, plant_species, organ, use, source):
        use_id = "use-" + hashlib.sha256(
            f"{plant_species}|{organ}|{use}".encode()
        ).hexdigest()[:12]
        self.graph.add_node(
            use_id,
            kind="traditional_use",
            plant_species=plant_species,
            organ=organ,
            use=use,
            source=source,
        )
        return use_id

    # -- edges -------------------------------------------------------------

    def add_compound_target(
        self, compound_ik, target_id, *, reported, method=None,
        confidence=None, citation=None,
    ):
        if reported and method is not None:
            raise InvalidInputError(
                "REPORTED_TARGET edges cite literature, not methods",
                context={"compound_ik": compound_ik, "method": method},
            )
        if not reported and confidence is None:
            raise InvalidInputError(
                "PREDICTED_TARGET edges require a confidence value",
                context={"compound_ik": compound_ik},
            )
        self.graph.add_edge(
            compound_ik,
            target_id,
            REPORTED_TARGET if reported else PREDICTED_TARGET,
            method=method,
            confidence=confidence,
            citation=citation,
        )

    def add_target_pathway(self, target_id, pathway_id):
        self.graph.add_edge(target_id, pathway_id, "PART_OF")

    def add_compound_traditional_use(self, compound_ik: str, use_id: str):
        self.graph.add_edge(compound_ik, use_id, "REPORTED_IN")

    # -- queries -----------------------------------------------------------

    def query_targets(self, compound_ik) -> list[Target]:
        targets: list[Target] = []
        for _, target_id, key, data in self.graph.out_edges(
            compound_ik, keys=True, data=True
        ):
            if key not in EDGE_TYPES[:2]:
                continue
            node = self.graph.nodes[target_id]
            targets.append(
                Target(
                    target_id=target_id,
                    name=node.get("name", target_id),
                    uniprot=node.get("uniprot"),
                    organism=node.get("organism"),
                    edge_type=key,
                    method=data.get("method"),
                    confidence=data.get("confidence"),
                    citation=data.get("citation"),
                )
            )
        targets.sort(key=lambda t: (t.edge_type, t.target_id))
        return targets

    def query_pathways(self, target_id) -> list[Pathway]:
        pathways = []
        for _, pathway_id, key in self.graph.out_edges(target_id, keys=True):
            if key != "PART_OF":
                continue
            node = self.graph.nodes[pathway_id]
            pathways.append(
                Pathway(
                    pathway_id=pathway_id,
                    name=node.get("name", pathway_id),
                    source=node.get("source", "kegg"),
                )
            )
        return sorted(pathways, key=lambda p: p.pathway_id)

    def query_compound_context(self, compound_ik) -> dict[str, Any]:
        targets = self.query_targets(compound_ik)
        pathways: list[Pathway] = []
        seen: set[str] = set()
        for target in targets:
            for pathway in self.query_pathways(target.target_id):
                if pathway.pathway_id not in seen:
                    seen.add(pathway.pathway_id)
                    pathways.append(pathway)
        traditional_uses = [
            self.graph.nodes[use_id]
            for _, use_id, key in self.graph.out_edges(compound_ik, keys=True)
            if key == "REPORTED_IN"
        ]
        return {
            "compound": compound_ik,
            "targets": [t.model_dump() for t in targets],
            "pathways": [p.model_dump() for p in pathways],
            "traditional_uses": traditional_uses,
            "n_reported_targets": sum(
                1 for t in targets if t.edge_type == REPORTED_TARGET
            ),
            "n_predicted_targets": sum(
                1 for t in targets if t.edge_type == PREDICTED_TARGET
            ),
        }

    # -- imports (idempotent, version-controlled) ---------------------------

    def _record_import(self, source: str, payload: str) -> bool:
        version = hashlib.sha256(payload.encode()).hexdigest()[:16]
        imports = self.graph.nodes["__import_metadata"]["imports"]
        if imports.get(source) == version:
            return False  # already imported this exact version
        imports[source] = version
        return True

    def import_chembl(self, compound_ik: str) -> int:
        payload = f"chembl|{compound_ik}"
        if not self._record_import("chembl", payload):
            return 0
        self.add_compound(compound_ik, name=compound_ik)
        reported = {"P23219": ("COX-1 / PTGS1", "PMID:1675914"),
                    "P35354": ("COX-2 / PTGS2", "PMID:11914285")}
        for target_id, (name, citation) in reported.items():
            self.add_target(target_id, name, uniprot=target_id)
            self.add_compound_target(
                compound_ik, target_id, reported=True, citation=citation
            )
        return len(reported)

    def import_kegg(self) -> int:
        if not self._record_import("kegg", "kegg-static-v1"):
            return 0
        pathways = {
            "map00590": "Arachidonic acid metabolism",
            "map01100": "Metabolic pathways",
        }
        for pathway_id, name in pathways.items():
            self.add_pathway(pathway_id, name, source="kegg")
        self.add_target_pathway("P23219", "map00590")
        self.add_target_pathway("P35354", "map00590")
        self.add_target_pathway("P35354", "map01100")
        return len(pathways)

    def import_reactome(self) -> int:
        if not self._record_import("reactome", "reactome-static-v1"):
            return 0
        self.add_pathway("R-HSA-2162123", "Cytochrome P450 - arranged by substrate type", source="reactome")
        self.add_target_pathway("P23219", "R-HSA-2162123")
        return 1

    def import_lotus(self, compound_ik: str) -> int:
        payload = f"lotus|{compound_ik}"
        if not self._record_import(payload, payload):
            return 0
        use_id = self.add_traditional_use(
            "Hypericum perforatum", "aerial parts",
            "traditional wound healing", "LOTUS (mock)"
        )
        self.add_compound_traditional_use(compound_ik, use_id)
        return 1


def get_knowledge_graph() -> KnowledgeGraph:
    """networkx locally; Neo4j wiring lands with deployment (Step 8)."""
    import os

    if os.environ.get("PHYTO_ENV") == "cloud" and os.environ.get("NEO4J_URI"):
        raise CloudServiceError(
            "Neo4j backend is wired at deployment time (Step 8); the "
            "local plane uses NetworkxKnowledgeGraph",
            context={},
        )
    return NetworkxKnowledgeGraph()
