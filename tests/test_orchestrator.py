"""Orchestrator service tests (Step 3).

Local plane only: no Docker daemon, no GCP credentials, no network.
Everything runs against the deterministic mock executor and must finish
in well under 10 seconds.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from phyto_shared.constants import ToolTier
from phyto_shared.exceptions import (
    CloudServiceError,
    InvalidInputError,
    ProvenanceError,
    SandboxError,
    ToolTierViolationError,
)
from phyto_shared.models import Candidate, MolecularFormula, Peak1D, Spectrum

from orchestrator.audit_logger import ChainedAuditLogger
from orchestrator.llm_client import (
    MockLLMClient,
    VertexAILLMClient,
    get_llm_client,
)
from orchestrator.planner_agent import (
    PlannerAgent,
    Task,
    TaskGraph,
    TaskStatus,
    WorkflowEngine,
)
from orchestrator.qa_critic_agent import QACriticAgent
from orchestrator.sandbox import CodeSandbox
from orchestrator.specialist_agents import (
    BiologistAgent,
    ChemistAgent,
    MetabolomistAgent,
    SpectroscopistAgent,
)
from orchestrator.tool_registry import (
    TIER1_TOOL_NAMES,
    TIER2_TOOL_NAMES,
    TIER3_TOOL_NAMES,
    LLMExplainOutput,
    LLMHypothesizeOutput,
    MockToolExecutor,
    ToolRegistry,
    build_default_registry,
)
from orchestrator.tool_tiering import (
    ExecutionContext,
    HumanCheckpoint,
    enforce_output_persistence,
    enforce_tiering,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> ToolRegistry:
    return build_default_registry()


@pytest.fixture()
def audit(tmp_path):
    return ChainedAuditLogger(tmp_path / "audit.jsonl")


@pytest.fixture()
def engine(registry, audit):
    return WorkflowEngine(
        registry=registry,
        executor=MockToolExecutor(registry),
        audit=audit,
        qa_critic=QACriticAgent(),
    )


@pytest.fixture()
def planner(registry):
    return PlannerAgent(registry)


@pytest.fixture(scope="session", autouse=True)
def _audit_env(tmp_path_factory):
    """Keep the API's audit file out of the repository during tests."""
    os.environ["PHYTO_AUDIT_LOG"] = str(
        tmp_path_factory.mktemp("audit") / "api_audit.jsonl"
    )
    yield
    os.environ.pop("PHYTO_AUDIT_LOG", None)


@pytest.fixture()
def client():
    from orchestrator.main import app, get_runtime

    get_runtime.cache_clear()  # pick up the test audit path
    with TestClient(app) as test_client:
        yield test_client
    get_runtime.cache_clear()


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_all_stub_tools_registered(self, registry):
        names = {spec.name for spec in registry.list_tools()}
        assert set(TIER1_TOOL_NAMES) | set(TIER2_TOOL_NAMES) | set(TIER3_TOOL_NAMES) <= names
        # 19 base tools + 4 taxonomy-aware tools (ADR-0052)
        assert len(registry) >= 23

    def test_tier_membership(self, registry):
        for name in TIER1_TOOL_NAMES:
            assert registry.get_tool(name).tier is ToolTier.TIER_1_DETERMINISTIC
        for name in TIER2_TOOL_NAMES:
            assert registry.get_tool(name).tier is ToolTier.TIER_2_ML_VALIDATED
        for name in TIER3_TOOL_NAMES:
            assert registry.get_tool(name).tier is ToolTier.TIER_3_LLM_GENERATED

    def test_get_tool_unknown_raises(self, registry):
        with pytest.raises(InvalidInputError) as excinfo:
            registry.get_tool("quantum.solve")
        assert excinfo.value.context["tool"] == "quantum.solve"

    def test_duplicate_registration_rejected(self, registry):
        spec = registry.get_tool("rdkit.formula")
        with pytest.raises(InvalidInputError):
            registry.register(spec)

    def test_list_tools_filters(self, registry):
        tier1 = registry.list_tools(tier=ToolTier.TIER_1_DETERMINISTIC)
        assert len(tier1) >= 12
        mockable = registry.list_tools(enabled_in_dry_run=True)
        assert len(mockable) == len(registry)

    def test_gpu_only_for_shift_mpnn(self, registry):
        assert registry.get_tool("shift_mpnn.predict").requires_gpu is True
        assert all(
            not spec.requires_gpu
            for spec in registry.list_tools()
            if spec.name != "shift_mpnn.predict"
        )


