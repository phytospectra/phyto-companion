"""Planner wiring for the metabolomics tasks (ADR-0068..ADR-0072): the
tasks exist only when a dataset reference was supplied, the graph stays
topologically valid, and SampleMetadata carries the field that gates them."""

from __future__ import annotations

import pytest

from phyto_shared.metabolomics import MetabolomicsInput
from phyto_shared.schemas import SampleMetadata

from orchestrator.planner_agent import PlannerAgent
from orchestrator.tool_registry import DEFAULT_REGISTRY


@pytest.fixture()
def agent() -> PlannerAgent:
    return PlannerAgent(registry=DEFAULT_REGISTRY)


def _metadata(**overrides) -> SampleMetadata:
    fields = dict(
        species="Curcuma longa",
        metabolomics=MetabolomicsInput(
            kind="nmr_bruker_dirs",
            paths=["/data/curcuma"],
            sample_sheet_path="sheet.csv",
            continuous_covariate="storage_day",
            driver_peak_ppm=7.55,
        ),
    )
    fields.update(overrides)
    return SampleMetadata(**fields)


class TestTaskWiring:
    def test_tasks_planned_when_reference_supplied(self, agent) -> None:
        graph = agent.plan(
            run_id="r1", workflow="identify", dry_run=True,
            sample_metadata=_metadata(),
        )
        ids = [t.id for t in graph.tasks]
        assert "metabolomics-preprocess" in ids
        assert "apply-metabolomics-layers" in ids
        preprocess = next(t for t in graph.tasks if t.id == "metabolomics-preprocess")
        apply_task = next(t for t in graph.tasks if t.id == "apply-metabolomics-layers")
        assert apply_task.depends_on == ["metabolomics-preprocess"]
        assert apply_task.consumes == ["metabolomics-preprocess"]
        assert preprocess.tool == "metabolomics.preprocess"
        assert apply_task.tool == "metabolomics.apply_layers"

    def test_no_tasks_without_reference(self, agent) -> None:
        graph = agent.plan(
            run_id="r2", workflow="identify", dry_run=True,
            sample_metadata=SampleMetadata(species="Curcuma longa"),
        )
        ids = [t.id for t in graph.tasks]
        assert "metabolomics-preprocess" not in ids
        assert "apply-metabolomics-layers" not in ids

    def test_graph_topologically_valid_with_metabolomics(self, agent) -> None:
        # _validate_topology raises on unknown deps or cycles
        graph = agent.plan(
            run_id="r3", workflow="identify", dry_run=True,
            sample_metadata=_metadata(),
        )
        assert len(graph.tasks) >= 10

    def test_feature_table_csv_kind_also_planned(self, agent) -> None:
        graph = agent.plan(
            run_id="r4", workflow="identify", dry_run=True,
            sample_metadata=_metadata(
                metabolomics=MetabolomicsInput(
                    kind="feature_table_csv", paths=["features.csv"],
                    group_column="treatment",
                )
            ),
        )
        assert "apply-metabolomics-layers" in [t.id for t in graph.tasks]


class TestSampleMetadataField:
    def test_field_defaults_absent(self) -> None:
        md = SampleMetadata()
        assert md.metabolomics is None

    def test_kind_is_limited_to_known_roots(self) -> None:
        with pytest.raises(Exception):
            MetabolomicsInput(kind="excel_spreadsheet", paths=["x.xlsx"])

    def test_paths_required(self) -> None:
        with pytest.raises(Exception):
            MetabolomicsInput(kind="feature_table_csv", paths=[])