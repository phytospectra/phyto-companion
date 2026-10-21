"""FastAPI entrypoint for the orchestrator service.

Local plane: every tool runs through the deterministic mock executor and
``--dry-run`` semantics; no Docker, no GCP credentials, no network.
Cloud plane: the same endpoints delegate to the real tool services
(integrated in Steps 4-6) -- the API surface does not change.

Run locally::

    uvicorn app.main:app --app-dir services/orchestrator --port 8000
"""

from __future__ import annotations

import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.constants import ToolTier
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict
from phyto_shared.schemas import AnalysisRequest, AnalysisResponse

from . import __version__
from .audit_logger import ChainedAuditLogger
from .planner_agent import EngineResult, PlannerAgent, TaskGraph, WorkflowEngine
from .qa_critic_agent import QACriticAgent
from .tool_registry import DEFAULT_REGISTRY, MockToolExecutor
from .tool_tiering import ExecutionContext, HumanCheckpoint

app = FastAPI(
    title="PhytoCompanion Orchestrator",
    version=__version__,
    description="Plans, delegates, verifies, and logs analysis workflows.",
)


# ---------------------------------------------------------------------------
# Runtime wiring (lazy so tests can point the audit log at a temp dir)
# ---------------------------------------------------------------------------


class _Runtime:
    """Everything the API needs, wired for the local plane."""

    def __init__(self) -> None:
        self.environment: Literal["local", "cloud"] = (
            "cloud" if os.environ.get("PHYTO_ENV") == "cloud" else "local"
        )
        audit_path = os.environ.get("PHYTO_AUDIT_LOG") or str(
            Path(tempfile.gettempdir()) / "phyto_companion_orchestrator_audit.jsonl"
        )
        self.registry = DEFAULT_REGISTRY
        self.audit = ChainedAuditLogger(audit_path)
        self.planner = PlannerAgent(self.registry)
        self.engine = WorkflowEngine(
            registry=self.registry,
            executor=MockToolExecutor(self.registry),
            audit=self.audit,
            qa_critic=QACriticAgent(),  # rule-based verification, no LLM needed
        )
        # run_id -> {"graph": TaskGraph, "context": ExecutionContext, "result": EngineResult}
        self.runs: dict[str, dict[str, Any]] = {}
        self.manifests: dict[str, str] = {}


@lru_cache(maxsize=1)
def get_runtime() -> _Runtime:
    return _Runtime()


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


class TaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    tool: str | None
    status: str
    requires_checkpoint: str | None
    error: str | None = None


class RunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow: str
    status: Literal["complete", "failed", "awaiting_review"]
    awaiting_task_id: str | None
    failed_task_id: str | None
    task_counts: dict[str, int]
    tasks: list[TaskSummary]
    provenance_manifest_id: str


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint: HumanCheckpoint
    actor: str = Field(..., min_length=1)


class ApproveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    approved: HumanCheckpoint
    status: Literal["complete", "failed", "awaiting_review"]
    awaiting_task_id: str | None
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_status_payload(run_id: str, runtime: _Runtime) -> RunStatusResponse:
    entry = runtime.runs[run_id]
    graph: TaskGraph = entry["graph"]
    result: EngineResult = entry["result"]
    return RunStatusResponse(
        run_id=run_id,
        workflow=graph.workflow,
        status=result.status,
        awaiting_task_id=result.awaiting_task_id,
        failed_task_id=result.failed_task_id,
        task_counts=graph.counts(),
        tasks=[
            TaskSummary(
                id=task.id,
                description=task.description,
                tool=task.tool,
                status=task.status,
                requires_checkpoint=(
                    str(task.requires_checkpoint)
                    if task.requires_checkpoint
                    else None
                ),
                error=task.error,
            )
            for task in graph.tasks
        ],
        provenance_manifest_id=runtime.manifests[run_id],
    )


def _build_manifest(run_id: str, request: AnalysisRequest) -> str:
    manifest = (
        ProvenanceBuilder()
        .with_input_hash("analysis_request", sha256_dict(request.model_dump()))
        .with_model_version("orchestrator", __version__)
        .with_environment()
        .with_parameters(
            {"workflow": request.workflow, "dry_run": request.dry_run}
        )
        .build()
    )
    return manifest.manifest_hash