class TestMockExecutor:
    def test_tier1_outputs(self, registry):
        executor = MockToolExecutor(registry)
        mass = executor.execute(
            "formula.exact_mass",
            {"formula": {"counts": {"C": 6, "H": 12, "O": 6}}},
        )
        assert mass.exact_mass == pytest.approx(180.063388, abs=2e-6)

    def test_fdr_is_real_benjamini_hochberg(self, registry):
        executor = MockToolExecutor(registry)
        out = executor.execute(
            "stats.fdr_correction",
            {"p_values": [0.01, 0.04, 0.03, 0.20], "alpha": 0.05},
        )
        assert out.adjusted_p_values == pytest.approx(
            [0.04, 0.0533333, 0.0533333, 0.20], abs=1e-6
        )
        assert out.rejected == [True, False, False, False]

    def test_gnps_job_id_deterministic(self, registry):
        executor = MockToolExecutor(registry)
        first = executor.execute(
            "gnps.submit_job", {"mgf_content": "x", "title": "sample A"}
        )
        second = executor.execute(
            "gnps.submit_job", {"mgf_content": "x", "title": "sample A"}
        )
        assert first.job_id == second.job_id
        assert first.job_id.startswith("MOCK-GNPS-")

    def test_invalid_input_rejected(self, registry):
        executor = MockToolExecutor(registry)
        with pytest.raises(ValidationError):
            executor.execute(
                "stats.t_test",
                {"sample_a": [1.0], "sample_b": [1.0, 2.0]},  # too short
            )


# ---------------------------------------------------------------------------
# Tool tiering
# ---------------------------------------------------------------------------


class TestToolTiering:
    def test_tier1_always_allowed(self, registry):
        context = ExecutionContext(run_id="r", dry_run=True)
        enforce_tiering(registry.get_tool("rdkit.formula"), context)

    def test_tier2_requires_verification(self, registry):
        spec = registry.get_tool("sirius.predict_formula")
        with pytest.raises(ToolTierViolationError) as excinfo:
            enforce_tiering(spec, ExecutionContext(run_id="r", dry_run=True))
        assert excinfo.value.context["tool"] == "sirius.predict_formula"
        enforce_tiering(
            spec,
            ExecutionContext(run_id="r", dry_run=True, verification_available=True),
        )

    def test_tier3_requires_verification_and_human(self, registry):
        spec = registry.get_tool("llm.explain")
        with pytest.raises(ToolTierViolationError):
            enforce_tiering(
                spec,
                ExecutionContext(
                    run_id="r", dry_run=True, human_in_the_loop_approved=True
                ),
            )
        with pytest.raises(ToolTierViolationError):
            enforce_tiering(
                spec, ExecutionContext(run_id="r", dry_run=True, verification_available=True)
            )
        enforce_tiering(
            spec,
            ExecutionContext(
                run_id="r",
                dry_run=True,
                verification_available=True,
                human_in_the_loop_approved=True,
            ),
        )

    def test_with_approval_grants_tier3(self):
        context = ExecutionContext(run_id="r", dry_run=True, verification_available=True)
        approved = context.with_approval(
            HumanCheckpoint.BEFORE_CONFIRMED_CLAIM, actor="pi"
        )
        assert approved.human_in_the_loop_approved is True
        assert approved.checkpoint_approved(
            HumanCheckpoint.BEFORE_CONFIRMED_CLAIM
        )
        # frozen: the original context is untouched
        assert context.human_in_the_loop_approved is False
        with pytest.raises(ValidationError):
            context.actor = "someone-else"

    def test_output_persistence_gate(self, registry):
        tier3 = registry.get_tool("llm.explain")
        with pytest.raises(ToolTierViolationError):
            enforce_output_persistence(tier3, verified=False)
        enforce_output_persistence(tier3, verified=True)
        # Tier 1 outputs do not need verification
        enforce_output_persistence(
            registry.get_tool("rdkit.formula"), verified=False
        )


