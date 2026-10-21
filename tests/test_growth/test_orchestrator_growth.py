"""Orchestrator growth surfaces: citations, share URLs, onboarding,
cohort analytics, audit export (ADR-0043/44/45/47/48).

All local-plane: deterministic mock executor, temp audit log, no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer mock:alice"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A client whose runtime is rebuilt per test with its own audit log."""
    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    from orchestrator import main as orchestrator_main

    orchestrator_main.get_runtime.cache_clear()
    with TestClient(orchestrator_main.app) as test_client:
        yield test_client
    orchestrator_main.get_runtime.cache_clear()


def _runtime():
    from orchestrator.main import get_runtime

    return get_runtime()


def _signup(user_id: str = "alice"):
    from phyto_shared.analytics import emit_event

    emit_event(_runtime().audit, "user_signup", user_id=user_id)


def _run(client, workflow="compare_extracts"):
    response = client.post(
        "/analysis",
        json={
            "sample_id": "s1",
            "workflow": workflow,
            "parameters": {},
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _completed_run(client):
    """A workflow that reaches ``complete`` without a checkpoint."""
    body = _run(client)
    assert body["status"] == "complete"
    return body["run_id"]


def _share_token(client, run_id):
    response = client.post(f"/runs/{run_id}/share", json={})
    assert response.status_code == 200, response.text
    return response.json()["share_url"].rsplit("/", 1)[1]


# ---------------------------------------------------------------------------
# Citations (ADR-0043)
# ---------------------------------------------------------------------------


class TestCitationEndpoint:
    def test_completed_run_has_a_citation(self, client):
        run_id = _completed_run(client)
        response = client.get(f"/runs/{run_id}/citation")
        assert response.status_code == 200
        body = response.json()
        assert body["canonical_id"] == f"phytospectra.run:{run_id}"
        assert body["provenance_manifest_hash"]
        assert body["audit_chain_head"]
        assert body["citation_text"]
        assert "doi" in body

    def test_citation_is_free_and_not_metered(self, client):
        run_id = _completed_run(client)
        for _ in range(3):
            assert client.get(f"/runs/{run_id}/citation").status_code == 200

    def test_unfinished_run_is_409(self, client):
        run_id = _run(client, workflow="single_compound")["run_id"]
        response = client.get(f"/runs/{run_id}/citation")
        assert response.status_code == 409
        assert "not completed" in response.json()["detail"]

    def test_unknown_run_is_404(self, client):
        assert client.get("/runs/nope/citation").status_code == 404

    def test_citation_records_run_cited_event_once(self, client):
        run_id = _completed_run(client)
        client.get(f"/runs/{run_id}/citation")
        client.get(f"/runs/{run_id}/citation")
        records = client.get("/audit/records").json()
        cited = [
            r
            for r in records
            if r.get("event") == "run_cited" and r.get("run_id") == run_id
        ]
        assert len(cited) == 1

    def test_citation_surfaces_the_active_share_url(self, client):
        run_id = _completed_run(client)
        assert client.get(f"/runs/{run_id}/citation").json()["share_url"] is None
        token = _share_token(client, run_id)
        share_url = client.get(f"/runs/{run_id}/citation").json()["share_url"]
        assert share_url is not None
        assert share_url.endswith(token)


# ---------------------------------------------------------------------------
# Share URLs (ADR-0044)
# ---------------------------------------------------------------------------


class TestShareEndpoints:
    def test_share_creates_a_read_only_link(self, client):
        run_id = _completed_run(client)
        response = client.post(f"/runs/{run_id}/share", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "read_only"
        assert body["share_url"].startswith("https://")
        assert "/share/" in body["share_url"]
        assert body["expires_at"]

    def test_share_expiry_is_validated(self, client):
        run_id = _completed_run(client)
        assert (
            client.post(
                f"/runs/{run_id}/share", json={"expires_in_days": 0}
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/runs/{run_id}/share", json={"expires_in_days": 366}
            ).status_code
            == 422
        )

    def test_shared_view_is_unauthenticated(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        response = client.get(f"/share/{token}")
        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["sample"]["sample_id"] == "s1"
        assert body["status"] == "complete"
        assert body["citation"] is not None
        # The view carries a CTA back into the product.
        assert body["cta"]["url"].endswith("/analysis/new")

    def test_share_view_never_contains_identity_fields(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        body = client.get(f"/share/{token}").json()
        for record in body["audit_tail"]:
            assert "user_id" not in record

    def test_tampered_token_is_404(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        body, _, signature = token.partition(".")
        flipped = ("A" if signature[-2] != "A" else "B") + signature[-1]
        tampered = f"{body}.{signature[:-2]}{flipped}"
        assert client.get(f"/share/{tampered}").status_code == 404

    def test_share_of_unknown_run_is_404(self, client):
        assert client.post("/runs/nope/share", json={}).status_code == 404
        assert client.get("/share/whatever").status_code == 404

    def test_revocation_kills_the_link(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        assert client.get(f"/share/{token}").status_code == 200
        revoked = client.delete(f"/runs/{run_id}/share")
        assert revoked.status_code == 200
        assert revoked.json()["revoked"] == 1
        assert client.get(f"/share/{token}").status_code == 410

    def test_citation_stops_advertising_a_revoked_link(self, client):
        run_id = _completed_run(client)
        _share_token(client, run_id)
        client.delete(f"/runs/{run_id}/share")
        assert client.get(f"/runs/{run_id}/citation").json()["share_url"] is None

    def test_artifact_can_be_read_through_the_share(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        response = client.get(f"/share/{token}/artifacts/anova")
        assert response.status_code == 200
        assert response.json()["artifact"] == "anova"
        assert response.json()["tool"] == "stats.anova"

    def test_unknown_artifact_is_404(self, client):
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        assert client.get(f"/share/{token}/artifacts/nope").status_code == 404

    def test_share_reads_are_rate_limited_per_ip(self, client):
        from orchestrator.rate_limit import SlidingWindowRateLimiter

        _runtime().rate_limiter = SlidingWindowRateLimiter(max_requests=2)
        run_id = _completed_run(client)
        token = _share_token(client, run_id)
        assert client.get(f"/share/{token}").status_code == 200
        assert client.get(f"/share/{token}").status_code == 200
        assert client.get(f"/share/{token}").status_code == 429


# ---------------------------------------------------------------------------
# Onboarding (ADR-0045)
# ---------------------------------------------------------------------------


class TestOnboarding:
    def test_first_story_fits_the_free_allowance(self, client):
        body = client.get("/onboarding/first-story").json()
        assert body["fits_in_free_tier"] is True
        assert body["monthly_allowance"] == 50
        assert body["total_tokens"] <= 50
        assert body["retry_buffer"] >= 0

    def test_step_costs_are_derived_from_the_pricing_table(self, client):
        body = client.get("/onboarding/first-story").json()
        from phyto_shared.token_pricing import cost_for_route

        for step in body["steps"]:
            expected = cost_for_route(step["method"], step["route"]).tokens
            assert step["tokens"] == expected

    def test_evidence_and_citation_reads_are_free(self, client):
        body = client.get("/onboarding/first-story").json()
        by_operation = {step["operation"]: step for step in body["steps"]}
        assert by_operation["view_evidence"]["tokens"] == 0
        assert by_operation["cite_and_share"]["tokens"] == 0

    def test_steps_are_numbered_and_ordered(self, client):
        steps = client.get("/onboarding/first-story").json()["steps"]
        assert [step["step"] for step in steps] == list(range(1, len(steps) + 1))


# ---------------------------------------------------------------------------
# Cohort analytics + audit export (ADR-0047/48)
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    """Auth-enforced app with an admin/department user and a funded ledger."""
    from auth.config import AuthConfig
    from auth.entitlements import MockEntitlementsClient
    from auth.middleware import AuthMiddleware
    from auth.pubsub_publisher import LoggingPublisher
    from auth.token_balance import MockTokenBalanceClient
    from orchestrator import main as orchestrator_main

    monkeypatch.setenv("PHYTO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("PHYTO_DRY_RUN", "1")
    monkeypatch.setenv("PHYTO_ADMIN_UIDS", "alice")

    cfg = AuthConfig(
        project_id="test-project",
        firebase_credentials_path=None,
        dry_run=True,
        default_free_tokens=50,
        enabled=True,
    )
    app = orchestrator_main.create_app(cfg)
    tokens = MockTokenBalanceClient({"alice": 5000})
    entitlements = MockEntitlementsClient(
        initial_plans={"alice": "department"}, ledger=tokens
    )
    for middleware in app.user_middleware:
        if middleware.cls is AuthMiddleware:
            middleware.kwargs["token_client"] = tokens
            middleware.kwargs["entitlements"] = entitlements
            middleware.kwargs["publisher"] = LoggingPublisher()
    orchestrator_main.get_runtime.cache_clear()
    with TestClient(app) as test_client:
        test_client.tokens = tokens
        test_client.entitlements = entitlements
        yield test_client
    orchestrator_main.get_runtime.cache_clear()


class TestCohortAdmin:
    def test_anonymous_admin_call_is_401(self, client):
        assert client.get("/admin/cohorts").status_code == 401

    def test_non_admin_is_403(self, admin_client, monkeypatch):
        monkeypatch.delenv("PHYTO_ADMIN_UIDS", raising=False)
        assert admin_client.get("/admin/cohorts", headers=AUTH).status_code == 403

    def test_recompute_turns_a_signup_and_run_into_cohort_metrics(self, admin_client):
        response = admin_client.post(
            "/analysis",
            headers=AUTH,
            json={
                "sample_id": "s1",
                "workflow": "compare_extracts",
                "parameters": {},
                "dry_run": True,
            },
        )
        assert response.status_code == 200, response.text
        _signup("alice")
        recomputed = admin_client.post("/admin/cohorts/recompute", headers=AUTH)
        assert recomputed.status_code == 200, recomputed.text
        cohorts = recomputed.json()["cohorts"]
        assert len(cohorts) == 1
        cohort = cohorts[0]
        assert cohort["signups"] == 1
        assert cohort["activated"] == 1
        assert cohort["activated_rate"] == 1.0
        assert "infra_cost_usd" in cohort

    def test_cohorts_endpoint_serves_the_stored_table(self, admin_client):
        _signup("alice")
        admin_client.post("/admin/cohorts/recompute", headers=AUTH)
        body = admin_client.get("/admin/cohorts", headers=AUTH).json()
        assert len(body["cohorts"]) == 1
        assert isinstance(body["alerts"], list)

    def test_audit_export_requires_department(self, admin_client):
        # Department user can export.
        response = admin_client.get("/audit/export", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["chain_valid"] is True
        # Downgrade to free: same route is now forbidden.
        admin_client.entitlements.set_plan("alice", "free")
        assert admin_client.get("/audit/export", headers=AUTH).status_code == 403
        # Pro is still below department (rank, not equality).
        admin_client.entitlements.set_plan("alice", "pro")
        assert admin_client.get("/audit/export", headers=AUTH).status_code == 403
        # Promotion restores access.
        admin_client.entitlements.set_plan("alice", "department")
        assert admin_client.get("/audit/export", headers=AUTH).status_code == 200

    def test_audit_chain_stays_valid_across_growth_events(self, admin_client):
        admin_client.post(
            "/analysis",
            headers=AUTH,
            json={
                "sample_id": "s1",
                "workflow": "compare_extracts",
                "parameters": {},
                "dry_run": True,
            },
        )
        _signup("alice")
        response = admin_client.get("/audit/records", headers=AUTH)
        assert response.status_code == 200, response.text
        records = response.json()
        assert records and records[0]["record_hash"]
        verification = admin_client.get("/audit/verify", headers=AUTH)
        assert verification.status_code == 200
        assert verification.json()["chain_valid"] is True
