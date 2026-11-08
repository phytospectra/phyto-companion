"""FastAPI entrypoint for the orchestrator service.

Scientific execution is explicitly configured independently of auth dry-run.
Offline mock templates and bounded measured-reference HSQC ranking are separate
capabilities; cloud mode never silently selects synthetic scientific results.

Growth features hosted here (the auth service hosts the account surface):

* Citations: every completed run mints a citable identifier;
  ``GET /runs/{run_id}/citation`` is free (ADR-0043).
* Share URLs: HMAC-signed read-only links, unauthenticated
  ``GET /share/{token}`` with an "Open in Phytospectra" CTA (ADR-0044).
* Onboarding: ``GET /onboarding/first-story`` (ADR-0045).
* Cohort analytics: admin endpoints over the audit chain (ADR-0048).
* Audit export: department-and-above plan gate (ADR-0047).

Run locally::

    uvicorn app.main:app --app-dir services/orchestrator --port 8000
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.analytics import emit_event
from phyto_shared.citation import (
    CitationRecord,
    MockDOIRegistrar,
    build_citation_record,
    doi_from_identifier,
    mint_analysis_doi,
)
from phyto_shared.constants import ToolTier, parse_observation_modalities
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.plans import plan_features
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict
from phyto_shared.schemas import AnalysisRequest, AnalysisResponse
from phyto_shared.share import (
    InMemoryShareRegistry,
    ShareTokenManager,
    share_url_for,
)
from phyto_shared.token_pricing import COST_MODEL_VERSION, cost_for_route

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .audit_logger import ChainedAuditLogger
from .cohort_analytics import InMemoryCohortStore, run_cohort_job
from .execution_config import ScientificExecutionConfig, build_tool_executor
from .onboarding import first_story_summary
from .planner_agent import EngineResult, PlannerAgent, TaskGraph, TaskStatus, WorkflowEngine
from .qa_critic_agent import QACriticAgent
from .rate_limit import DEFAULT_SHARE_RATE_LIMIT, SlidingWindowRateLimiter
from .tool_registry import DEFAULT_REGISTRY
from .tool_tiering import ExecutionContext, HumanCheckpoint

router = APIRouter()

logger = logging.getLogger(__name__)


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
        self.scientific_config = ScientificExecutionConfig.from_env()
        self.executor = build_tool_executor(self.registry, self.scientific_config)
        self.engine = WorkflowEngine(
            registry=self.registry,
            executor=self.executor,
            audit=self.audit,
            qa_critic=QACriticAgent(),  # rule-based verification, no LLM needed
        )
        # run_id -> {"graph", "context", "result", "request", "citation",
        #            "share_token"}
        self.runs: dict[str, dict[str, Any]] = {}
        self.manifests: dict[str, str] = {}
        # Share tokens: HMAC-signed read-only links (ADR-0044).  The dev
        # HMAC key is fine only in dry-run; from_env refuses to mint with
        # it in production.  The registry is what makes revocation real:
        # a manager without one can mint and verify but never revoke.
        self.share_registry = InMemoryShareRegistry()
        self.share_manager = ShareTokenManager.from_env(self.share_registry)
        self.rate_limiter = SlidingWindowRateLimiter(
            max_requests=int(
                os.environ.get("SHARE_RATE_LIMIT_PER_MIN", str(DEFAULT_SHARE_RATE_LIMIT))
            )
        )
        self.cohort_store = InMemoryCohortStore()
        # DOI registration is deferred and gated (ADR-0043): without
        # ZENODO_TOKEN the canonical id is used and this stays None.
        self.doi_registrar = (
            MockDOIRegistrar()
            if self.executor.mode == "mock" and os.environ.get("PHYTO_DRY_RUN", "1") == "1"
            else None
        )


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
    citation: CitationRecord | None = None
    modalities: list[str] = Field(
        default_factory=list,
        description="Observation modalities this run was planned for; empty "
        "when the request did not declare any",
    )
    pruned_tasks: list[dict[str, str]] = Field(
        default_factory=list,
        description="Planned steps dropped as impossible for the supplied "
        "evidence, each with the reason (ADR-0050)",
    )
    evidence_gap: str | None = Field(
        default=None,
        description="What the supplied modality set cannot establish",
    )


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
    citation: CitationRecord | None = None


class ShareCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share_url: str
    expires_at: str
    scope: str


class ShareCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_in_days: int = Field(default=30, ge=1, le=365)


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
        citation=entry.get("citation"),
        modalities=graph.modalities,
        pruned_tasks=[
            {"id": pruned.id, "description": pruned.description, "reason": pruned.reason}
            for pruned in graph.pruned
        ],
        evidence_gap=graph.evidence_gap,
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


def _request_user(request: Request | None):
    return getattr(request.state, "user", None) if request is not None else None


def _tier_for_plan(plan: str | None) -> str:
    """Model tier the run's user is routed to (ADR-0046)."""
    if not plan:
        return "unknown"
    return "distilled" if plan_features(plan).distilled_llm else "full"