# ---------------------------------------------------------------------------
# Audit chain
# ---------------------------------------------------------------------------


class TestAuditChain:
    def test_chain_verifies(self, audit):
        audit.log_event(run_id="r", event="run_started")
        audit.log_tool_call(
            run_id="r",
            agent="orchestrator",
            tool="rdkit.formula",
            tier=ToolTier.TIER_1_DETERMINISTIC,
            input_hash="a" * 64,
            output_hash="b" * 64,
        )
        assert audit.verify_chain() is True
        assert len(audit.records()) == 2

    def test_chain_continues_across_reopen(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        first = ChainedAuditLogger(path)
        first.log_event(run_id="r", event="one")
        first.log_event(run_id="r", event="two")

        second = ChainedAuditLogger(path)
        second.log_event(run_id="r", event="three")
        assert second.verify_chain() is True
        assert [r["seq"] for r in second.records()] == [1, 2, 3]

    def test_tampered_record_detected(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        logger = ChainedAuditLogger(path)
        logger.log_event(run_id="r", event="one")
        logger.log_tool_call(
            run_id="r",
            agent="orchestrator",
            tool="llm.explain",
            tier=ToolTier.TIER_3_LLM_GENERATED,
            input_hash="a" * 64,
            output_hash="b" * 64,
        )
        logger.log_event(run_id="r", event="three")

        # Tamper: change the tool name in the middle record, keep its hash
        lines = path.read_text().splitlines()
        record = json.loads(lines[1])
        record["tool"] = "rdkit.formula"  # a downgrade nobody approved
        lines[1] = json.dumps(record)
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ProvenanceError):
            ChainedAuditLogger(path).verify_chain()

    def test_deleted_middle_record_detected(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        logger = ChainedAuditLogger(path)
        for i in range(4):
            logger.log_event(run_id="r", event=f"e{i}")

        lines = path.read_text().splitlines()
        del lines[1]  # remove the second record
        path.write_text("\n".join(lines) + "\n")

        with pytest.raises(ProvenanceError):
            ChainedAuditLogger(path).verify_chain()


# ---------------------------------------------------------------------------
# LLM client
# ---------------------------------------------------------------------------


class TestLLMClient:
    def test_local_factory_returns_mock(self):
        client = get_llm_client("local")
        assert isinstance(client, MockLLMClient)

    def test_mock_is_deterministic_and_marked(self):
        client = MockLLMClient()
        first = client.complete("be careful", "explain this evidence")
        second = client.complete("be careful", "explain this evidence")
        assert first == second
        assert first.startswith("[MOCK-LLM]")
        assert "Tier-3" in first

    def test_cloud_factory_requires_config(self):
        with pytest.raises(CloudServiceError):
            get_llm_client("cloud")
        client = get_llm_client(
            "cloud", endpoint_id="123456789", project="my-project"
        )
        assert isinstance(client, VertexAILLMClient)

    def test_vertex_client_fails_closed_without_sdk(self):
        client = VertexAILLMClient(endpoint_id="1", project="p")
        with pytest.raises(CloudServiceError) as excinfo:
            client.complete("s", "u")
        assert "local plane" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class TestSandbox:
    @pytest.fixture()
    def sandbox(self):
        return CodeSandbox(docker_binary="docker-binary-that-does-not-exist")

    def test_syntax_error_rejected(self, sandbox):
        with pytest.raises(SandboxError):
            sandbox.run("def broken(:\n    pass")

    def test_forbidden_import_rejected(self, sandbox):
        with pytest.raises(SandboxError) as excinfo:
            sandbox.run("import os\nprint(os.getcwd())")
        assert "os" in excinfo.value.context["module"]

    def test_forbidden_call_rejected(self, sandbox):
        with pytest.raises(SandboxError):
            sandbox.run("open('/etc/passwd')")

    def test_allowed_code_passes_guards(self, sandbox):
        result = sandbox.run(
            "import math\nimport statistics\nprint(statistics.mean([1, 2, 3]))",
            dry_run=True,
        )
        assert result.executed is False
        assert result.exit_code == 0
        assert "dry-run" in result.notes

    def test_execution_refused_without_docker(self, sandbox):
        with pytest.raises(SandboxError) as excinfo:
            sandbox.run("print('hi')")
        assert "dry_run=True" in str(excinfo.value)

    def test_code_hash_recorded(self, sandbox):
        result = sandbox.run("x = 1", dry_run=True)
        assert len(result.code_hash) == 64


# ---------------------------------------------------------------------------
# Planner and task graph
# ---------------------------------------------------------------------------


class TestPlanner:
    def test_single_compound_template(self, planner):
        graph = planner.plan(
            run_id="r1", workflow="single_compound", dry_run=True
        )
        ids = [task.id for task in graph.tasks]
        assert "approve-claim" in ids
        checkpoint_task = graph.task_by_id("approve-claim")
        assert (
            checkpoint_task.requires_checkpoint
            is HumanCheckpoint.BEFORE_CONFIRMED_CLAIM
        )
        # dependencies precede dependents (template is topologically ordered)
        for task in graph.tasks:
            for dep in task.depends_on:
                assert ids.index(dep) < ids.index(task.id)

    def test_all_workflows_plan(self, planner):
        for workflow in (
            "single_compound",
            "compare_extracts",
            "novelty_scan",
            "build_network",
        ):
            graph = planner.plan(run_id="r", workflow=workflow, dry_run=True)
            assert graph.tasks

    def test_cycle_rejected(self):
        with pytest.raises(ValidationError):
            TaskGraph(
                run_id="r",
                workflow="single_compound",
                dry_run=True,
                tasks=[
                    Task(id="a", description="a", depends_on=["b"]),
                    Task(id="b", description="b", depends_on=["a"]),
                ],
            )

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValidationError):
            TaskGraph(
                run_id="r",
                workflow="single_compound",
                dry_run=True,
                tasks=[Task(id="a", description="a", depends_on=["ghost"])],
            )

    def test_replan_skips_downstream(self, planner):
        graph = planner.plan(run_id="r", workflow="single_compound", dry_run=True)
        updated = planner.replan_after_failure(graph, "predict-formula")
        assert updated.task_by_id("predict-formula").status == TaskStatus.FAILED
        assert updated.task_by_id("predict-shifts").status == TaskStatus.SKIPPED
        assert updated.task_by_id("match-peaks").status == TaskStatus.SKIPPED
        # the independent root survives
        assert updated.task_by_id("parse-peaks").status == TaskStatus.PENDING


# ---------------------------------------------------------------------------
# Workflow engine (the heart)
# ---------------------------------------------------------------------------


class TestWorkflowEngine:
    def _context(self, **overrides):
        base = {
            "run_id": "r1",
            "dry_run": True,
            "verification_available": True,
        }
        base.update(overrides)
        return ExecutionContext(**base)

    def test_run_pauses_at_unapproved_checkpoint(self, engine, planner, audit):
        graph = planner.plan(run_id="r1", workflow="single_compound", dry_run=True)
        result = engine.run(graph, self._context())
        assert result.status == "awaiting_review"
        assert result.awaiting_task_id == "approve-claim"
        # work before the gate is done; nothing after it ran
        assert graph.task_by_id("match-peaks").status == TaskStatus.COMPLETE
        assert graph.task_by_id("explain-result").status == TaskStatus.PENDING

    def test_resume_after_approval_completes(self, engine, planner, audit):
        graph = planner.plan(run_id="r1", workflow="single_compound", dry_run=True)
        engine.run(graph, self._context())
        approved = self._context().with_approval(
            HumanCheckpoint.BEFORE_CONFIRMED_CLAIM, actor="pi"
        )
        result = engine.run(graph, approved)
        assert result.status == "complete"
        assert all(
            task.status == TaskStatus.COMPLETE for task in graph.tasks
        )
        assert audit.verify_chain() is True
        # Tier-3 output recorded as verified
        explain_records = [
            r for r in audit.records() if r.get("tool") == "llm.explain"
        ]
        assert len(explain_records) == 1
        assert explain_records[0]["verified"] is True

    def test_tier3_without_human_approval_fails_run(self, engine, planner, audit):
        graph = planner.plan(run_id="r1", workflow="single_compound", dry_run=True)
        # checkpoint approved directly (not via with_approval): no human grant
        context = self._context(
            approved_checkpoints=frozenset({HumanCheckpoint.BEFORE_CONFIRMED_CLAIM})
        )
        result = engine.run(graph, context)
        assert result.status == "failed"
        assert result.failed_task_id == "explain-result"
        assert "Tier 3" in graph.task_by_id("explain-result").error
        assert any(
            r.get("event") == "tier_violation" for r in audit.records()
        )

    def test_unverified_tier3_output_not_persisted(
        self, registry, audit, planner
    ):
        class BadExplainExecutor(MockToolExecutor):
            def execute(self, tool_name, inputs):
                output = super().execute(tool_name, inputs)
                if tool_name == "llm.explain":
                    # passes the output SCHEMA but fails the critic rubric
                    # (explanation under 10 chars): exercises the
                    # verification gate, not schema validation
                    return LLMExplainOutput(explanation="no")
                return output

        engine = WorkflowEngine(
            registry=registry,
            executor=BadExplainExecutor(registry),
            audit=audit,
            qa_critic=QACriticAgent(),
        )
        graph = planner.plan(run_id="r1", workflow="single_compound", dry_run=True)
        engine.run(graph, self._context())
        context = self._context().with_approval(
            HumanCheckpoint.BEFORE_CONFIRMED_CLAIM, actor="pi"
        )
        result = engine.run(graph, context)
        assert result.status == "failed"
        assert result.failed_task_id == "explain-result"
        assert any(
            r.get("event") == "verification_rejected" for r in audit.records()
        )

    def test_compare_extracts_completes_without_checkpoints(
        self, engine, planner
    ):
        graph = planner.plan(run_id="r1", workflow="compare_extracts", dry_run=True)
        result = engine.run(graph, self._context())
        assert result.status == "complete"

    def test_build_network_pauses_at_kg_write(self, engine, planner):
        graph = planner.plan(run_id="r1", workflow="build_network", dry_run=True)
        first = engine.run(graph, self._context())
        assert first.status == "awaiting_review"
        assert (
            graph.task_by_id("approve-kg-write").requires_checkpoint
            is HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE
        )
        approved = self._context().with_approval(
            HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE, actor="pi"
        )
        second = engine.run(graph, approved)
        assert second.status == "complete"

    def test_novelty_scan_double_gate(self, engine, planner):
        graph = planner.plan(run_id="r1", workflow="novelty_scan", dry_run=True)
        first = engine.run(graph, self._context())
        assert first.status == "awaiting_review"
        assert first.awaiting_task_id == "approve-novel-generation"

        second = engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_NOVEL_STRUCTURE_GENERATION, actor="pi"
            ),
        )
        assert second.status == "awaiting_review"
        assert second.awaiting_task_id == "approve-experiments"

        third = engine.run(
            graph,
            self._context()
            .with_approval(
                HumanCheckpoint.BEFORE_NOVEL_STRUCTURE_GENERATION, actor="pi"
            )
            .with_approval(
                HumanCheckpoint.BEFORE_EXPERIMENT_RECOMMENDATION, actor="pi"
            ),
        )
        assert third.status == "complete"

    def test_audit_records_every_tool_call(self, engine, planner, audit):
        graph = planner.plan(run_id="r1", workflow="single_compound", dry_run=True)
        engine.run(graph, self._context())
        engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_CONFIRMED_CLAIM, actor="pi"
            ),
        )
        tool_calls = [
            r for r in audit.records() if r["kind"] == "tool_call"
        ]
        tool_task_count = sum(
            1 for task in graph.tasks if task.tool is not None
        )
        assert len(tool_calls) == tool_task_count
        for record in tool_calls:
            assert len(record["input_hash"]) == 64
            assert len(record["output_hash"]) == 64
        # checkpoints are in the trail too
        events = {r["event"] for r in audit.records() if r["kind"] == "event"}
        assert {"run_started", "checkpoint_requested", "checkpoint_approved"} <= events


