"""Knowledge graph: schema, PREDICTED vs REPORTED separation, idempotency."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from pharmacology.knowledge_graph import (
    PREDICTED_TARGET,
    REPORTED_TARGET,
    NetworkxKnowledgeGraph,
)

IK = "REFJWTPEDVJJIY-UHFFFAOYSA-N"


@pytest.fixture()
def kg() -> NetworkxKnowledgeGraph:
    graph = NetworkxKnowledgeGraph()
    graph.add_compound(IK, "quercetin", formula="C15H10O7")
    graph.add_target("P35354", "COX-2 / PTGS2", uniprot="P35354")
    graph.add_target("P23219", "COX-1 / PTGS1", uniprot="P23219")
    graph.add_pathway("map00590", "Arachidonic acid metabolism")
    return graph


class TestEdgeSeparation:
    def test_predicted_and_reported_are_distinct_edge_types(self, kg):
        kg.add_compound_target(
            IK, "P35354", reported=True, citation="PMID:11914285"
        )
        kg.add_compound_target(
            IK, "P23219", reported=False, method="similarity", confidence=0.72
        )
        targets = kg.query_targets(IK)
        assert len(targets) == 2
        types = {t.target_id: t.edge_type for t in targets}
        assert types["P35354"] == REPORTED_TARGET
        assert types["P23219"] == PREDICTED_TARGET

    def test_predicted_requires_confidence(self, kg):
        with pytest.raises(InvalidInputError):
            kg.add_compound_target(IK, "P35354", reported=False, method="docking")

    def test_reported_rejects_method_field(self, kg):
        # a literature report has no computational method
        with pytest.raises(InvalidInputError):
            kg.add_compound_target(
                IK, "P35354", reported=True, method="similarity", citation="PMID:1"
            )

    def test_context_counts_report_both_separately(self, kg):
        kg.add_compound_target(IK, "P35354", reported=True, citation="PMID:1")
        kg.add_compound_target(
            IK, "P23219", reported=False, method="similarity", confidence=0.6
        )
        context = kg.query_compound_context(IK)
        assert context["n_reported_targets"] == 1
        assert context["n_predicted_targets"] == 1


class TestQueries:
    def test_query_pathways(self, kg):
        kg.add_target_pathway("P35354", "map00590")
        pathways = kg.query_pathways("P35354")
        assert [p.pathway_id for p in pathways] == ["map00590"]
        assert pathways[0].source == "kegg"

    def test_compound_context_includes_traditional_use(self, kg):
        use_id = kg.add_traditional_use(
            "Hypericum perforatum", "aerial parts", "wound healing", "LOTUS"
        )
        kg.add_compound_traditional_use(IK, use_id)
        kg.add_compound_target(IK, "P35354", reported=True, citation="PMID:1")
        kg.add_target_pathway("P35354", "map00590")
        context = kg.query_compound_context(IK)
        assert context["traditional_uses"]
        assert context["traditional_uses"][0]["plant_species"] == "Hypericum perforatum"
        assert context["pathways"][0]["pathway_id"] == "map00590"

    def test_unknown_compound_context_is_empty_not_error(self, kg):
        context = kg.query_compound_context("UNKNOWN")
        assert context["targets"] == []
        assert context["n_predicted_targets"] == 0


class TestImports:
    def test_chembl_import_creates_reported_edges(self):
        kg = NetworkxKnowledgeGraph()
        imported = kg.import_chembl(IK)
        assert imported == 2
        targets = kg.query_targets(IK)
        assert all(t.edge_type == REPORTED_TARGET for t in targets)
        assert all(t.citation for t in targets)

    def test_imports_are_idempotent(self):
        kg = NetworkxKnowledgeGraph()
        first = kg.import_chembl(IK)
        second = kg.import_chembl(IK)
        assert first == 2
        assert second == 0  # same version hash: nothing re-imported

    def test_kegg_and_reactome_imports(self):
        kg = NetworkxKnowledgeGraph()
        assert kg.import_kegg() == 2
        assert kg.import_kegg() == 0
        assert kg.import_reactome() == 1
        assert kg.import_reactome() == 0

    def test_lotus_import(self):
        kg = NetworkxKnowledgeGraph()
        assert kg.import_lotus(IK) == 1
        assert kg.import_lotus(IK) == 0
        context = kg.query_compound_context(IK)
        assert context["traditional_uses"]