def _run_principal(request: Request) -> str | None:
    """UID when authenticated; anonymous runs exist only on the local offline plane."""
    user = _request_user(request)
    if user is not None:
        return user.uid
    from auth.config import AuthConfig

    config = AuthConfig.from_env()
    if os.getenv("PHYTO_ENV", "local") == "local" and not config.enabled:
        return None
    raise HTTPException(status_code=401, detail="Authentication required")


def _owned_run(runtime: _Runtime, run_id: str, request: Request) -> dict[str, Any]:
    principal = _run_principal(request)
    entry = runtime.runs.get(run_id)
    # Missing legacy owner metadata is denied, never implicitly public.
    if entry is None or "owner_uid" not in entry or entry["owner_uid"] != principal:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return entry


def _analytics_user(request: Request | None) -> str:
    user = _request_user(request)
    return user.uid if user is not None else "anonymous-local"


def _analytics_plan(request: Request | None) -> str | None:
    return getattr(request.state, "plan", None) if request is not None else None


def _analytics_workspace(request: Request | None) -> str | None:
    return getattr(request.state, "workspace_id", None) if request is not None else None


def _record_run_events(
    runtime: _Runtime,
    *,
    request: Request | None,
    run_id: str,
    analysis: AnalysisRequest,
    result: EngineResult,
) -> None:
    """Emit the spec'd cohort events for one run (ADR-0048)."""
    user_id = _analytics_user(request)
    plan = _analytics_plan(request)
    emit_event(
        runtime.audit,
        "run_started",
        user_id=user_id,
        run_id=run_id,
        properties={
            "run_id": run_id,
            "workspace_id": _analytics_workspace(request),
            "plan": plan,
        },
    )
    if result.status == "complete":
        tokens_consumed = (
            cost_for_route("POST", "/analysis").tokens
            if cost_for_route("POST", "/analysis")
            else 0
        )
        emit_event(
            runtime.audit,
            "run_completed",
            user_id=user_id,
            run_id=run_id,
            properties={
                "run_id": run_id,
                "tokens_consumed": tokens_consumed,
                "model_tier": _tier_for_plan(plan),
                "cost_usd": result.cost_estimate_usd,
            },
        )