# ---------------------------------------------------------------------------
# Specialist agents
# ---------------------------------------------------------------------------


class TestSpecialistAgents:
    def test_spectroscopist(self):
        agent = SpectroscopistAgent()
        good = Spectrum(
            kind="1H",
            data=[
                Peak1D(ppm=6.2, intensity=10.0),
                Peak1D(ppm=7.4, intensity=8.0, solvent_flag=True),
            ],
        )
        assert agent.review_spectrum(good).status == "approve"
        mostly_solvent = Spectrum(
            kind="1H",
            data=[
                Peak1D(ppm=2.5, intensity=90.0, solvent_flag=True),
                Peak1D(ppm=3.3, intensity=80.0, solvent_flag=True),
                Peak1D(ppm=6.2, intensity=5.0),
            ],
        )
        assert agent.review_spectrum(mostly_solvent).status == "reject"
        assert agent.review_spectrum(Spectrum(kind="1H", data=[])).status == "needs_info"

    def test_metabolomist(self):
        agent = MetabolomistAgent()
        assert agent.review_statistics([0.01, 0.02], fdr_applied=True).status == "approve"
        assert agent.review_statistics([0.01, 0.02], fdr_applied=False).status == "needs_info"
        assert agent.review_statistics([1.5], fdr_applied=True).status == "reject"

    def test_chemist(self):
        agent = ChemistAgent()
        quercetin = Candidate(
            id="c1",
            name="quercetin",
            smiles="Oc1cc(O)c(c(c1)O)-c2oc3cc(O)cc(O)c3c(=O)c2",
            formula=MolecularFormula(counts={"C": 15, "H": 10, "O": 7}),
            exact_mass=302.042653,
            provenance="experimental",
            source="test",
        )
        assert agent.review_candidate(quercetin).status == "approve"
        bad_mass = quercetin.model_copy(update={"exact_mass": 250.0})
        assert agent.review_candidate(bad_mass).status == "reject"

    def test_biologist(self):
        agent = BiologistAgent()
        assert agent.review_hypothesis(
            "We hypothesize compound X may inhibit COX-2"
        ).status == "approve"
        assert agent.review_hypothesis("Compound X is proven to inhibit COX-2").status == "reject"
        assert agent.review_hypothesis("Compound X inhibits COX-2").status == "needs_info"


