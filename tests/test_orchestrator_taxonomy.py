"""End-to-end identify-workflow tests: taxonomy tasks, reranking, skips.

Runs the real planner + engine + mock executor (local plane): the mock
elucidation tool delegates to the REAL taxonomy reranker, so these tests
exercise the ADR-0052 chain from sample metadata to evidence graph.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from phyto_shared.constants import ObservationModality
from phyto_shared.schemas import SampleMetadata

from orchestrator.audit_logger import ChainedAuditLogger
from orchestrator.planner_agent import PlannerAgent, TaskStatus, WorkflowEngine
from orchestrator.qa_critic_agent import QACriticAgent
from orchestrator.tool_registry import MockToolExecutor, build_default_registry
from orchestrator.tool_tiering import ExecutionContext, HumanCheckpoint


@pytest.fixture()
def registry():
    return build_default_registry()


@pytest.fixture()
def engine(registry, tmp_path):
    return WorkflowEngine(
        registry,
        MockToolExecutor(registry),
        ChainedAuditLogger(tmp_path / "audit.jsonl"),
        qa_critic=QACriticAgent(),
    )


@pytest.fixture()
def planner(registry):
    return PlannerAgent(registry)


_BOESENBERGIA = SampleMetadata(
    species="Boesenbergia rotunda", family="Zingiberaceae",
    extraction_solvent="methanol",
)


class TestPlanning:
    def test_identify_with_taxon_metadata_plans_taxonomy_tasks_in_order(
        self, planner
    ) -> None:
        graph = planner.plan(
            run_id="r1", workflow="identify", dry_run=True,
            sample_metadata=_BOESENBERGIA,
        )
        ids = [task.id for task in graph.tasks]
        assert "resolve-taxonomy" in ids and "build-taxon-prior" in ids
        resolve = graph.task_by_id("resolve-taxonomy")
        prior = graph.task_by_id("build-taxon-prior")
        elucidate = graph.task_by_id("elucidate")
        # ingestion -> resolution -> prior -> elucidation (ADR-0052)
        assert "ingest-peaks" in resolve.depends_on
        assert "resolve-taxonomy" in prior.depends_on
        assert "build-taxon-prior" in elucidate.depends_on
        assert elucidate.inputs["apply_taxon_prior"] is True
        assert elucidate.inputs["solvent"] == "methanol"
        # a genus-only hint is enough to plan the prior too
        genus_only = planner.plan(
            run_id="r1b", workflow="identify", dry_run=True,
            sample_metadata=SampleMetadata(genus="Curcuma"),
        )
        assert "build-taxon-prior" in [t.id for t in genus_only.tasks]

    def test_identify_without_metadata_omits_taxonomy_tasks_and_logs(
        self, planner, caplog
    ) -> None:
        with caplog.at_level(logging.INFO, logger="orchestrator.planner_agent"):
            graph = planner.plan(
                run_id="r2", workflow="identify", dry_run=True, sample_metadata=None,
            )
        ids = [task.id for task in graph.tasks]
        assert "resolve-taxonomy" not in ids and "build-taxon-prior" not in ids
        elucidate = graph.task_by_id("elucidate")
        assert "build-taxon-prior" not in elucidate.depends_on
        assert elucidate.inputs["apply_taxon_prior"] is False
        assert any(
            "taxon prior skipped: no metadata" in record.message
            for record in caplog.records
        )


class TestEndToEnd:
    def _run(self, planner, engine, metadata) -> None:
        graph = planner.plan(
            run_id="run-tax", workflow="identify", dry_run=True,
            modalities=frozenset({ObservationModality.HSQC}),
            sample_metadata=metadata,
        )
        context = ExecutionContext(
            run_id="run-tax", dry_run=True, verification_available=True,
            human_in_the_loop_approved=True,
            approved_checkpoints=frozenset({HumanCheckpoint.BEFORE_CONFIRMED_CLAIM}),
            actor="test",
        )
        result = engine.run(graph, context)
        return graph, result

    def test_reranker_runs_and_evidence_graph_has_taxon_item(
        self, planner, engine
    ) -> None:
        graph, result = self._run(planner, engine, _BOESENBERGIA)
        assert result.status == "complete", [
            (t.id, t.error) for t in graph.tasks if t.error
        ]
        # Artifacts: the resolution and the prior are stored as task outputs
        resolution = graph.task_by_id("resolve-taxonomy").output
        assert resolution["resolution"]["canonical_name"] == "Boesenbergia rotunda"
        prior_output = graph.task_by_id("build-taxon-prior").output
        assert prior_output["n_species"] >= 1
        elucidate = graph.task_by_id("elucidate")
        assert elucidate.status == TaskStatus.COMPLETE
        output = elucidate.output
        assert output["taxon_prior_applied"] is True
        top = output["ranks"][0]
        assert top["taxon_prior_match"] == "species"
        assert top["taxon_prior_provenance"], "top candidate must carry provenance"
        relations = [
            e["relation"] for e in output["top_evidence_graph"]["evidence"]
        ]
        assert "taxon_prior" in relations
        # the species bonus (+0.30, methanol-damped) lifted panduratin A
        # over caffeine's fixture distance
        assert top["candidate"]["name"] == "panduratin A"
        # literature grounding: the top candidate was searched in-species
        lit = graph.task_by_id("literature-ground").output
        assert lit["total_count"] >= 1
        assert all("Boesenbergia rotunda" in p["title"] for p in lit["papers"])

    def test_without_metadata_ranking_is_spectral_only(
        self, planner, engine
    ) -> None:
        graph, result = self._run(planner, engine, None)
        assert result.status == "complete"
        output = graph.task_by_id("elucidate").output
        assert output["taxon_prior_applied"] is False
        assert output["top_candidate_name"] == "caffeine"  # fixture distance 1.00
        assert all(
            rank["taxon_prior_match"] == "none" for rank in output["ranks"]
        )


class TestApiSurface:
    def test_analysis_accepts_metadata_and_unknown_solvent_warns(self, caplog) -> None:
        from orchestrator.main import create_app

        app = create_app()
        client = TestClient(app)
        with caplog.at_level(logging.WARNING, logger="orchestrator.main"):
            response = client.post(
                "/analysis",
                json={
                    "sample_id": "s-tax",
                    "workflow": "identify",
                    "modalities": ["hsqc"],
                    "sample_metadata": {
                        "species": "Boesenbergia rotunda",
                        "extraction_solvent": "vinegar",
                    },
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("complete", "awaiting_review")
        # unknown solvent warned, not rejected -- the filter is skipped
        assert any(
            "unknown extraction solvent" in record.message
            for record in caplog.records
        )

    def test_metadata_schema_and_species_aware_mechanism(self, planner) -> None:
        with pytest.raises(ValidationError):
            SampleMetadata(species="")  # empty strings never pass validation
        graph = planner.plan(
            run_id="r4", workflow="mechanism_hypothesis", dry_run=True,
            sample_metadata=SampleMetadata(species="Andrographis paniculata"),
        )
        lit = graph.task_by_id("literature-search")
        assert lit.tool == "literature.search_compound_in_species"
        assert lit.inputs["species_name"] == "Andrographis paniculata"