def _mint_citation(
    runtime: _Runtime,
    *,
    run_id: str,
    analysis: AnalysisRequest,
    request: Request | None,
) -> CitationRecord:
    """Mint (once per run) the citable identifier (ADR-0043)."""
    if runtime.executor.mode != "mock":
        raise HTTPException(status_code=503, detail="No real DOI registrar is configured")
    entry = runtime.runs[run_id]
    if entry.get("citation") is not None:
        return entry["citation"]

    user = _request_user(request)
    authors = [user.email or user.uid] if user is not None else ["Anonymous"]
    title = (
        f"Phytospectra analysis of sample {analysis.sample_id} "
        f"({analysis.workflow} workflow)"
    )
    chain_head = (
        runtime.audit.records()[-1]["record_hash"]
        if runtime.audit.records()
        else "0" * 64
    )
    identifier = mint_analysis_doi(
        run_id,
        {
            "title": title,
            "authors": authors,
            "description": f"Provenance manifest {runtime.manifests[run_id]}",
            "provenance_manifest_hash": runtime.manifests[run_id],
            "audit_chain_head": chain_head,
        },
        registrar=runtime.doi_registrar,
    )
    record = build_citation_record(
        run_id=run_id,
        title=title,
        authors=authors,
        model_version=f"orchestrator:{__version__}",
        cost_model_version=COST_MODEL_VERSION,
        provenance_manifest_hash=runtime.manifests[run_id],
        audit_chain_head=chain_head,
        doi=doi_from_identifier(identifier),
    )
    entry["citation"] = record
    return record


_REDACT_KEYS = frozenset({"user_id", "email", "uid", "actor", "user_email"})


def _redact(value: Any) -> Any:
    """Strip identity fields from a share view (ADR-0044: no user data)."""
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if key not in _REDACT_KEYS
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


_EVIDENCE_OUTPUT_KEYS = frozenset(
    {"grounding_score", "explanation", "hypothesis", "evidence", "confidence",
     # Ranking/proposal outputs feed the web app report view (the
     # read-only share view is the documented evidence surface).
     "ranks", "candidate_ranks", "proposals", "assignments", "peaks",
     "ranking", "claim"}
)


def _share_view(run_id: str, runtime: _Runtime) -> dict[str, Any]:
    """The read-only view a share token unlocks (ADR-0044).

    Sample metadata, task graph, artifacts, citation, evidence panels and
    the (redacted) audit tail.  Never the user's identity, never raw
    uploads (the orchestrator never holds them; ingestion does).
    """
    entry = runtime.runs[run_id]
    graph: TaskGraph = entry["graph"]
    result: EngineResult = entry["result"]
    analysis: AnalysisRequest = entry["request"]

    tasks = [
        {
            "id": task.id,
            "description": task.description,
            "tool": task.tool,
            "status": task.status,
            "requires_checkpoint": (
                str(task.requires_checkpoint) if task.requires_checkpoint else None
            ),
        }
        for task in graph.tasks
    ]
    artifacts = {
        task.id: {
            "tool": task.tool,
            "status": task.status,
            "output_keys": sorted(task.output.keys()) if isinstance(task.output, dict) else [],
        }
        for task in graph.tasks
        if task.status == "complete" and task.tool is not None
    }
    evidence_panels = [
        {
            "task_id": task.id,
            "tool": task.tool,
            "output": _redact(task.output),
        }
        for task in graph.tasks
        if isinstance(task.output, dict)
        and _EVIDENCE_OUTPUT_KEYS & set(task.output.keys())
    ]
    audit_tail = [
        _redact(record)
        for record in runtime.audit.records()
        if record.get("run_id") == run_id
    ][-50:]

    citation = entry.get("citation")
    return {
        "run_id": run_id,
        "status": result.status,
        "sample": {
            "sample_id": analysis.sample_id,
            "workflow": analysis.workflow,
            "parameters": analysis.parameters,
            "dry_run": analysis.dry_run,
        },
        "task_graph": tasks,
        "artifacts": artifacts,
        "evidence_panels": evidence_panels,
        "audit_tail": audit_tail,
        "citation": citation.model_dump(mode="json") if citation else None,
        "note": "read-only shared view; raw uploads are never exposed",
        "cta": {
            "label": "Open in Phytospectra",
            "url": (
                f"{os.environ.get('PHYTO_APP_BASE_URL', 'https://www.phytospectra.com')}"
                "/analysis/new"
            ),
            "prefill": {
                "sample_id": analysis.sample_id,
                "workflow": analysis.workflow,
            },
        },
    }


def _require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    admin_uids = {
        uid.strip()
        for uid in os.environ.get("PHYTO_ADMIN_UIDS", "").split(",")
        if uid.strip()
    }
    if user.claims.get("role") != "admin" and user.uid not in admin_uids:
        raise HTTPException(status_code=403, detail="Admin role required.")