class TestQACritic:
    def test_good_output_passes(self):
        critic = QACriticAgent()
        result = critic.verify(
            "llm.explain", LLMExplainOutput(explanation="The evidence supports X")
        )
        assert result.passed is True

    def test_empty_output_fails_closed(self):
        critic = QACriticAgent()
        result = critic.verify("llm.explain", LLMExplainOutput(explanation="x"))
        assert result.passed is False

    def test_overclaiming_hypothesis_rejected(self):
        critic = QACriticAgent()
        from orchestrator.tool_registry import LLMHypothesizeOutput

        result = critic.verify(
            "llm.hypothesize_mechanism",
            LLMHypothesizeOutput(
                hypothesis="This mechanism is proven to work"
            ),
        )
        assert result.passed is False

    def test_llm_judge_supplements_rules(self):
        critic = QACriticAgent(llm=MockLLMClient())
        result = critic.verify(
            "llm.explain", LLMExplainOutput(explanation="The evidence supports X")
        )
        assert result.verified_by == "llm_judge"
        assert any(check.name == "llm_judge" for check in result.checks)

    def test_llm_judge_verdict_carries_model_identity(self):
        # ADR-0080: every llm_judge verdict must attribute itself to a
        # specific model so it can be re-verified against the same model.
        class RecordingClient:
            model_identity = "test-judge-model:v7"

            def complete(self, system_prompt: str, user_prompt: str) -> str:
                return "PASS"

        result = QACriticAgent(llm=RecordingClient()).verify(
            "llm.explain", LLMExplainOutput(explanation="The evidence supports X")
        )
        judge = next(c for c in result.checks if c.name == "llm_judge")
        assert judge.model_identity == "test-judge-model:v7"
        assert judge.to_dict()["model_identity"] == "test-judge-model:v7"

    def test_rule_based_checks_have_no_model_identity(self):
        critic = QACriticAgent()
        result = critic.verify(
            "llm.explain", LLMExplainOutput(explanation="The evidence supports X")
        )
        assert all(c.model_identity is None for c in result.checks)

    def test_mock_judge_identity_is_not_a_credential(self):
        critic = QACriticAgent(llm=MockLLMClient())
        result = critic.verify(
            "llm.explain", LLMExplainOutput(explanation="The evidence supports X")
        )
        judge = next(c for c in result.checks if c.name == "llm_judge")
        assert judge.model_identity == "mock-llm-client:deterministic"


