"""Network pharmacology: hubs, modules, mechanism hypotheses, QA rejection."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InsufficientEvidenceError
from pharmacology.network_pharmacology import (
    build_network,
    hub_analysis,
    mechanism_hypothesis,
    module_detection,
    validate_hypothesis_text,
)
from pharmacology.target_prediction import TargetPrediction

Q = "REFJWTPEDVJJIY-UHFFFAOYSA-N"
K = "VNEYFRNUTPLQCC-UHFFFAOYSA-N"
C = "RYYVLZVUVIJVGH-UHFFFAOYSA-N"


def _prediction(compound: str, target: str, name: str, confidence: float, method: str = "similarity"):
    return TargetPrediction(
        compound_ik=compound,
        target_id=target,
        target_name=name,
        method=method,  # type: ignore[arg-type]
        confidence=confidence,
        confidence_basis="test",
        citation="PMID:11914285",
    )


def _network():
    predictions = {
        Q: [
            _prediction(Q, "P35354", "COX-2 / PTGS2", 0.72),
            _prediction(Q, "P23219", "COX-1 / PTGS1", 0.61),
        ],
        K: [
            _prediction(K, "P35354", "COX-2 / PTGS2", 0.35),
        ],
        C: [
            _prediction(C, "P00533", "EGFR", 0.18),
        ],
    }
    return build_network([Q, K, C], predictions)


class TestBuildNetwork:
    def test_topology(self):
        graph = _network()
        assert graph.has_edge(Q, "P35354")
        assert graph.has_edge("P35354", "map00590")
        assert graph.nodes["map00590"]["kind"] == "pathway"


class TestHubs:
    def test_degree_hubs_are_targets_only(self):
        result = hub_analysis(_network(), "degree")
        assert result.hubs
        assert all(hub["kind"] == "target" for hub in result.hubs)
        assert result.hubs[0]["node"] == "P35354"  # shared by two compounds

    def test_betweenness_and_eigenvector_run(self):
        graph = _network()
        for method in ("betweenness", "eigenvector"):
            result = hub_analysis(graph, method)  # type: ignore[arg-type]
            assert result.method == method
            assert all(hub["kind"] == "target" for hub in result.hubs)


class TestModules:
    def test_louvain_partitions(self):
        result = module_detection(_network())
        assert result.modules
        all_nodes = {node for module in result.modules for node in module}
        assert all_nodes == set(_network().nodes)

    def test_unknown_method_rejected(self):
        with pytest.raises(ValueError):
            module_detection(_network(), "leiden")


class TestMechanismHypothesis:
    def test_hypothesis_is_labeled_and_evidenced(self):
        graph = _network()
        hypothesis = mechanism_hypothesis(graph, Q)
        assert hypothesis.is_hypothesis is True
        assert hypothesis.evidence.evidence
        assert hypothesis.evidence.node_id.startswith("mechanism:")
        assert hypothesis.top_targets
        assert hypothesis.top_targets[0]["target"] == "P35354"
        assert hypothesis.citations == ["PMID:11914285"]
        assert "HYPOTHESIS" in hypothesis.proposed_mechanism
        assert "experimental validation" in hypothesis.proposed_mechanism

    def test_top_targets_sorted_by_confidence(self):
        hypothesis = mechanism_hypothesis(_network(), Q)
        confidences = [t["confidence"] for t in hypothesis.top_targets]
        assert confidences == sorted(confidences, reverse=True)

    def test_compound_without_targets_rejected(self):
        graph = _network()
        graph.add_node("NO-TARGETS", kind="compound")
        with pytest.raises(InsufficientEvidenceError):
            mechanism_hypothesis(graph, "NO-TARGETS")

    def test_unknown_compound_rejected(self):
        with pytest.raises(InsufficientEvidenceError):
            mechanism_hypothesis(_network(), "ghost")

    def test_llm_text_without_evidenced_target_is_rejected(self):
        class BadLLM:
            def complete(self, system_prompt, user_prompt):
                return "The compound treats everything via an unrelated mechanism."

        with pytest.raises(InsufficientEvidenceError) as excinfo:
            mechanism_hypothesis(_network(), Q, llm_client=BadLLM())
        assert "names no target" in str(excinfo.value.context["violations"])

    def test_llm_overclaim_rejected(self):
        class OverclaimingLLM:
            def complete(self, system_prompt, user_prompt):
                return "It is proven that P35354 is inhibited."

        with pytest.raises(InsufficientEvidenceError) as excinfo:
            mechanism_hypothesis(_network(), Q, llm_client=OverclaimingLLM())
        assert any("overclaims" in v for v in excinfo.value.context["violations"])

    def test_llm_honest_text_passes(self):
        class GoodLLM:
            def complete(self, system_prompt, user_prompt):
                return (
                    "HYPOTHESIS: this compound may modulate P35354. "
                    "This is a hypothesis requiring experimental validation."
                )

        hypothesis = mechanism_hypothesis(_network(), Q, llm_client=GoodLLM())
        assert "P35354" in hypothesis.proposed_mechanism

    def test_external_qa_critic_can_reject(self):
        def reject(_hypothesis):
            return ["external reviewer: evidence too weak for any claim"]

        with pytest.raises(InsufficientEvidenceError):
            mechanism_hypothesis(_network(), Q, qa_critic=reject)


class TestValidationRules:
    def test_named_target_required(self):
        violations = validate_hypothesis_text("nothing relevant here", {"P35354"})
        assert violations

    def test_overclaim_detected(self):
        violations = validate_hypothesis_text(
            "P35354 is an established target", {"P35354"}
        )
        assert any("overclaims" in v for v in violations)

    def test_clean_text_passes(self):
        assert validate_hypothesis_text(
            "P35354 may be involved", {"P35354"}
        ) == []