def _require_plan(request: Request, minimum: str) -> None:
    from phyto_shared.plans import plan_at_or_above

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    plan = getattr(request.state, "plan", None) or user.claims.get("plan")
    if not plan_at_or_above(plan, minimum):
        raise HTTPException(
            status_code=403,
            detail=f"Requires the {minimum} plan or above (current: {plan or 'free'}).",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tools")
def list_tools(
    tier: int | None = None,
    enabled_in_dry_run: bool | None = None,
) -> list[dict[str, Any]]:
    runtime = get_runtime()
    tier_enum = ToolTier(tier) if tier is not None else None
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "tier": int(spec.tier),
            "estimated_cost_seconds": spec.estimated_cost_seconds,
            "requires_network": spec.requires_network,
            "requires_gpu": spec.requires_gpu,
            "enabled_in_dry_run": spec.enabled_in_dry_run,
        }
        for spec in runtime.registry.list_tools(
            tier=tier_enum, enabled_in_dry_run=enabled_in_dry_run
        )
    ]


def _isolation_layer_status(graph: TaskGraph) -> dict[str, str]:
    """Visible report line for the isolation layer (ADR-0055).

    Applied / skipped / not-planned, derived from the graph's own task
    outputs so the report cannot disagree with what actually ran.
    """
    task = next((t for t in graph.tasks if t.id == "load-isolation-record"), None)
    if task is None:
        return {"isolation_layer": "skipped: no record"}
    if task.status != TaskStatus.COMPLETE:
        return {"isolation_layer": f"skipped: task {task.status}"}
    if (task.output or {}).get("record") is None:
        return {"isolation_layer": "skipped: no record"}
    elucidate = graph.task_by_id("elucidate")
    applied = bool((elucidate.output or {}).get("isolation_applied")) if elucidate else False
    return {"isolation_layer": "applied" if applied else "skipped: no compounds"}


def _chromatography_layers_status(graph: TaskGraph) -> dict[str, Any]:
    """Visible report line for the chromatography evidence layers
    (ADR-0061..0066): which ran and which were skipped with why, derived
    from the apply-chromatography-layers task's own output so the report
    cannot disagree with what actually executed.
    """
    task = next(
        (t for t in graph.tasks if t.id == "apply-chromatography-layers"), None
    )
    if task is None:
        return {"chromatography_layers": "skipped: no metadata"}
    if task.status != TaskStatus.COMPLETE:
        return {"chromatography_layers": f"skipped: task {task.status}"}
    output = task.output or {}
    applied = output.get("layers_applied") or []
    skipped = output.get("skipped_layers") or []
    if not applied:
        return {
            "chromatography_layers": "skipped: " + (
                "; ".join(
                    f"{s.get('layer')}: {s.get('reason', '')}"
                    for s in skipped
                )
                or "no layer metadata supplied"
            )
        }
    # MSI level as the standard defines the levels (Sumner 2007,
    # ADR-0075): the summary reports the level the top rank's evidence
    # actually carries (attributed by the ranker/calibration layer), not
    # a blanket 1-or-2 guess.  "no Level-1" must never read as "no
    # match"; the Level-1 box appears only when a home-library standard
    # matched on orthogonal properties.
    ranks = output.get("ranks") or []
    top = ranks[0] if ranks else None
    msi_level = (
        (top or {}).get("msi_level")
        or (1 if (top or {}).get("level_1_match") else 2)
    )
    return {
        "chromatography_layers": {
            "applied": applied,
            "skipped": [
                {"layer": s.get("layer"), "reason": s.get("reason")}
                for s in skipped
            ],
            "top_candidate_msi_level": msi_level,
        }
    }