# ---------------------------------------------------------------------------
# FastAPI surface
# ---------------------------------------------------------------------------


class TestAPI:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["environment"] == "local"
        assert body["registry_size"] >= 23

    def test_tools_listing_and_filter(self, client):
        all_tools = client.get("/tools").json()
        assert len(all_tools) >= 23
        tier1 = client.get("/tools", params={"tier": 1}).json()
        assert len(tier1) >= 12
        assert all(tool["tier"] == 1 for tool in tier1)

    def test_analysis_completes_when_no_checkpoint(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "s1",
                "workflow": "compare_extracts",
                "parameters": {},
                "dry_run": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "complete"
        assert len(body["provenance_manifest_id"]) == 64
        assert body["summary"]["workflow"] == "compare_extracts"

    def test_full_checkpoint_flow(self, client):
        submit = client.post(
            "/analysis",
            json={
                "sample_id": "s1",
                "workflow": "single_compound",
                "parameters": {},
                "dry_run": True,
            },
        ).json()
        run_id = submit["run_id"]
        assert submit["status"] == "awaiting_review"

        status = client.get(f"/runs/{run_id}").json()
        assert status["awaiting_task_id"] == "approve-claim"

        # wrong checkpoint -> 409
        wrong = client.post(
            f"/runs/{run_id}/approve",
            json={
                "checkpoint": "before_knowledge_graph_write",
                "actor": "pi",
            },
        )
        assert wrong.status_code == 409

        right = client.post(
            f"/runs/{run_id}/approve",
            json={"checkpoint": "before_confirmed_claim", "actor": "pi"},
        )
        assert right.status_code == 200
        assert right.json()["status"] == "complete"

        final = client.get(f"/runs/{run_id}").json()
        assert final["status"] == "complete"
        assert final["task_counts"]["complete"] == len(final["tasks"])

    def test_unknown_run_404(self, client):
        assert client.get("/runs/nope").status_code == 404

    def test_unknown_workflow_422(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "s1",
                "workflow": "solve_everything",
                "parameters": {},
                "dry_run": True,
            },
        )
        assert response.status_code == 422

    def test_audit_verify_endpoint(self, client):
        client.post(
            "/analysis",
            json={
                "sample_id": "s1",
                "workflow": "compare_extracts",
                "parameters": {},
                "dry_run": True,
            },
        )
        body = client.get("/audit/verify").json()
        assert body["chain_valid"] is True
        assert body["records"] > 0


