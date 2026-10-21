"""Single-modality support: NMR-only and MS-only users.

The platform's core promise is that a user with only NMR data (or only MS
data) gets a complete, honest analysis rather than a degraded one.  These
tests pin the two guarantees that make that true (ADR-0050):

1. Ranking reports which modalities actually scored, and states the
   cross-modal gap instead of letting a single-family result look fused.
2. Planning prunes steps the supplied evidence cannot support and says
   why, rather than running them and failing -- or worse, succeeding
   without the constraint they were supposed to supply.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.constants import (
    ObservationModality,
    is_single_family,
    parse_observation_modalities,
    single_family_gap,
)
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, MolecularFormula

from elucidation.candidate_ranker import (
    ObservedEvidence,
    parse_formula_string,
    rank_candidates,
)
from elucidation.shift_predictor import MockShiftPredictor
from orchestrator.planner_agent import PlannerAgent, TaskStatus
from orchestrator.tool_registry import DEFAULT_REGISTRY

M = ObservationModality


def _candidate(name: str, smiles: str, formula_text: str) -> Candidate:
    counts = parse_formula_string(formula_text)
    return Candidate(
        id=name.lower(),
        name=name,
        smiles=smiles,
        formula=MolecularFormula(counts=counts),
        exact_mass=MolecularFormula(counts=counts).exact_mass(),
        provenance="experimental",
        source="test",
    )


PHENOL = _candidate("phenol", "Oc1ccccc1", "C6H6O")

NMR_ONLY = ObservedEvidence(
    h_1d=[5.30, 6.85, 7.20, 6.80, 7.20, 6.85],
    c_1d=[155.0, 116.0, 130.0, 121.0, 130.0, 116.0],
    hsqc=[
        (6.85, 116.0, "CH"),
        (7.20, 130.0, "CH"),
        (6.80, 121.0, "CH"),
        (7.20, 130.0, "CH"),
        (6.85, 116.0, "CH"),
    ],
    formula="C6H6O",
)

PHENOL_MS2 = [(95.0, 100.0), (77.0, 50.0), (51.0, 20.0)]


# ---------------------------------------------------------------------------
# Shared modality vocabulary
# ---------------------------------------------------------------------------


class TestModalityVocabulary:
    def test_every_modality_belongs_to_one_family(self):
        assert is_single_family({M.H_1D, M.C_1D, M.HSQC})
        assert is_single_family({M.MS1, M.MS2})
        assert not is_single_family({M.HSQC, M.MS2})

    def test_gap_names_only_the_absent_family(self):
        nmr_gap = single_family_gap({M.HSQC})
        ms_gap = single_family_gap({M.MS2})
        assert nmr_gap is not None and "NMR-only" in nmr_gap
        assert ms_gap is not None and "MS-only" in ms_gap
        assert single_family_gap({M.HSQC, M.MS2}) is None

    def test_empty_set_has_no_gap(self):
        # Nothing supplied means "unspecified", not "missing the other one".
        assert single_family_gap(set()) is None

    def test_parse_is_case_and_space_insensitive(self):
        assert parse_observation_modalities(["HSQC", " ms2 "]) == {M.HSQC, M.MS2}

    def test_parse_accepts_a_bare_string(self):
        assert parse_observation_modalities("hsqc") == {M.HSQC}

    def test_unknown_modality_is_rejected_not_dropped(self):
        # A typo must not silently reduce the evidence set the run is
        # planned against.
        with pytest.raises(InvalidInputError) as excinfo:
            parse_observation_modalities(["hsqc", "hmbc"])
        assert "hmbc" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Ranking: NMR-only and MS-only
# ---------------------------------------------------------------------------


class TestNMROnlyRanking:
    def test_nmr_only_ranks_without_mass_spectrometry(self):
        ranks = rank_candidates(
            NMR_ONLY, [PHENOL], predictor=MockShiftPredictor()
        )
        assert len(ranks) == 1
        assert ranks[0].candidate.name == "phenol"

    def test_nmr_only_profile_reports_what_scored(self):
        rank = rank_candidates(
            NMR_ONLY, [PHENOL], predictor=MockShiftPredictor()
        )[0]
        profile = rank.evidence_profile
        assert profile is not None
        assert profile.evaluated == ["c_1d", "h_1d", "hsqc"]
        assert profile.families == ["NMR"]
        assert profile.is_single_family is True

    def test_nmr_only_claim_carries_the_cross_modal_gap(self):
        rank = rank_candidates(
            NMR_ONLY, [PHENOL], predictor=MockShiftPredictor()
        )[0]
        assert rank.evidence_profile.gap is not None
        # The caveat must reach the evidence graph, not only the profile:
        # a reader of ``gaps`` is the one who could overclaim.
        assert any("NMR-only" in gap for gap in rank.evidence_graph.gaps)


class TestMSOnlyRanking:
    def test_ms_only_with_reference_ranks(self):
        observed = ObservedEvidence(
            ms2=PHENOL_MS2,
            formula="C6H6O",
            precursor_mz=95.0491,
            precursor_adduct="[M+H]+",
        )
        ranks = rank_candidates(
            observed,
            [PHENOL],
            predictor=MockShiftPredictor(),
            reference_ms2={PHENOL.id: PHENOL_MS2},
        )
        assert len(ranks) == 1
        profile = ranks[0].evidence_profile
        assert profile.evaluated == ["ms2"]
        assert profile.families == ["MS"]
        assert profile.gap is not None and "MS-only" in profile.gap

    def test_ms2_without_reference_is_a_gap_not_a_score(self):
        # The honest failure mode: peaks were supplied but nothing matched
        # them, so no modality scored and ranking refuses to pretend.
        observed = ObservedEvidence(ms2=PHENOL_MS2, formula="C6H6O")
        with pytest.raises(InvalidInputError) as excinfo:
            rank_candidates(observed, [PHENOL], predictor=MockShiftPredictor())
        assert "reference" in str(excinfo.value)

    def test_ms1_alone_cannot_rank(self):
        observed = ObservedEvidence(precursor_mz=95.0491, formula="C6H6O")
        with pytest.raises(InvalidInputError) as excinfo:
            rank_candidates(observed, [PHENOL], predictor=MockShiftPredictor())
        assert "MS1" in str(excinfo.value)

    def test_supplied_but_unscorable_ms2_does_not_upgrade_to_cross_modal(self):
        # The subtle dishonesty this guards against: an NMR list plus an
        # MS2 spectrum with no reference is NOT cross-modal support.
        observed = NMR_ONLY.model_copy(update={"ms2": PHENOL_MS2})
        rank = rank_candidates(
            observed, [PHENOL], predictor=MockShiftPredictor()
        )[0]
        profile = rank.evidence_profile
        assert profile.is_single_family is True
        assert profile.families == ["NMR"]
        assert profile.unavailable == ["ms2"]
        assert "could not be scored" in profile.gap


class TestCrossModalRanking:
    def test_both_families_scored_means_no_gap(self):
        observed = ObservedEvidence(
            h_1d=[6.85, 7.20],
            ms2=PHENOL_MS2,
            formula="C6H6O",
        )
        rank = rank_candidates(
            observed,
            [PHENOL],
            predictor=MockShiftPredictor(),
            reference_ms2={PHENOL.id: PHENOL_MS2},
        )[0]
        profile = rank.evidence_profile
        assert profile.families == ["MS", "NMR"]
        assert profile.is_single_family is False
        assert profile.gap is None


# ---------------------------------------------------------------------------
# Planning: impossible steps are pruned and explained
# ---------------------------------------------------------------------------


@pytest.fixture()
def planner() -> PlannerAgent:
    return PlannerAgent(DEFAULT_REGISTRY)


class TestModalityAwarePlanning:
    def test_undeclared_modalities_keep_every_step(self, planner):
        # Backwards compatibility: callers that have not declared their
        # evidence get the full template, exactly as before.
        graph = planner.plan(run_id="r", workflow="single_compound", dry_run=True)
        assert graph.pruned == []
        assert graph.modalities == []
        assert graph.evidence_gap is None
        assert len(graph.tasks) == 7

    def test_nmr_only_prunes_the_ms_formula_step(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        pruned_ids = {p.id for p in graph.pruned}
        assert pruned_ids == {"predict-formula"}
        assert "predict-formula" not in {t.id for t in graph.tasks}
        reason = graph.pruned[0].reason
        assert "ms2" in reason and "not observed" in reason

    def test_nmr_only_keeps_the_full_nmr_chain(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        assert [t.id for t in graph.tasks] == [
            "parse-peaks",
            "predict-shifts",
            "match-peaks",
            "approve-claim",
            "confirm-identity",
            "explain-result",
        ]

    def test_pruning_preserves_topological_order(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        ids = [t.id for t in graph.tasks]
        for task in graph.tasks:
            for dep in task.depends_on:
                assert ids.index(dep) < ids.index(task.id)

    def test_rewiring_keeps_surviving_dependencies(self, planner):
        # match-peaks depended on parse-peaks AND predict-shifts; only the
        # ordering link to the pruned formula step disappears.
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        assert graph.task_by_id("match-peaks").depends_on == [
            "parse-peaks",
            "predict-shifts",
        ]

    def test_ms_only_prunes_the_nmr_matching_chain(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.MS2}),
        )
        assert {t.id for t in graph.tasks} == {
            "parse-peaks",
            "predict-formula",
            "predict-shifts",
        }
        assert "match-peaks" in {p.id for p in graph.pruned}

    def test_both_modalities_prune_nothing(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC, M.MS2}),
        )
        assert graph.pruned == []
        assert len(graph.tasks) == 7

    def test_graph_records_the_evidence_gap(self, planner):
        graph = planner.plan(
            run_id="r",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        assert graph.evidence_gap is not None
        assert "NMR-only" in graph.evidence_gap

    def test_network_keeps_the_kg_gate_for_whichever_network_survives(self, planner):
        # The knowledge-graph checkpoint needs A network, not one specific
        # one: MS2-only keeps the GNPS path and its gate; NMR-only keeps the
        # HSQC path and its gate.
        ms_graph = planner.plan(
            run_id="r", workflow="build_network", dry_run=True,
            modalities=frozenset({M.MS2}),
        )
        assert {t.id for t in ms_graph.tasks} == {
            "submit-gnps", "fetch-gnps", "approve-kg-write", "write-knowledge-graph",
        }

        nmr_graph = planner.plan(
            run_id="r", workflow="build_network", dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        assert {t.id for t in nmr_graph.tasks} == {
            "hsqc-network", "approve-kg-write", "write-knowledge-graph",
        }

    def test_workflow_impossible_for_the_evidence_is_refused(self, planner):
        # An HSQC-only user asking for a literature-grounded mechanism
        # search cannot run it: the search needs HSQC pairs it does not have.
        with pytest.raises(InvalidInputError):
            planner.plan(
                run_id="r",
                workflow="mechanism_hypothesis",
                dry_run=True,
                modalities=frozenset({M.MS2}),
            )

    @pytest.mark.parametrize(
        "workflow",
        ["single_compound", "novelty_scan", "build_network", "mechanism_hypothesis"],
    )
    def test_ms1_alone_cannot_support_a_structural_workflow(self, planner, workflow):
        # A precursor m/z filters candidates; it never identifies one.  Any
        # workflow that ends in a structural claim must be refused outright
        # rather than planned down to a path with nothing to test.
        with pytest.raises(InvalidInputError) as excinfo:
            planner.plan(
                run_id="r",
                workflow=workflow,
                dry_run=True,
                modalities=frozenset({M.MS1}),
            )
        assert "needs at least one of" in str(excinfo.value)

    def test_feature_statistics_need_no_spectral_modality(self, planner):
        # Comparing extracts is instrument-agnostic: it must still run for a
        # user who has declared only a precursor mass.
        graph = planner.plan(
            run_id="r",
            workflow="compare_extracts",
            dry_run=True,
            modalities=frozenset({M.MS1}),
        )
        assert [t.id for t in graph.tasks] == ["parse-peaks", "anova", "fdr"]
        assert graph.pruned == []


class TestPlannerPrunedGraphRuns:
    def test_pruned_graph_executes_cleanly(self, planner, tmp_path):
        from orchestrator.audit_logger import ChainedAuditLogger
        from orchestrator.qa_critic_agent import QACriticAgent
        from orchestrator.tool_registry import MockToolExecutor
        from orchestrator.tool_tiering import ExecutionContext
        from orchestrator.planner_agent import WorkflowEngine

        audit = ChainedAuditLogger(tmp_path / "audit.jsonl")
        engine = WorkflowEngine(
            registry=DEFAULT_REGISTRY,
            executor=MockToolExecutor(DEFAULT_REGISTRY),
            audit=audit,
            qa_critic=QACriticAgent(),
        )
        graph = planner.plan(
            run_id="r-nmr",
            workflow="single_compound",
            dry_run=True,
            modalities=frozenset({M.HSQC}),
        )
        context = ExecutionContext(
            run_id="r-nmr", dry_run=True, verification_available=True
        )
        result = engine.run(graph, context)
        # It pauses at the human checkpoint, as every claim-bearing path
        # must -- not because a pruned step failed.
        assert result.status == "awaiting_review"
        assert result.awaiting_task_id == "approve-claim"
        assert all(
            task.status != TaskStatus.FAILED for task in graph.tasks
        )


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


class TestAnalysisApiModalities:
    @pytest.fixture()
    def client(self):
        from orchestrator.main import create_app

        return TestClient(create_app())

    def test_submit_accepts_modalities_and_reports_pruning(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "sample-1",
                "workflow": "single_compound",
                "modalities": ["hsqc"],
                "dry_run": True,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"]["modalities"] == ["hsqc"]
        assert body["summary"]["pruned_tasks"] == ["predict-formula"]
        assert "NMR-only" in body["summary"]["evidence_gap"]

    def test_unknown_modality_is_422(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "sample-1",
                "workflow": "single_compound",
                "modalities": ["hmbc"],
                "dry_run": True,
            },
        )
        assert response.status_code == 422
        assert "hmbc" in response.text

    def test_run_status_exposes_the_pruning_and_gap(self, client):
        created = client.post(
            "/analysis",
            json={
                "sample_id": "sample-1",
                "workflow": "single_compound",
                "modalities": ["hsqc"],
                "dry_run": True,
            },
        ).json()
        status = client.get(f"/runs/{created['run_id']}")
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["modalities"] == ["hsqc"]
        assert [p["id"] for p in body["pruned_tasks"]] == ["predict-formula"]
        assert "NMR-only" in body["evidence_gap"]

    def test_ms1_only_workflow_is_refused_with_the_reason(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "sample-1",
                "workflow": "novelty_scan",
                "modalities": ["ms1"],
                "dry_run": True,
            },
        )
        assert response.status_code == 422
        assert "needs at least one of" in response.text


class TestElucidationApiModalities:
    """The evidence profile must survive the wire, not just exist in-process."""

    @pytest.fixture()
    def client(self):
        from elucidation import main

        main.get_runtime.cache_clear()
        with TestClient(main.app) as test_client:
            yield test_client
        main.get_runtime.cache_clear()

    def test_nmr_only_candidates_report_the_gap(self, client):
        response = client.post(
            "/elucidate/candidates",
            json={
                "observed": {
                    "h_1d": [6.85, 7.20],
                    "c_1d": [116.0, 130.0],
                    "formula": "C6H6O",
                },
                "formula": "C6H6O",
            },
        )
        assert response.status_code == 200, response.text
        ranks = response.json()["ranks"]
        assert ranks, "phenol should be enumerated for C6H6O"
        profile = ranks[0]["evidence_profile"]
        assert profile["families"] == ["NMR"]
        assert profile["is_single_family"] is True
        assert "NMR-only" in profile["gap"]
        gaps = ranks[0]["evidence_graph"]["gaps"]
        assert any("NMR-only" in gap for gap in gaps)
