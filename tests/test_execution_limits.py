"""Execution-limit and restore-safety regressions (2026-11-12 review round).

Covers the two newly enforceable resource boundaries: the ingress payload cap
(413 before any handler or accounting effect) and the cooperative per-pass
execution budget (a run fails explicitly instead of silently overrunning its
wall-clock allowance), plus the restore audit that surfaces key/accounting
interactions of a database restore.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from orchestrator import main
from orchestrator.planner_agent import Task, TaskGraph, WorkflowEngine
from orchestrator.run_store import SQLiteRunStore, new_run_id
from orchestrator.tool_registry import DEFAULT_REGISTRY
from orchestrator.tool_tiering import ExecutionContext


class SlowMockExecutor:
    """The real mock executor wrapped with a per-call delay."""

    mode = "mock"

    def __init__(self, delay: float):
        from orchestrator.tool_registry import MockToolExecutor

        self._inner = MockToolExecutor(DEFAULT_REGISTRY)
        self.delay = delay

    def preflight(self, tool_names):
        return self._inner.preflight(tool_names)

    def execute(self, tool, inputs):
        time.sleep(self.delay)
        return self._inner.execute(tool, inputs)

    def provenance(self, tool):
        return self._inner.provenance(tool)

    def health(self):
        return self._inner.health()


def make_two_task_graph(run_id: str) -> TaskGraph:
    tool = "rdkit.formula"
    return TaskGraph(
        run_id=run_id,
        workflow="reference_hsqc_identify",
        dry_run=True,
        tasks=[
            Task(id="step-a", description="step a", tool=tool, inputs={"smiles": "CCO"}),
            Task(id="step-b", description="step b", tool=tool, inputs={"smiles": "CCO"}),
        ],
    )


class CollectingAudit:
    """Minimal audit sink recording event names."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def log_event(self, run_id=None, event=None, detail=None):
        self.events.append((event, detail or {}))

    def log_tool_call(self, **kwargs):
        return None

    def records(self):
        return [{"event": event, **detail} for event, detail in self.events]


def test_execution_budget_fails_remaining_work_between_tasks():
    registry = DEFAULT_REGISTRY
    audit = CollectingAudit()
    engine = WorkflowEngine(
        registry=registry,
        executor=SlowMockExecutor(delay=0.2),
        audit=audit,
        qa_critic=None,
    )
    graph = make_two_task_graph(new_run_id())
    context = ExecutionContext(
        run_id=graph.run_id,
        dry_run=True,
        execution_budget_seconds=0.1,
    )
    result = engine.run(graph, context)
    # The first task completed (it started inside the budget); the second was
    # refused with an explicit failure rather than silent partial success.
    assert result.status == "failed"
    assert result.failed_task_id == "step-b"
    assert graph.task_by_id("step-a").status == "complete"
    assert graph.task_by_id("step-b").status == "failed"
    assert "execution budget exceeded" in graph.task_by_id("step-b").error
    assert any(event == "execution_budget_exceeded" for event, _ in audit.events)


def test_generous_budget_does_not_interfere():
    audit = CollectingAudit()
    engine = WorkflowEngine(
        registry=DEFAULT_REGISTRY,
        executor=SlowMockExecutor(delay=0.01),
        audit=audit,
        qa_critic=None,
    )
    graph = make_two_task_graph(new_run_id())
    result = engine.run(
        graph,
        ExecutionContext(run_id=graph.run_id, dry_run=True,
                         execution_budget_seconds=5.0),
    )
    assert result.status == "complete"
    assert all(
        task.status == "complete" for task in graph.tasks if task.tool is not None
    )


@pytest.fixture()
def limits_app(monkeypatch, tmp_path):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "0")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_RUN_STORE", "sqlite")
    monkeypatch.setenv("PHYTO_RUN_STORE_PATH", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("PHYTO_MAX_REQUEST_BYTES", "2048")
    main.get_runtime.cache_clear()
    app = main.create_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, str(tmp_path / "runs.sqlite"), main.get_runtime()
    main.get_runtime.cache_clear()


def test_oversized_body_rejected_before_any_handler_effect(limits_app):
    client, path, runtime = limits_app
    before = SQLiteRunStore(path).list_runs("anonymous-local", limit=100)
    oversized = {"sample_id": "x", "workflow": "single_compound", "dry_run": True,
                 "parameters": {"padding": "y" * 4096}}
    response = client.post("/analysis", json=oversized)
    assert response.status_code == 413
    assert "exceeds the configured limit" in response.json()["detail"]
    # Nothing was admitted or executed: no run rows, no idempotency rows.
    store = SQLiteRunStore(path)
    assert store.list_runs("anonymous-local", limit=100) == before
    assert store.orphan_claims() == []
    # A normal-size request still works.
    normal = client.post("/analysis", json={
        "sample_id": "limits-test", "workflow": "single_compound", "dry_run": True,
    })
    assert normal.status_code == 200


def test_api_execution_budget_produces_failed_outcome(limits_app, monkeypatch):
    client, path, runtime = limits_app
    monkeypatch.setenv("PHYTO_EXECUTION_BUDGET_SECONDS", "0.1")
    runtime.execution_budget_seconds = 0.1
    monkeypatch.setattr(runtime.engine, "executor", SlowMockExecutor(delay=0.3))
    response = client.post("/analysis", json={
        "sample_id": "budget-test", "workflow": "single_compound", "dry_run": True,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    run_id = response.json()["run_id"]
    record = SQLiteRunStore(path).load_run(run_id)
    assert record.outcome == "failed"
    assert any(
        "execution budget exceeded" in (task.get("error") or "")
        for task in record.state["graph"]["tasks"]
    )


def test_restore_audit_distinguishes_replays_from_loss(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite")
    snapshot = "2026-11-12T10:00:00+00:00"
    old = _make_record(created="2026-11-12T09:00:00+00:00")
    new = _make_record(created="2026-11-12T11:00:00+00:00")
    store.create_run(old)
    store.create_run(new)
    store.admit(user_uid="alice", idem_key="old-key-0000000001", request_hash="h" * 64, run_id=old.run_id)
    store.admit(user_uid="alice", idem_key="new-key-0000000001", request_hash="h" * 64, run_id=new.run_id)
    audit = store.restore_audit(snapshot=snapshot)
    by_kind = {row["kind"]: row for row in audit}
    assert by_kind["replays_pre_restore_response"]["run_id"] == old.run_id
    assert by_kind["run_created_after_snapshot"]["run_id"] == new.run_id
    # Post-restore (only the old run remains), the audit shows the replaying
    # key and no would-be-lost rows.
    restored = SQLiteRunStore(tmp_path / "runs.sqlite")
    restored._conn.execute("DELETE FROM runs WHERE run_id=?", (new.run_id,))
    post = restored.restore_audit(snapshot=snapshot)
    assert [row["kind"] for row in post] == ["replays_pre_restore_response"]


def _make_record(created: str):
    from orchestrator.run_store import RunRecord

    return RunRecord(
        run_id=new_run_id(),
        owner_uid="alice",
        workflow="single_compound",
        status="awaiting_review",
        outcome="awaiting_review",
        manifest_id="m" * 64,
        state={"graph": {"run_id": "x"}, "request": {"sample_id": "s"}},
        created_at=created,
        updated_at=created,
    )