# ---------------------------------------------------------------------------
# mechanism_hypothesis workflow (ADR-0029 / ADR-0031)
# ---------------------------------------------------------------------------


class TestMechanismHypothesisWorkflow:
    def _context(self, **overrides):
        base = {"run_id": "r-mh", "dry_run": True, "verification_available": True}
        base.update(overrides)
        return ExecutionContext(**base)

    def test_template_has_nmr_literature_hypothesis_and_two_gates(self, planner):
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        ids = [task.id for task in graph.tasks]
        assert ids == [
            "nmr-search",
            "literature-search",
            "approve-mechanism",
            "hypothesize-mechanism",
            "verify-grounding",
            "approve-kg-write",
            "write-knowledge-graph",
        ]
        checkpoints = {
            task.requires_checkpoint for task in graph.tasks if task.requires_checkpoint
        }
        assert checkpoints == {
            HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS,
            HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE,
        }

    def test_topological_order_is_respected(self, planner):
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        ids = [t.id for t in graph.tasks]
        for task in graph.tasks:
            for dep in task.depends_on:
                assert ids.index(dep) < ids.index(task.id)

    def test_run_pauses_before_llm(self, engine, planner):
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        result = engine.run(graph, self._context())
        assert result.status == "awaiting_review"
        assert result.awaiting_task_id == "approve-mechanism"
        # Tier-1 retrieval ran; the LLM did not
        assert graph.task_by_id("nmr-search").status == TaskStatus.COMPLETE
        assert graph.task_by_id("literature-search").status == TaskStatus.COMPLETE
        assert graph.task_by_id("hypothesize-mechanism").status == TaskStatus.PENDING

    def test_full_flow_passes_grounding_and_reaches_complete(
        self, engine, planner, audit
    ):
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        engine.run(graph, self._context())

        after_llm = engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS, actor="pi"
            ),
        )
        # The LLM ran and the grounding gate accepted it; now the KG gate.
        assert graph.task_by_id("hypothesize-mechanism").status == TaskStatus.COMPLETE
        assert graph.task_by_id("verify-grounding").status == TaskStatus.COMPLETE
        assert after_llm.status == "awaiting_review"
        assert after_llm.awaiting_task_id == "approve-kg-write"

        final = engine.run(
            graph,
            self._context()
            .with_approval(HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS, actor="pi")
            .with_approval(HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE, actor="pi"),
        )
        assert final.status == "complete"
        hypothesis = graph.task_by_id("hypothesize-mechanism").output
        assert hypothesis["grounding_score"] >= 0.7
        assert hypothesis["claims_uncited"] == 0
        assert audit.verify_chain() is True

    def test_context_papers_are_wired_from_literature_to_hypothesis(
        self, engine, planner
    ):
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        engine.run(graph, self._context())
        engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS, actor="pi"
            ),
        )
        papers = graph.task_by_id("literature-search").output["papers"]
        assert papers
        # The produced hypothesis cites identifiers that exist in the papers,
        # which is only possible if the wiring supplied them.
        hypothesis = graph.task_by_id("hypothesize-mechanism").output
        assert any(ref for ref in hypothesis["literature_refs"])

    def test_ungrounded_hypothesis_is_rejected(self, registry, audit, planner):
        class UngroundedExecutor(MockToolExecutor):
            def execute(self, tool_name, inputs):
                output = super().execute(tool_name, inputs)
                if tool_name == "llm.hypothesize":
                    return LLMHypothesizeOutput(
                        hypothesis=(
                            "This compound definitely cures inflammation through "
                            "an undocumented pathway."
                        ),
                        grounding_score=1.0,  # self-reported lie: must be ignored
                    )
                return output

        engine = WorkflowEngine(
            registry=registry,
            executor=UngroundedExecutor(registry),
            audit=audit,
            qa_critic=QACriticAgent(),
        )
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        engine.run(graph, self._context())
        result = engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS, actor="pi"
            ),
        )
        assert result.status == "failed"
        assert result.failed_task_id == "hypothesize-mechanism"

    def test_fabricated_citation_is_rejected(self, registry, audit, planner):
        class FabricatingExecutor(MockToolExecutor):
            def execute(self, tool_name, inputs):
                output = super().execute(tool_name, inputs)
                if tool_name == "llm.hypothesize":
                    return LLMHypothesizeOutput(
                        hypothesis=(
                            "The compound modulates the inflammatory pathway "
                            "described in the literature. PMID: 99999999."
                        ),
                        grounding_score=1.0,
                    )
                return output

        engine = WorkflowEngine(
            registry=registry,
            executor=FabricatingExecutor(registry),
            audit=audit,
            qa_critic=QACriticAgent(),
        )
        graph = planner.plan(run_id="r-mh", workflow="mechanism_hypothesis", dry_run=True)
        engine.run(graph, self._context())
        result = engine.run(
            graph,
            self._context().with_approval(
                HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS, actor="pi"
            ),
        )
        assert result.status == "failed"
        assert any(r.get("event") == "verification_rejected" for r in audit.records())

    def test_api_exposes_mechanism_workflow(self, client):
        response = client.post(
            "/analysis",
            json={
                "sample_id": "s1",
                "workflow": "mechanism_hypothesis",
                "parameters": {},
                "dry_run": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "awaiting_review"
        assert body["summary"]["awaiting_task_id"] == "approve-mechanism"
