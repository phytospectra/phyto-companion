"""Client for the Phytospectra orchestrator API.

Thin, dependency-light (stdlib + httpx fallback to urllib) wrapper around
the same API the web frontend uses: submit analysis, poll status, approve
checkpoints, fetch the citation, and pull the share-view evidence panels
that feed the desktop DOCX report.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import config

DEFAULT_TIMEOUT = 60
POLL_INTERVAL_S = 3.0


class ApiError(RuntimeError):
    """The API returned an error (status + detail carried in the message)."""


def _request(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict | list:
    url = config.api_base().rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode()).get("detail", "")
        except Exception:
            pass
        raise ApiError(f"HTTP {exc.code} on {path}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"cannot reach {config.api_base()}: {exc.reason}") from exc


def health() -> dict:
    return _request("GET", "/health")


def submit_analysis(
    token: str,
    sample_id: str,
    workflow: str,
    modalities: list[str],
    parameters: dict | None = None,
    sample_metadata: dict | None = None,
) -> dict:
    body: dict[str, Any] = {
        "sample_id": sample_id,
        "workflow": workflow,
        "dry_run": False,
        "modalities": modalities,
        "parameters": parameters or {},
    }
    if sample_metadata:
        body["sample_metadata"] = sample_metadata
    return _request("POST", "/analysis", token=token, body=body)


def get_run(token: str, run_id: str) -> dict:
    return _request("GET", f"/runs/{run_id}", token=token)


def approve_checkpoint(token: str, run_id: str, checkpoint: str) -> dict:
    return _request(
        "POST",
        f"/runs/{run_id}/approve",
        token=token,
        body={"checkpoint": checkpoint, "actor": "desktop-app"},
    )


def get_citation(token: str, run_id: str) -> dict:
    return _request("GET", f"/runs/{run_id}/citation", token=token)


def get_share_view(token: str, run_id: str) -> dict:
    """Read-only evidence view: task graph, artifacts, evidence panels."""
    return _request("POST", f"/runs/{run_id}/share", token=token, body={})


def create_share(token: str, run_id: str, expires_in_days: int = 30) -> dict:
    return _request(
        "POST", f"/runs/{run_id}/share", token=token,
        body={"expires_in_days": expires_in_days},
    )


def read_share(share_token: str) -> dict:
    return _request("GET", f"/share/{share_token}")


def run_to_completion(
    token: str,
    run_id: str,
    on_status: Callable[[str, dict], None] | None = None,
    max_seconds: float = 1800.0,
) -> dict:
    """Poll the run; auto-approve human checkpoints (desktop mode is
    unattended: the user already clicked "Run" in the app)."""
    deadline = time.monotonic() + max_seconds
    last = ""
    while time.monotonic() < deadline:
        run = get_run(token, run_id)
        status = run.get("status", "?")
        if status != last:
            last = status
            if on_status:
                on_status(status, run)
        if status == "awaiting_review":
            checkpoint = _awaiting_checkpoint(run)
            approve_checkpoint(token, run_id, checkpoint)
            time.sleep(1.0)
            continue
        if status in ("complete", "failed"):
            return run
        time.sleep(POLL_INTERVAL_S)
    raise ApiError(f"run {run_id} timed out after {max_seconds:.0f}s")


def _awaiting_checkpoint(run: dict) -> str:
    for task in run.get("tasks", []):
        if task.get("status") == "awaiting_approval":
            req = task.get("requires_checkpoint") or ""
            if req:
                return req
    raise ApiError(
        f"run awaiting review but no checkpoint task found: {run.get('awaiting_task_id')}"
    )
