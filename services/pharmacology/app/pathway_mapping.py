"""Map targets to pathways and build compound-target-pathway networks."""

from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import InvalidInputError

__all__ = [
    "PathwayResult",
    "MOCK_TARGET_PATHWAYS",
    "targets_to_pathways",
    "compound_target_pathway_network",
]

# Mock target->pathway knowledge for the local plane; every pathway
# carries an explicit KEGG-style id (citations ride on the network edge).
MOCK_TARGET_PATHWAYS: dict[str, list[tuple[str, str]]] = {
    "P23219": [("map00590", "Arachidonic acid metabolism")],
    "P35354": [("map00590", "Arachidonic acid metabolism"), ("map01100", "Metabolic pathways")],
    "P00533": [("map01521", "EGFR tyrosine kinase inhibitor resistance")],
}


class PathwayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_to_pathways: dict[str, list[dict[str, str]]]
    source: str


def targets_to_pathways(
    target_ids: list[str], source: str = "kegg"
) -> PathwayResult:
    """Map target ids to pathways (KEGG in the cloud, mock locally).

    Source 'kegg' resolves ids as UniProt accessions against the mock
    table here; the cloud implementation joins through the KEGG REST
    API at deployment time (Step 8).
    """
    mapping: dict[str, list[dict[str, str]]] = {}
    for target_id in target_ids:
        pathways = MOCK_TARGET_PATHWAYS.get(target_id, [])
        mapping[target_id] = [
            {"pathway_id": pid, "pathway_name": name, "source": source}
            for pid, name in pathways
        ]
    return PathwayResult(target_to_pathways=mapping, source=source)


def compound_target_pathway_network(
    compounds: list[str],
    targets: dict[str, list[str]],
    pathways: dict[str, list[tuple[str, str]]],
) -> nx.Graph:
    """Tripartite compound -> target -> pathway network.

    ``targets``: compound_ik -> target ids; ``pathways``: target_id ->
    (pathway_id, pathway_name) pairs.
    """
    graph = nx.Graph()
    for compound in compounds:
        graph.add_node(compound, kind="compound")
    known_compounds = set(compounds)
    for compound_ik, target_ids in targets.items():
        if compound_ik not in known_compounds:
            raise InvalidInputError(
                "targets given for an unknown compound",
                context={"compound_ik": compound_ik},
            )
        for target_id in target_ids:
            graph.add_node(target_id, kind="target")
            graph.add_edge(compound_ik, target_id, kind="targets")
        for target_id in target_ids:
            for pathway_id, pathway_name in pathways.get(target_id, []):
                graph.add_node(pathway_id, kind="pathway", name=pathway_name)
                graph.add_edge(target_id, pathway_id, kind="part_of")
    return graph
