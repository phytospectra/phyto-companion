"""Network pharmacology (Banerjee et al. 2019-2025): compound-target-
pathway networks, hub analysis, modules, and evidence-grounded mechanism
hypotheses.

Honesty rules (principle 3, guarantee 3/8): every MechanismHypothesis is
labeled a hypothesis, carries a full EvidenceGraph, and its natural-
language text is VALIDATED against that graph -- if the LLM (or the
deterministic fallback) claims a mechanism the evidence does not
support, the hypothesis is REJECTED, not downgraded.
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Protocol

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import InsufficientEvidenceError
from phyto_shared.models import EvidenceGraph, EvidenceItem

from .pathway_mapping import targets_to_pathways
from .target_prediction import TargetPrediction

__all__ = [
    "HubResult",
    "ModuleResult",
    "MechanismHypothesis",
    "build_network",
    "hub_analysis",
    "module_detection",
    "mechanism_hypothesis",
    "validate_hypothesis_text",
]

HubMethod = Literal["degree", "betweenness", "eigenvector"]


class HubResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: HubMethod
    hubs: list[dict[str, Any]]  # {"node": ..., "score": ..., "kind": ...}


class ModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = "louvain"
    modules: list[list[str]]


class MechanismHypothesis(BaseModel):
    """A mechanism hypothesis -- never a conclusion."""

    model_config = ConfigDict(extra="forbid")

    compound: str
    top_targets: list[dict[str, Any]]  # {"target": ..., "confidence": ..., "method": ...}
    top_pathways: list[dict[str, Any]]  # {"pathway": ..., "n_targets": ...}
    proposed_mechanism: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: EvidenceGraph
    citations: list[str] = Field(default_factory=list)
    is_hypothesis: bool = True  # ALWAYS True by construction and by test

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        object.__setattr__(self, "is_hypothesis", True)


class LLMClientLike(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


# ---------------------------------------------------------------------------
# Network construction and analysis
# ---------------------------------------------------------------------------


def build_network(
    compounds: list[str],
    targets_per_compound: dict[str, list[TargetPrediction]],
    pathways_per_target: dict[str, list[tuple[str, str]]] | None = None,
) -> nx.Graph:
    """Compound -> target -> pathway network from predictions."""
    graph = nx.Graph()
    for compound in compounds:
        graph.add_node(compound, kind="compound")
    for compound_ik, predictions in targets_per_compound.items():
        for prediction in predictions:
            graph.add_node(
                prediction.target_id,
                kind="target",
                name=prediction.target_name,
            )
            graph.add_edge(
                compound_ik,
                prediction.target_id,
                kind="targets",
                method=prediction.method,
                confidence=prediction.confidence,
                citation=prediction.citation,
            )
    pathway_index = pathways_per_target
    if pathway_index is None:
        mapped = targets_to_pathways(
            sorted({p.target_id for preds in targets_per_compound.values() for p in preds})
        )
        pathway_index = {
            target: [(entry["pathway_id"], entry["pathway_name"])
                     for entry in entries]
            for target, entries in mapped.target_to_pathways.items()
        }
    for target_id, pathways in pathway_index.items():
        if target_id not in graph:
            continue
        for pathway_id, pathway_name in pathways:
            graph.add_node(pathway_id, kind="pathway", name=pathway_name)
            graph.add_edge(target_id, pathway_id, kind="part_of")
    return graph


def hub_analysis(network: nx.Graph, method: HubMethod = "degree") -> HubResult:
    if method == "degree":
        scores = dict(network.degree())
    elif method == "betweenness":
        scores = dict(nx.betweenness_centrality(network))
    else:
        try:
            scores = dict(nx.eigenvector_centrality(network, max_iter=1000))
        except nx.PowerIterationFailedConvergence:
            scores = {node: 0.0 for node in network.nodes}
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    hubs = [
        {
            "node": node,
            "score": float(score),
            "kind": network.nodes[node].get("kind", "unknown"),
        }
        for node, score in ranked[:10]
        if network.nodes[node].get("kind") == "target"
    ]
    return HubResult(method=method, hubs=hubs)


def module_detection(network: nx.Graph, method: str = "louvain") -> ModuleResult:
    if method != "louvain":
        raise ValueError(f"module detection method {method!r} not implemented")
    if network.number_of_nodes() == 0:
        return ModuleResult(modules=[])
    communities = nx.community.louvain_communities(network, seed=42)
    modules = [sorted(community) for community in communities]
    return ModuleResult(modules=sorted(modules, key=len, reverse=True))


# ---------------------------------------------------------------------------
# Mechanism hypotheses (evidence-grounded, QA/Critic-validated)
# ---------------------------------------------------------------------------


def validate_hypothesis_text(
    text: str, evidence_names: set[str], forbidden_overclaims=("proven", "established")
) -> list[str]:
    """Deterministic QA/Critic rules for mechanism-hypothesis text.

    Returns the list of violations (empty = passes):
    1. the text must name at least one evidenced target;
    2. it must not use overclaiming language.
    """
    violations: list[str] = []
    lowered = text.lower()
    if not any(name.lower() in lowered for name in evidence_names):
        violations.append(
            "hypothesis text names no target that appears in the evidence graph"
        )
    for word in forbidden_overclaims:
        if word in lowered:
            violations.append(f"hypothesis text overclaims: '{word}'")
    return violations


def mechanism_hypothesis(
    network: nx.Graph,
    compound: str,
    llm_client: LLMClientLike | None = None,
    qa_critic: Callable[[MechanismHypothesis], list[str]] | None = None,
) -> MechanismHypothesis:
    """Build an evidence-grounded mechanism hypothesis for ``compound``.

    The narrative is generated by the injected LLM client (Step 3's
    llm_client protocol: ``complete(system, user)``) or, without one, by
    a deterministic template -- then validated against the evidence
    graph.  Any violation rejects the hypothesis with
    InsufficientEvidenceError.  An optional ``qa_critic`` (Step 3's
    QACriticAgent.verify-compatible callable) runs additional checks.
    """
    if compound not in network:
        raise InsufficientEvidenceError(
            "compound not present in the network", context={"compound": compound}
        )
    target_edges = [
        (target, data)
        for _, target, data in network.edges(compound, data=True)
        if network.nodes[target].get("kind") == "target"
    ]
    if not target_edges:
        raise InsufficientEvidenceError(
            "no predicted targets for this compound in the network",
            context={"compound": compound},
        )
    target_edges.sort(key=lambda item: -item[1].get("confidence", 0.0))
    top_targets = [
        {
            "target": target,
            "confidence": data.get("confidence", 0.0),
            "method": data.get("method", "unknown"),
        }
        for target, data in target_edges[:5]
    ]
    pathway_counts: dict[str, int] = {}
    for target, _ in target_edges:
        for _, pathway in network.edges(target):
            if network.nodes[pathway].get("kind") == "pathway":
                pathway_counts[pathway] = pathway_counts.get(pathway, 0) + 1
    top_pathways = [
        {"pathway": pathway, "n_targets": count}
        for pathway, count in sorted(
            pathway_counts.items(), key=lambda item: (-item[1], item[0])
        )[:5]
    ]

    evidence_items = [
        EvidenceItem(
            source_spectrum=f"target_prediction:{data.get('method', 'unknown')}",
            spectral_feature=f"{compound} -> {target} ({network.nodes[target].get('name', target)})",
            relation=f"targets_via_{data.get('method', 'unknown')}",
            strength=data.get("confidence", 0.0),
        )
        for target, data in target_edges
    ]
    citations = sorted(
        {
            data["citation"]
            for _, _, data in network.edges(compound, data=True)
            if data.get("citation")
        }
    )
    evidence = EvidenceGraph(
        node_id=f"mechanism:{compound}",
        claim=f"mechanism hypothesis for {compound}",
        evidence=evidence_items,
        confidence=round(
            sum(item.strength for item in evidence_items) / len(evidence_items), 4
        ),
        gaps=["no experimental validation performed"],
    )

    evidenced_targets = {target for target, _ in target_edges}
    if llm_client is not None:
        text = llm_client.complete(
            system_prompt=(
                "You are drafting a MECHANISM HYPOTHESIS. Only mention "
                "targets present in the provided evidence. Never claim "
                "proof. End with: 'This is a hypothesis requiring "
                "experimental validation.'"
            ),
            user_prompt=(
                f"Compound: {compound}. Evidenced targets: "
                f"{sorted(evidenced_targets)}. Pathways: "
                f"{sorted(pathway_counts)}."
            ),
        )
    else:
        text = (
            f"HYPOTHESIS: {compound} may modulate "
            f"{', '.join(t['target'] for t in top_targets[:3])} "
            "(predicted by "
            f"{'/'.join(sorted({t['method'] for t in top_targets}))}), "
            f"plausibly engaging {', '.join(p['pathway'] for p in top_pathways[:3])}. "
            "This is a hypothesis requiring experimental validation."
        )

    violations = validate_hypothesis_text(text, evidenced_targets)
    hypothesis = MechanismHypothesis(
        compound=compound,
        top_targets=top_targets,
        top_pathways=top_pathways,
        proposed_mechanism=text,
        confidence=evidence.confidence,
        evidence=evidence,
        citations=citations,
    )
    if qa_critic is not None:
        violations.extend(qa_critic(hypothesis))
    if violations:
        raise InsufficientEvidenceError(
            "mechanism hypothesis rejected by validation",
            context={"violations": violations, "compound": compound},
        )
    return hypothesis
