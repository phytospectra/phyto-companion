"""Evidence graphs for candidate ranks: what supports the claim, what is
missing, and -- for novel structures -- whether the novelty claim has any
supporting evidence at all.

``RichEvidenceGraph`` extends the Step 2 ``EvidenceGraph`` with per-claim
nodes and inspection/export helpers; the parent's ``evidence``/``gaps``
fields stay populated (flattened) for Step 2 compatibility.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from phyto_shared.models import CandidateRank, EvidenceGraph, EvidenceItem

__all__ = ["RichEvidenceGraph", "build_evidence_graph"]

_CLAIM_NODE_MATCH = "match_support"
_CLAIM_NODE_GAPS = "unexplained_evidence"
_CLAIM_NODE_NOVELTY = "novelty_hypothesis"


class RichEvidenceGraph(EvidenceGraph):
    """EvidenceGraph with per-claim nodes and inspection helpers."""

    claim_nodes: dict[str, list[EvidenceItem]] = Field(default_factory=dict)

    def add_evidence(self, claim_node_id: str, evidence: EvidenceItem) -> None:
        self.claim_nodes.setdefault(claim_node_id, []).append(evidence)
        self.evidence.append(evidence)

    def get_evidence(self, claim_node_id: str) -> list[EvidenceItem]:
        return list(self.claim_nodes.get(claim_node_id, []))

    def find_gaps(self, claim_node_id: str) -> list[str]:
        """Descriptions of evidence that would strengthen the claim."""
        items = self.claim_nodes.get(claim_node_id, [])
        gaps: list[str] = []
        for item in items:
            if item.relation == "unexplained_observed_peak":
                gaps.append(
                    f"observed peak '{item.spectral_feature}' is not "
                    "explained by this structure"
                )
            elif item.relation == "unexplained_predicted_peak":
                gaps.append(
                    f"predicted peak '{item.spectral_feature}' has no "
                    "observed counterpart"
                )
        if claim_node_id == _CLAIM_NODE_NOVELTY:
            supporting = [
                item for item in items if item.relation.startswith("supports")
            ]
            if not supporting:
                gaps.append(
                    "novelty claim lacks supporting evidence: no spectral or "
                    "database evidence distinguishes this structure from "
                    "known compounds"
                )
        return gaps

    def to_dataframe(self) -> list[dict[str, Any]]:
        """Rows for export/inspection (pandas wrap downstream, per
        ``phyto_shared.AuditLog`` convention)."""
        return [
            {
                "claim_node": node_id,
                "relation": item.relation,
                "spectral_feature": item.spectral_feature,
                "source": item.source_spectrum,
                "strength": item.strength,
            }
            for node_id, items in self.claim_nodes.items()
            for item in items
        ]

    def to_cytoscape_json(self) -> dict[str, Any]:
        elements: list[dict[str, Any]] = [
            {
                "data": {
                    "id": self.node_id,
                    "label": self.claim[:60],
                    "kind": "root_claim",
                    "confidence": self.confidence,
                }
            }
        ]
        for node_id, items in self.claim_nodes.items():
            elements.append(
                {"data": {"id": node_id, "label": node_id, "kind": "claim", "n_items": len(items)}}
            )
            for index, item in enumerate(items):
                item_id = f"{node_id}#{index}"
                elements.append(
                    {
                        "data": {
                            "id": item_id,
                            "label": item.relation,
                            "kind": "evidence",
                            "strength": item.strength,
                        }
                    }
                )
                elements.append(
                    {"data": {"id": f"{item_id}->", "source": node_id, "target": item_id}}
                )
            elements.append(
                {"data": {"id": f"{node_id}->", "source": self.node_id, "target": node_id}}
            )
        return {"elements": elements}


def build_evidence_graph(rank: CandidateRank, observed: Any = None) -> RichEvidenceGraph:
    """Structure a rank's evidence items into claim nodes.

    Consumes the raw items the ranker attached (relations
    ``matched:<modality>``, ``unexplained_observed_peak``,
    ``unexplained_predicted_peak``, ``formula_mismatch``) and adds a
    novelty-hypothesis node for predicted (novel) candidates.
    """
    root_claim = (
        rank.evidence_graph.claim if rank.evidence_graph else f"identity: {rank.candidate.name}"
    )
    graph = RichEvidenceGraph(
        node_id=rank.candidate.id,
        claim=root_claim,
        evidence=[],
        confidence=rank.coverage or 0.0,
        gaps=[],
    )
    items = rank.evidence_graph.evidence if rank.evidence_graph else []
    for item in items:
        if item.relation.startswith("matched"):
            graph.add_evidence(_CLAIM_NODE_MATCH, item)
        elif item.relation.startswith("unexplained") or item.relation == "formula_mismatch":
            graph.add_evidence(_CLAIM_NODE_GAPS, item)
        else:
            graph.add_evidence(_CLAIM_NODE_MATCH, item)

    if rank.candidate.provenance == "predicted":
        graph.add_evidence(
            _CLAIM_NODE_NOVELTY,
            EvidenceItem(
                source_spectrum="database:lookup",
                spectral_feature=(
                    f"no known structure at formula {rank.candidate.formula} "
                    "matches the observed spectrum"
                ),
                relation="supports_novelty_indirectly",
                strength=round(1.0 - (rank.coverage or 0.0), 4),
            ),
        )

    # Core claim nodes always exist, even when empty, so consumers can
    # distinguish "no gaps" from "gaps not assessed".
    graph.claim_nodes.setdefault(_CLAIM_NODE_GAPS, [])
    graph.claim_nodes.setdefault(_CLAIM_NODE_MATCH, [])
    graph.gaps = (
        graph.find_gaps(_CLAIM_NODE_GAPS) + graph.find_gaps(_CLAIM_NODE_NOVELTY)
    )
    return graph