@router.post("/analysis", response_model=AnalysisResponse, status_code=200)
def submit_analysis(request: AnalysisRequest, http_request: Request) -> AnalysisResponse:
    runtime = get_runtime()
    owner_uid = _run_principal(http_request)
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    manifest_id = _build_manifest(run_id, request)
    runtime.manifests[run_id] = manifest_id

    try:
        modalities = parse_observation_modalities(request.modalities)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        if request.sample_metadata is not None and request.sample_metadata.solvent_warning():
            # Unknown solvent is a warning, not an error (ADR-0052): the
            # solvent compatibility filter is skipped, the run proceeds.
            logger.warning("%s", request.sample_metadata.solvent_warning())
        graph = runtime.planner.plan(
            run_id=run_id,
            workflow=request.workflow,
            dry_run=request.dry_run,
            modalities=modalities,
            sample_metadata=request.sample_metadata,
            parameters=request.parameters,
        )
        if runtime.executor.mode == "real":
            if request.dry_run:
                raise InvalidInputError("Real scientific workflows do not accept request dry_run=true")
            if request.workflow not in runtime.executor.supported_workflows:
                raise InvalidInputError("Workflow unsupported by the configured real executor")
        runtime.executor.preflight([t.tool for t in graph.tasks if t.tool is not None])
    except KeyError:
        raise HTTPException(
            status_code=422, detail=f"unknown workflow '{request.workflow}'"
        ) from None
    except (InvalidInputError, ValueError) as exc:
        # Unsupported real workflows and invalid observation contracts are rejected before execution. of this workflow needs a modality the user did not
        # supply: refuse rather than return a run that did nothing (ADR-0050).
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
    runtime.runs[run_id] = {
        "owner_uid": owner_uid,
        "graph": graph,
        "context": context,
        "result": result,
        "request": request,
        "citation": None,
        "share_token": None,
    }

    _record_run_events(runtime, request=http_request, run_id=run_id, analysis=request, result=result)
    if result.status == "complete" and runtime.executor.mode == "mock":
        # Minted for its stored side effect; GET /runs/{id}/citation reads it
        # back from the run entry (the create response carries no citation).
        _mint_citation(
            runtime, run_id=run_id, analysis=request, request=http_request
        )

    awaiting = result.awaiting_task_id
    return AnalysisResponse(
        run_id=run_id,
        status=_status_literal(result),
        provenance_manifest_id=manifest_id,
        summary={
            "workflow": request.workflow,
            "dry_run": request.dry_run,
            "scientific_execution": runtime.executor.mode,
            "tool_executor": type(runtime.executor).__name__,
            "scientific_validation": "not_validated",
            "scientific_results": [
                {"task_id": t.id, "tool": t.tool, "output": t.output}
                for t in graph.tasks
                if t.tool == "reference_hsqc.rank" and t.output is not None
            ],
            "task_counts": graph.counts(),
            "awaiting_task_id": awaiting,
            "cost_estimate_usd": result.cost_estimate_usd,
            "wall_seconds": result.wall_seconds,
            "modalities": graph.modalities,
            "pruned_tasks": [p.id for p in graph.pruned],
            "evidence_gap": graph.evidence_gap,
            **_isolation_layer_status(graph),
            **_chromatography_layers_status(graph),
        },
    )


@router.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str, http_request: Request) -> RunStatusResponse:
    runtime = get_runtime()
    _owned_run(runtime, run_id, http_request)
    return _run_status_payload(run_id, runtime)


@router.post("/runs/{run_id}/approve", response_model=ApproveResponse)
def approve_checkpoint(
    run_id: str, request: ApproveRequest, http_request: Request
) -> ApproveResponse:
    runtime = get_runtime()
    entry = _owned_run(runtime, run_id, http_request)
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
        "owner_uid": entry["owner_uid"],
        "graph": graph,
        "context": new_context,
        "result": new_result,
        "request": entry["request"],
        "citation": entry.get("citation"),
        "share_token": entry.get("share_token"),
    }
    entry = runtime.runs[run_id]
    citation = None
    if new_result.status == "complete" and runtime.executor.mode == "mock":
        _record_run_events(
            runtime,
            request=http_request,
            run_id=run_id,
            analysis=entry["request"],
            result=new_result,
        )
        citation = _mint_citation(
            runtime, run_id=run_id, analysis=entry["request"], request=http_request
        )
    else:
        citation = None
    return ApproveResponse(
        run_id=run_id,
        approved=request.checkpoint,
        status=new_result.status,
        awaiting_task_id=new_result.awaiting_task_id,
        provenance_manifest_id=runtime.manifests[run_id],
        citation=citation,
    )


