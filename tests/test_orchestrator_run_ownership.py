"""Offline HTTP ownership regression: principals never read or mutate another run."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PHYTO_ENV", "local")
    monkeypatch.setenv("PHYTO_AUTH_ENABLED", "0")
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_SCIENTIFIC_EXECUTION", "mock")
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    from orchestrator.main import create_app, get_runtime
    get_runtime.cache_clear()
    app = create_app()

    @app.middleware("http")
    async def test_identity(request, call_next):
        # Test-only principal injection, never installed on the exported app.
        uid = request.headers.get("X-Test-UID")
        if uid:
            request.state.user = SimpleNamespace(uid=uid)
        return await call_next(request)

    with TestClient(app) as test_client:
        yield test_client
    get_runtime.cache_clear()


def start(client, uid=None):
    headers = {"X-Test-UID": uid} if uid else {}
    response = client.post("/analysis", headers=headers, json={
        "sample_id": "offline-test", "workflow": "single_compound", "dry_run": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def test_other_user_cannot_read_approve_cite_or_share(client):
    from orchestrator.main import get_runtime
    run_id = start(client, "alice")
    entry = get_runtime().runs[run_id]
    assert entry["owner_uid"] == "alice"
    before = entry.copy()
    for uid in ("bob", None):
        headers = {"X-Test-UID": uid} if uid else {}
        for method, suffix, body in [
            ("GET", "", None), ("GET", "/citation", None),
            ("POST", "/approve", {"checkpoint": "before_confirmed_claim", "actor": "alice"}),
            ("POST", "/share", {}),
        ]:
            path = f"/runs/{run_id}{suffix}"
            response = client.request(method, path, headers=headers, **({"json": body} if body is not None else {}))
            missing = client.request(method, f"/runs/missing{suffix}", headers=headers, **({"json": body} if body is not None else {}))
            assert response.status_code == missing.status_code == 404
            assert response.json()["detail"].startswith("unknown run")
    assert get_runtime().runs[run_id] == before
    assert client.get(f"/runs/{run_id}", headers={"X-Test-UID": "alice"}).status_code == 200


def test_anonymous_local_runs_are_not_claimed_by_authenticated_user(client):
    run_id = start(client)
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert client.get(f"/runs/{run_id}", headers={"X-Test-UID": "alice"}).status_code == 404


def test_missing_owner_metadata_denied(client):
    from orchestrator.main import get_runtime
    run_id = start(client, "alice")
    del get_runtime().runs[run_id]["owner_uid"]
    assert client.get(f"/runs/{run_id}", headers={"X-Test-UID": "alice"}).status_code == 404


def test_anonymous_cloud_access_denied_even_if_middleware_disabled(client, monkeypatch):
    run_id = start(client)
    monkeypatch.setenv("PHYTO_ENV", "cloud")
    assert client.get(f"/runs/{run_id}").status_code == 401
    assert client.post("/analysis", json={"sample_id": "x", "workflow": "single_compound"}).status_code == 401