def _status_literal(result: EngineResult) -> Literal[
    "queued", "running", "complete", "failed", "awaiting_review"
]:
    return result.status  # engine statuses are already valid API statuses


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    runtime = get_runtime()
    return {
        "status": "ok",
        "service": "orchestrator",
        "version": __version__,
        "environment": runtime.environment,
        "registry_size": len(runtime.registry),
        "audit_records": len(runtime.audit.records()),
    }


@app.get("/tools")
def list_tools(
    tier: int | None = None,
    enabled_in_dry_run: bool | None = None,
) -> list[dict[str, Any]]:
    runtime = get_runtime()
    tier_enum = ToolTier(tier) if tier is not None else None
    return [
        {
            "name": spec.name,
            "tier": int(spec.tier),
            "description": spec.description,
            "estimated_cost_seconds": spec.estimated_cost_seconds,
            "requires_network": spec.requires_network,
            "requires_gpu": spec.requires_gpu,
            "enabled_in_dry_run": spec.enabled_in_dry_run,
        }
        for spec in runtime.registry.list_tools(
            tier=tier_enum, enabled_in_dry_run=enabled_in_dry_run
        )
    ]


@app.post("/analysis", response_model=AnalysisResponse, status_code=200)
def submit_analysis(request: AnalysisRequest) -> AnalysisResponse:
    runtime = get_runtime()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    manifest_id = _build_manifest(run_id, request)
    runtime.manifests[run_id] = manifest_id

    try:
        graph = runtime.planner.plan(
            run_id=run_id, workflow=request.workflow, dry_run=request.dry_run
        )
    except KeyError:
        raise HTTPException(
            status_code=422, detail=f"unknown workflow '{request.workflow}'"
        ) from None

    # QA/Critic is registered -> verification capacity is available.  Human
    # approval for Tier 3 starts withheld; checkpoint approvals grant it.
    context = ExecutionContext(
        run_id=run_id,
        dry_run=request.dry_run,
        verification_available=True,
        human_in_the_loop_approved=False,
        actor="api",
    )
    result = runtime.engine.run(graph, context)
    runtime.runs[run_id] = {"graph": graph, "context": context, "result": result}

    awaiting = result.awaiting_task_id
    return AnalysisResponse(
        run_id=run_id,
        status=_status_literal(result),
        provenance_manifest_id=manifest_id,
        summary={
            "workflow": request.workflow,
            "dry_run": request.dry_run,
            "task_counts": graph.counts(),
            "awaiting_task_id": awaiting,
            "cost_estimate_usd": result.cost_estimate_usd,
            "wall_seconds": result.wall_seconds,
        },
    )


@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    runtime = get_runtime()
    if run_id not in runtime.runs:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return _run_status_payload(run_id, runtime)


@app.post("/runs/{run_id}/approve", response_model=ApproveResponse)
def approve_checkpoint(run_id: str, request: ApproveRequest) -> ApproveResponse:
    runtime = get_runtime()
    if run_id not in runtime.runs:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")

    entry = runtime.runs[run_id]
    graph: TaskGraph = entry["graph"]
    context: ExecutionContext = entry["context"]
    result: EngineResult = entry["result"]

    if result.status != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"run is '{result.status}', nothing to approve",
        )
    awaiting_task = graph.task_by_id(result.awaiting_task_id or "")
    if awaiting_task.requires_checkpoint != request.checkpoint:
        raise HTTPException(
            status_code=409,
            detail=(
                f"run awaits '{awaiting_task.requires_checkpoint}', not "
                f"'{request.checkpoint}'"
            ),
        )

    new_context = context.with_approval(request.checkpoint, actor=request.actor)
    new_result = runtime.engine.run(graph, new_context)
    runtime.runs[run_id] = {
        "graph": graph,
        "context": new_context,
        "result": new_result,
    }
    return ApproveResponse(
        run_id=run_id,
        approved=request.checkpoint,
        status=new_result.status,
        awaiting_task_id=new_result.awaiting_task_id,
        provenance_manifest_id=runtime.manifests[run_id],
    )


@app.get("/audit/verify")
def verify_audit() -> dict[str, Any]:
    runtime = get_runtime()
    try:
        valid = runtime.audit.verify_chain()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"chain_valid": valid, "records": len(runtime.audit.records())}


@app.get("/audit/records")
def audit_records(run_id: str | None = None) -> list[dict[str, Any]]:
    runtime = get_runtime()
    records = runtime.audit.records()
    if run_id is not None:
        records = [r for r in records if r.get("run_id") == run_id]
    return records
