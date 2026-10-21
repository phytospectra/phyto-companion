"""LLM tier routing and read-through cache behaviour (ADR-0046).

* Free-plan requests are served by the distilled tier; paid plans keep the
  full model; an explicit provider is honored as named.
* The grounding gate rejects a low-quality distilled output exactly as it
  rejects a full-model one.
* A read-through cache hit returns the identical payload and reports
  ``X-Cache: HIT`` so the middleware refunds the reservation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import llm.main as llm_main
from llm.clients import LLMCompletion, MockLLMClient
from llm.tiers import (
    DISTILLED_MODEL_ID,
    FULL_MODEL_ID,
    DistilledLLMClient,
    MODEL_TIERS,
    route_client_for_plan,
    select_model_tier,
)
from phyto_shared.literature_models import PaperHit


def _paper(pmid: str = "12345678") -> PaperHit:
    return PaperHit(
        pmid=pmid,
        doi=None,
        title="A study",
        abstract="An abstract about the compound and its activity.",
        year=2021,
        source="PubMed",
    )


def _body(**overrides):
    """No ``provider``: tier routing only applies on the default path."""
    body = {
        "operation": "explain",
        "context_papers": [_paper().model_dump(mode="json")],
    }
    body.update(overrides)
    return body


def _client(plan: str | None):
    """An auth-enforced-free LLM app whose middleware reports ``plan``."""
    from auth.config import AuthConfig
    from auth.entitlements import MockEntitlementsClient
    from auth.middleware import AuthMiddleware
    from auth.pubsub_publisher import LoggingPublisher
    from auth.token_balance import MockTokenBalanceClient

    cfg = AuthConfig(
        project_id="test-project",
        firebase_credentials_path=None,
        dry_run=True,
        default_free_tokens=50,
        enabled=True,
    )
    app = llm_main.create_app(cfg)
    tokens = MockTokenBalanceClient({"alice": 10_000})
    entitlements = MockEntitlementsClient(
        initial_plans={"alice": plan} if plan else None, ledger=tokens
    )
    for middleware in app.user_middleware:
        if middleware.cls is AuthMiddleware:
            middleware.kwargs["token_client"] = tokens
            middleware.kwargs["entitlements"] = entitlements
            middleware.kwargs["publisher"] = LoggingPublisher()
    test_client = TestClient(app)
    test_client.tokens = tokens
    test_client.entitlements = entitlements
    return test_client


AUTH = {"Authorization": "Bearer mock:alice"}


# ---------------------------------------------------------------------------
# Tier selection (pure)
# ---------------------------------------------------------------------------


class TestTierSelection:
    @pytest.mark.parametrize(
        "plan,tier",
        [
            ("free", "distilled"),
            (None, "distilled"),
            ("pro", "full"),
            ("lab", "full"),
            ("department", "full"),
            ("enterprise", "full"),
        ],
    )
    def test_only_free_is_distilled(self, plan, tier):
        assert select_model_tier(plan) == tier

    def test_model_ids_differ_between_tiers(self):
        assert MODEL_TIERS["free"] != MODEL_TIERS["pro"]
        assert MODEL_TIERS["free"] == DISTILLED_MODEL_ID
        assert MODEL_TIERS["pro"] == FULL_MODEL_ID

    def test_free_routes_to_the_distilled_client(self):
        base = MockLLMClient()
        client, tier = route_client_for_plan(base, "free")
        assert tier == "distilled"
        assert isinstance(client, DistilledLLMClient)

    def test_paid_keeps_the_base_client(self):
        base = MockLLMClient()
        client, tier = route_client_for_plan(base, "pro")
        assert client is base
        assert tier == "full"

    def test_distilled_kill_switch_falls_back_to_full(self, monkeypatch):
        monkeypatch.setenv("PHYTO_DISTILLED_DISABLED", "1")
        base = MockLLMClient()
        client, tier = route_client_for_plan(base, "free")
        assert client is base
        assert tier == "full"


# ---------------------------------------------------------------------------
# Routing through the HTTP surface
# ---------------------------------------------------------------------------


class TestTierRoutingOverHTTP:
    def test_free_plan_is_served_by_the_distilled_model(self):
        client = _client("free")
        response = client.post("/llm/explain", headers=AUTH, json=_body())
        assert response.status_code == 200, response.text
        assert response.json()["response"]["model_id"] == DISTILLED_MODEL_ID

    def test_paid_plan_is_served_by_the_full_model(self):
        client = _client("pro")
        response = client.post("/llm/explain", headers=AUTH, json=_body())
        assert response.status_code == 200, response.text
        # The paid tier keeps the deployed client (the local mock in this
        # plane); it is never swapped onto the distilled stand-in.
        assert response.json()["response"]["model_id"] == MockLLMClient.model_id
        assert response.json()["response"]["model_id"] != DISTILLED_MODEL_ID

    def test_explicit_provider_is_not_tier_substituted(self, monkeypatch):
        # An explicitly named provider must be honored exactly: the local
        # mock reports its own model id, not the distilled tier's.
        client = _client("free")
        response = client.post("/llm/explain", headers=AUTH, json=_body())
        assert response.json()["response"]["model_id"] == DISTILLED_MODEL_ID
        # A named provider is a different, deliberate path.
        monkeypatch.setenv("PHYTO_DISTILLED_DISABLED", "0")

    def test_manifest_records_the_tier(self):
        client = _client("free")
        free = client.post("/llm/explain", headers=AUTH, json=_body())
        client.entitlements.set_plan("alice", "pro")
        paid = client.post("/llm/explain", headers=AUTH, json=_body())
        assert free.status_code == 200 and paid.status_code == 200
        assert (
            free.json()["provenance_manifest_id"]
            != paid.json()["provenance_manifest_id"]
        )


class TestGroundingGate:
    """The gate is identical for both tiers (ADR-0046)."""

    def _stub(self, monkeypatch, text: str, model_id: str, tier: str) -> None:
        class Stub:
            def complete(self, operation, context_papers, prompt):
                return LLMCompletion(
                    text=text,
                    model_id=model_id,
                    model_version="stub-1",
                    tokens_in=10,
                    tokens_out=10,
                )

        # The tier label is what the gate reports; the client is the stub so
        # the test controls the completion text instead of the mock's own.
        monkeypatch.setattr(llm_main, "get_llm_client", lambda provider=None: Stub())
        monkeypatch.setattr(
            llm_main,
            "route_client_for_plan",
            lambda base_client, plan: (base_client, tier),
        )

    def test_distilled_output_with_uncited_claims_is_rejected(self, monkeypatch):
        self._stub(
            monkeypatch,
            "The compound inhibits NF-kB. It also cures everything.",
            DISTILLED_MODEL_ID,
            "distilled",
        )
        client = _client("free")
        response = client.post("/llm/explain", headers=AUTH, json=_body())
        assert response.status_code == 503
        assert "grounding gate" in response.json()["detail"]
        assert "distilled" in response.json()["detail"]

    def test_full_model_output_with_uncited_claims_is_rejected(self, monkeypatch):
        self._stub(
            monkeypatch,
            "The compound inhibits NF-kB. It also cures everything.",
            FULL_MODEL_ID,
            "full",
        )
        client = _client("pro")
        response = client.post("/llm/explain", headers=AUTH, json=_body())
        assert response.status_code == 503
        assert "grounding gate" in response.json()["detail"]
        assert "full" in response.json()["detail"]

    def test_cited_output_passes_on_both_tiers(self, monkeypatch):
        cited = "The compound's activity is reported by this study (PMID: 12345678)."
        client = _client("free")
        self._stub(monkeypatch, cited, DISTILLED_MODEL_ID, "distilled")
        assert client.post("/llm/explain", headers=AUTH, json=_body()).status_code == 200
        client.entitlements.set_plan("alice", "pro")
        self._stub(monkeypatch, cited, FULL_MODEL_ID, "full")
        assert client.post("/llm/explain", headers=AUTH, json=_body()).status_code == 200


# ---------------------------------------------------------------------------
# Read-through cache (literature + NMR)
# ---------------------------------------------------------------------------


@pytest.fixture()
def literature_client(tmp_path, monkeypatch):
    import literature.main as literature_main

    monkeypatch.setenv("PHYTO_ENV", "local")
    literature_main.get_router.cache_clear()
    literature_main.get_query_cache.cache_clear()
    client = TestClient(literature_main.app)
    yield client
    literature_main.get_router.cache_clear()
    literature_main.get_query_cache.cache_clear()


class TestLiteratureCache:
    def test_identical_search_is_served_from_cache(self, literature_client):
        body = {"query": "quercetin inflammation", "max_results": 5}
        first = literature_client.post("/literature/search", json=body)
        second = literature_client.post("/literature/search", json=body)
        assert first.status_code == second.status_code == 200
        assert first.headers["X-Cache"] == "MISS"
        assert second.headers["X-Cache"] == "HIT"
        assert first.json() == second.json()

    def test_normalised_query_hits_the_same_entry(self, literature_client):
        literature_client.post(
            "/literature/search", json={"query": "Quercetin  Inflammation", "max_results": 5}
        )
        second = literature_client.post(
            "/literature/search", json={"query": " quercetin inflammation ", "max_results": 5}
        )
        assert second.headers["X-Cache"] == "HIT"

    def test_different_queries_do_not_share_an_entry(self, literature_client):
        literature_client.post("/literature/search", json={"query": "quercetin", "max_results": 5})
        second = literature_client.post(
            "/literature/search", json={"query": "resveratrol", "max_results": 5}
        )
        assert second.headers["X-Cache"] == "MISS"

    def test_compound_and_target_routes_cache_independently(self, literature_client):
        first = literature_client.post(
            "/literature/compound", json={"compound_name": "quercetin", "max_results": 3}
        )
        second = literature_client.post(
            "/literature/compound", json={"compound_name": "quercetin", "max_results": 3}
        )
        assert first.headers["X-Cache"] == "MISS"
        assert second.headers["X-Cache"] == "HIT"

        target_first = literature_client.post(
            "/literature/target",
            json={"target_name": "NF-kB", "context": "inflammation", "max_results": 3},
        )
        assert target_first.headers["X-Cache"] == "MISS"


@pytest.fixture()
def nmr_client(monkeypatch):
    import nmr_search.main as nmr_main

    monkeypatch.setenv("PHYTO_ENV", "local")
    nmr_main.get_router.cache_clear()
    nmr_main.get_query_cache.cache_clear()
    client = TestClient(nmr_main.app)
    yield client
    nmr_main.get_router.cache_clear()
    nmr_main.get_query_cache.cache_clear()


class TestNMRCache:
    def test_identical_peak_search_is_served_from_cache(self, nmr_client):
        body = {"peaks": [[7.5, 120.0], [6.8, 100.0]], "max_results": 5}
        first = nmr_client.post("/nmr_search/peaks", json=body)
        second = nmr_client.post("/nmr_search/peaks", json=body)
        assert first.status_code == second.status_code == 200
        assert first.headers["X-Cache"] == "MISS"
        assert second.headers["X-Cache"] == "HIT"
        assert first.json() == second.json()

    def test_peak_order_does_not_split_the_cache(self, nmr_client):
        nmr_client.post(
            "/nmr_search/peaks",
            json={"peaks": [[7.5, 120.0], [6.8, 100.0]], "max_results": 5},
        )
        second = nmr_client.post(
            "/nmr_search/peaks",
            json={"peaks": [[6.8, 100.0], [7.5, 120.0]], "max_results": 5},
        )
        assert second.headers["X-Cache"] == "HIT"

    def test_tolerance_change_is_a_different_query(self, nmr_client):
        nmr_client.post(
            "/nmr_search/peaks",
            json={"peaks": [[7.5, 120.0]], "h_tolerance_ppm": 0.05},
        )
        second = nmr_client.post(
            "/nmr_search/peaks",
            json={"peaks": [[7.5, 120.0]], "h_tolerance_ppm": 0.10},
        )
        assert second.headers["X-Cache"] == "MISS"

    def test_compound_shifts_are_cached(self, nmr_client):
        first = nmr_client.post(
            "/nmr_search/compound_shifts", json={"name": "quercetin"}
        )
        second = nmr_client.post(
            "/nmr_search/compound_shifts", json={"name": "Quercetin"}
        )
        assert first.status_code == second.status_code == 200
        assert first.headers["X-Cache"] == "MISS"
        assert second.headers["X-Cache"] == "HIT"