# -- citations (ADR-0043) ----------------------------------------------------


@router.get("/runs/{run_id}/citation")
def get_citation(run_id: str, http_request: Request) -> dict[str, Any]:
    """The run's citable record.  Free by design: metering citation
    retrieval would stop users citing, which is the whole point."""
    runtime = get_runtime()
    entry = _owned_run(runtime, run_id, http_request)
    if entry.get("citation") is None:
        if runtime.executor.mode != "mock":
            raise HTTPException(
                status_code=503,
                detail="No real DOI registrar is configured; no synthetic DOI was minted",
            )
        raise HTTPException(
            status_code=409,
            detail="run has not completed; citations are minted on completion",
        )
    record: CitationRecord = entry["citation"]
    # The share URL is an alternate access link: surface the current one.
    share_token = entry.get("share_token")
    record = record.model_copy(
        update={"share_url": share_url_for(share_token) if share_token else None}
    )
    already_cited = any(
        r.get("event") == "run_cited" and r.get("run_id") == run_id
        for r in runtime.audit.records()
    )
    if not already_cited:
        emit_event(
            runtime.audit,
            "run_cited",
            user_id=_analytics_user(http_request),
            run_id=run_id,
            properties={"citation_id": record.canonical_id, "run_id": run_id},
        )
    return record.model_dump(mode="json")


# -- share URLs (ADR-0044) -----------------------------------------------------


@router.post("/runs/{run_id}/share", response_model=ShareCreateResponse)
def create_share(run_id: str, request: ShareCreateRequest, http_request: Request) -> ShareCreateResponse:
    runtime = get_runtime()
    _owned_run(runtime, run_id, http_request)
    token, claims = runtime.share_manager.create_token(
        run_id, expires_in_days=request.expires_in_days
    )
    runtime.runs[run_id]["share_token"] = token
    return ShareCreateResponse(
        share_url=share_url_for(token),
        expires_at=claims.expires_at.isoformat(),
        scope=claims.scope,
    )


@router.delete("/runs/{run_id}/share")
def revoke_shares(run_id: str, http_request: Request) -> dict[str, Any]:
    runtime = get_runtime()
    _owned_run(runtime, run_id, http_request)
    registry = runtime.share_manager.registry
    revoked = registry.revoke_all(run_id) if registry is not None else 0
    runtime.runs[run_id]["share_token"] = None
    return {"run_id": run_id, "revoked": revoked}


def _verify_share_token(token: str):
    from phyto_shared.share import (
        ExpiredShareTokenError,
        InvalidShareTokenError,
        RevokedShareTokenError,
    )

    runtime = get_runtime()
    try:
        claims = runtime.share_manager.verify(token)
    except ExpiredShareTokenError:
        raise HTTPException(status_code=410, detail="share token has expired") from None
    except RevokedShareTokenError:
        raise HTTPException(status_code=410, detail="share token was revoked") from None
    except InvalidShareTokenError:
        raise HTTPException(status_code=404, detail="unknown share token") from None
    if claims.run_id not in runtime.runs:
        raise HTTPException(status_code=404, detail="unknown share token")
    return claims


def _rate_limit_share(http_request: Request) -> None:
    runtime = get_runtime()
    client_ip = http_request.client.host if http_request.client else "unknown"
    if not runtime.rate_limiter.allow(client_ip):
        raise HTTPException(
            status_code=429,
            detail="share read rate limit exceeded; try again in a minute",
        )


