"""Evidence graph tests: structure, gaps, Cytoscape round trip, novelty."""

from __future__ import annotations

import json

import pytest
from phyto_shared.models import EvidenceItem

from elucidation.candidate_ranker import (
    ObservedEvidence,
    rank_candidates,
)
from elucidation.evidence_graph import RichEvidenceGraph, build_evidence_graph
from elucidation.shift_predictor import MockShiftPredictor
from test_candidate_ranker import PHENOL, PHENOL_OBSERVED  # shared fixtures


def _ranked():
    return rank_candidates(PHENOL_OBSERVED, [PHENOL], predictor=MockShiftPredictor())[0]


class TestGraphStructure:
    def test_matched_candidate_has_claim_nodes(self):
        graph = build_evidence_graph(_ranked())
        assert "match_support" in graph.claim_nodes
        assert "unexplained_evidence" in graph.claim_nodes
        assert graph.claim is not None
        assert graph.confidence == pytest.approx(1.0)

    def test_unmatched_peak_lands_in_gaps_node(self):
        observed = ObservedEvidence(
            h_1d=[7.26] * 6 + [9.99],  # one unexplained extra peak
            formula="C6H6",
        )
        from test_candidate_ranker import BENZENE

        rank = rank_candidates(observed, [BENZENE], predictor=MockShiftPredictor())[0]
        graph = build_evidence_graph(rank, observed)
        gap_items = [
            item for item in graph.claim_nodes.get("unexplained_evidence", [])
            if item.relation == "unexplained_observed_peak"
        ]
        assert gap_items, "the extra 9.99 ppm peak must be reported as a gap"

    def test_database_candidate_has_no_novelty_node(self):
        graph = build_evidence_graph(_ranked())
        assert "novelty_hypothesis" not in graph.claim_nodes

    def test_novel_candidate_gets_novelty_node(self):
        rank = _ranked()
        novel_rank = rank.candidate.model_copy(update={"provenance": "predicted"})
        rank = rank.model_copy(update={"candidate": novel_rank})
        graph = build_evidence_graph(rank)
        assert "novelty_hypothesis" in graph.claim_nodes
        assert graph.find_gaps("novelty_hypothesis") == []  # indirect support added


class TestGaps:
    def test_unsupported_novelty_claim_is_flagged(self):
        graph = RichEvidenceGraph(
            node_id="x", claim="identity: novel thing",
            confidence=0.5,
        )
        graph.add_evidence("novelty_hypothesis", EvidenceItem(
            source_spectrum="none",
            spectral_feature="nothing",
            relation="unexplained_observed_peak",
            strength=0.0,
        ))
        gaps = graph.find_gaps("novelty_hypothesis")
        assert any("lacks supporting evidence" in gap for gap in gaps)

    def test_gaps_describe_missing_evidence(self):
        graph = RichEvidenceGraph(node_id="x", claim="c", confidence=0.5)
        graph.add_evidence("unexplained_evidence", EvidenceItem(
            source_spectrum="observed:hsqc",
            spectral_feature="HSQC peak #3 (5.5, 105.0)",
            relation="unexplained_observed_peak",
            strength=0.0,
        ))
        gaps = graph.find_gaps("unexplained_evidence")
        assert any("not explained" in gap for gap in gaps)


class TestExports:
    def test_cytoscape_json_round_trips(self):
        payload = build_evidence_graph(_ranked()).to_cytoscape_json()
        assert json.loads(json.dumps(payload)) == payload
        kinds = {element["data"].get("kind") for element in payload["elements"]}
        assert "root_claim" in kinds and "claim" in kinds and "evidence" in kinds

    def test_to_dataframe_rows(self):
        graph = build_evidence_graph(_ranked())
        rows = graph.to_dataframe()
        assert rows
        assert {"claim_node", "relation", "spectral_feature", "strength"} <= set(rows[0])

    def test_parent_fields_stay_populated(self):
        graph = build_evidence_graph(_ranked())
        # Step 2 compatibility: flattened evidence + gap strings
        assert len(graph.evidence) == sum(
            len(items) for items in graph.claim_nodes.values()
        )
        assert isinstance(graph.gaps, list)