@router.get("/share/{share_token}")
def read_share(share_token: str, http_request: Request) -> dict[str, Any]:
    """Unauthenticated read-only run view; the signed token is the
    credential (ADR-0044).  Rate-limited per IP."""
    _rate_limit_share(http_request)
    claims = _verify_share_token(share_token)
    runtime = get_runtime()
    emit_event(
        runtime.audit,
        "run_shared",
        user_id=_analytics_user(http_request),
        run_id=claims.run_id,
        properties={"share_token": claims.token_id, "run_id": claims.run_id},
    )
    return _share_view(claims.run_id, runtime)


@router.get("/share/{share_token}/artifacts/{name}")
def read_share_artifact(
    share_token: str, name: str, http_request: Request
) -> dict[str, Any]:
    _rate_limit_share(http_request)
    claims = _verify_share_token(share_token)
    runtime = get_runtime()
    entry = runtime.runs[claims.run_id]
    graph: TaskGraph = entry["graph"]
    task = next((t for t in graph.tasks if t.id == name), None)
    if task is None:
        raise HTTPException(
            status_code=404, detail=f"unknown artifact '{name}' for this run"
        )
    return {
        "run_id": claims.run_id,
        "artifact": name,
        "tool": task.tool,
        "status": task.status,
        "output": _redact(task.output),
    }


# -- onboarding (ADR-0045) ------------------------------------------------------


@router.get("/onboarding/first-story")
def onboarding_first_story() -> dict[str, Any]:
    return first_story_summary()


# -- cohort analytics (ADR-0048) -------------------------------------------------


@router.get("/admin/cohorts")
def admin_cohorts(request: Request) -> dict[str, Any]:
    _require_admin(request)
    runtime = get_runtime()
    cohorts = runtime.cohort_store.all()
    return {
        "cohorts": [
            cohorts[month].model_dump(mode="json") for month in sorted(cohorts)
        ],
        "alerts": [alert.model_dump(mode="json") for alert in runtime.cohort_store.alerts()],
    }


@router.post("/admin/cohorts/recompute")
def admin_cohorts_recompute(request: Request) -> dict[str, Any]:
    """Run the cohort job over this service's audit chain now (the daily
    schedule calls the same function)."""
    _require_admin(request)
    runtime = get_runtime()
    return run_cohort_job(runtime.audit.records(), runtime.cohort_store)


# -- audit export (department plan, ADR-0047) ------------------------------------


@router.get("/audit/export")
def audit_export(request: Request) -> dict[str, Any]:
    _require_plan(request, "department")
    runtime = get_runtime()
    return {
        "records": runtime.audit.records(),
        "count": len(runtime.audit.records()),
        "chain_valid": runtime.audit.verify_chain(),
    }


@router.get("/audit/verify")
def verify_audit() -> dict[str, Any]:
    runtime = get_runtime()
    try:
        valid = runtime.audit.verify_chain()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"chain_valid": valid, "records": len(runtime.audit.records())}


@router.get("/audit/records")
def audit_records(run_id: str | None = None) -> list[dict[str, Any]]:
    runtime = get_runtime()
    records = runtime.audit.records()
    if run_id is not None:
        records = [r for r in records if r.get("run_id") == run_id]
    return records


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed.

    Middleware is installed before routes are added so no endpoint can be
    registered outside the metering boundary.
    """
    application = FastAPI(
        title="PhytoCompanion Orchestrator",
        version=__version__,
        description="Plans, delegates, verifies, and logs analysis workflows.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        runtime = get_runtime()
        return {
            "status": "ok",
            "service": "orchestrator",
            "version": __version__,
            "environment": runtime.environment,
            "registry_size": len(runtime.registry),
            "audit_records": len(runtime.audit.records()),
            "dry_run": cfg.dry_run,
            "tool_executor": type(runtime.executor).__name__,
            "scientific_execution": runtime.executor.mode,
            "scientific_ready": False,
            "scientific_validation": "not_validated",
            "execution_readiness": runtime.executor.health(),
        }

    return application


app = create_app()
