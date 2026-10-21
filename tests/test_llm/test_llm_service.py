"""LLM service tests: grounding, provider routing, OpenRouter client.

No network: the OpenRouter client is driven through ``httpx.MockTransport``,
and the FastAPI endpoints use the offline mock provider.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import PaperHit

import llm.main as llm_main
from llm.clients import MockLLMClient, VertexAILLMClient
from llm.grounding import compute_grounding, extract_citations
from llm.openrouter_client import DEFAULT_OPENROUTER_MODEL, OpenRouterLLMClient


def _paper(pmid: str = "12345678", doi: str | None = None, title: str = "A study") -> PaperHit:
    return PaperHit(
        pmid=pmid,
        doi=doi,
        title=title,
        abstract="An abstract about the compound and its activity.",
        year=2021,
        source="PubMed",
    )


# ---------------------------------------------------------------------------
# Grounding (shared logic, exercised through the service re-export)
# ---------------------------------------------------------------------------


class TestGrounding:
    def test_cited_claim_is_grounded(self):
        papers = [_paper("12345678")]
        report = compute_grounding("Quercetin inhibits NF-kB. PMID: 12345678.", papers)
        assert report.claims_total == 1
        assert report.claims_uncited == 0
        assert report.score == 1.0

    def test_uncited_claim_is_flagged(self):
        report = compute_grounding("Quercetin inhibits NF-kB strongly.", [_paper()])
        assert report.claims_uncited == 1
        assert report.score == 0.0

    def test_fabricated_citation_is_invalid(self):
        report = compute_grounding(
            "Quercetin inhibits NF-kB in macrophages. PMID: 99999999.", [_paper()]
        )
        assert "PMID:99999999" in report.invalid_citations
        assert report.claims_uncited == 1

    def test_partial_grounding_scores_fraction(self):
        papers = [_paper("12345678")]
        text = (
            "Quercetin inhibits NF-kB in macrophages. PMID: 12345678. "
            "It also reduces cytokine release in vivo."
        )
        report = compute_grounding(text, papers)
        assert report.claims_total == 2
        assert report.claims_uncited == 1
        assert report.score == 0.5

    def test_mock_output_passes_grounding(self):
        papers = [_paper("12345678", "10.1000/x"), _paper("87654321", "10.1000/y")]
        completion = MockLLMClient().complete(
            "hypothesize", papers, "compound: quercetin\n"
        )
        report = compute_grounding(completion.text, papers)
        assert report.score >= 0.7
        assert report.claims_uncited == 0
        assert report.invalid_citations == []

    def test_doi_citation_is_recognised(self):
        papers = [_paper("12345678", "10.1000/x")]
        text = "The compound inhibits the pathway in macrophages. doi: 10.1000/x."
        citations = extract_citations(text)
        assert "DOI:10.1000/x" in citations
        report = compute_grounding(text, papers)
        assert report.score == 1.0

    def test_bracketed_reference_out_of_range_is_invalid(self):
        report = compute_grounding("This is a claim about the compound [7].", [_paper()])
        assert "REF:7" in report.invalid_citations


# ---------------------------------------------------------------------------
# OpenRouter client
# ---------------------------------------------------------------------------


class TestOpenRouterClient:
    def test_missing_key_fails_closed(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        client = OpenRouterLLMClient(api_key="")
        with pytest.raises(CloudServiceError, match="OPENROUTER_API_KEY"):
            client.complete("explain", [_paper()], "evidence")

    def test_default_model_is_free_qwen(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
        assert OpenRouterLLMClient(api_key="k").model_id == DEFAULT_OPENROUTER_MODEL
        assert DEFAULT_OPENROUTER_MODEL == "qwen/qwen3-4b:free"

    def test_model_is_configurable_by_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct")
        assert OpenRouterLLMClient(api_key="k").model_id == "meta-llama/llama-3-8b-instruct"

    def _client(self, handler) -> OpenRouterLLMClient:
        return OpenRouterLLMClient(
            api_key="test-key",
            transport=httpx.MockTransport(handler),
        )

    def test_successful_completion_parses_usage_and_model(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/chat/completions")
            assert request.headers["Authorization"] == "Bearer test-key"
            payload = __import__("json").loads(request.content)
            assert payload["messages"][0]["role"] == "system"
            assert "PMID" in payload["messages"][0]["content"]
            return httpx.Response(
                200,
                json={
                    "model": "qwen/qwen3-4b:free",
                    "choices": [
                        {"message": {"role": "assistant", "content": "Grounded answer. PMID: 12345678."}}
                    ],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 7},
                },
            )

        completion = self._client(handler).complete("explain", [_paper()], "evidence")
        assert completion.text == "Grounded answer. PMID: 12345678."
        assert completion.tokens_in == 42
        assert completion.tokens_out == 7
        assert completion.model_id == "qwen/qwen3-4b:free"

    def test_http_error_raises_cloud_service_error(self):
        client = self._client(lambda request: httpx.Response(429, text="rate limited"))
        with pytest.raises(CloudServiceError, match="OpenRouter returned an error"):
            client.complete("explain", [_paper()], "evidence")

    def test_malformed_response_raises_cloud_service_error(self):
        client = self._client(lambda request: httpx.Response(200, json={"unexpected": True}))
        with pytest.raises(CloudServiceError, match="unexpected response shape"):
            client.complete("explain", [_paper()], "evidence")

    def test_empty_completion_raises(self):
        client = self._client(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "   "}}]}
            )
        )
        with pytest.raises(CloudServiceError, match="empty completion"):
            client.complete("explain", [_paper()], "evidence")

    def test_transport_error_raises_cloud_service_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with pytest.raises(CloudServiceError, match="request failed"):
            self._client(handler).complete("explain", [_paper()], "evidence")

    def test_no_key_in_returned_metadata(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok. PMID: 12345678."}}]},
            )

        completion = self._client(handler).complete("explain", [_paper()], "evidence")
        assert "test-key" not in repr(completion)


# ---------------------------------------------------------------------------
# Provider factory and endpoints
# ---------------------------------------------------------------------------


class TestProviderFactory:
    def test_vertex_provider_returns_vertex_client(self):
        assert isinstance(llm_main.get_llm_client("vertex"), VertexAILLMClient)

    def test_openrouter_provider_returns_openrouter_client(self):
        assert isinstance(llm_main.get_llm_client("openrouter"), OpenRouterLLMClient)

    def test_local_provider_returns_mock(self):
        assert isinstance(llm_main.get_llm_client("local"), MockLLMClient)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown LLM provider"):
            llm_main.get_llm_client("anthropic")

    def test_default_is_vertex_in_cloud_plane(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "cloud")
        monkeypatch.delenv("PHYTO_LLM_PROVIDER", raising=False)
        assert isinstance(llm_main.get_llm_client(), VertexAILLMClient)

    def test_default_is_local_outside_cloud(self, monkeypatch):
        monkeypatch.setenv("PHYTO_ENV", "local")
        monkeypatch.delenv("PHYTO_LLM_PROVIDER", raising=False)
        assert isinstance(llm_main.get_llm_client(), MockLLMClient)

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("PHYTO_LLM_PROVIDER", "openrouter")
        assert isinstance(llm_main.get_llm_client(), OpenRouterLLMClient)


class TestEndpoints:
    def setup_method(self):
        self.client = TestClient(llm_main.app)

    def _body(self, **overrides):
        body = {
            "operation": "explain",
            "provider": "local",
            "context_papers": [_paper().model_dump(mode="json")],
        }
        body.update(overrides)
        return body

    def test_empty_context_is_400_not_422(self):
        response = self.client.post(
            "/llm/explain", json=self._body(context_papers=[])
        )
        assert response.status_code == 400
        assert "context_papers" in response.json()["detail"]

    def test_local_provider_returns_grounded_response(self):
        response = self.client.post("/llm/explain", json=self._body())
        assert response.status_code == 200
        payload = response.json()
        assert payload["response"]["grounding_score"] >= 0.7
        assert payload["response"]["claims_uncited"] == 0
        assert payload["provenance_manifest_id"]

    def test_openrouter_without_key_is_503(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        response = self.client.post(
            "/llm/explain", json=self._body(provider="openrouter")
        )
        assert response.status_code == 503
        assert "OPENROUTER_API_KEY" in response.json()["detail"]

    def test_unknown_provider_is_rejected_by_schema(self):
        response = self.client.post(
            "/llm/explain", json=self._body(provider="anthropic")
        )
        assert response.status_code == 422

    @pytest.mark.parametrize(
        "path",
        [
            "/llm/explain",
            "/llm/hypothesize",
            "/llm/recommend-experiments",
            "/llm/summarize-literature",
        ],
    )
    def test_every_endpoint_requires_context(self, path):
        response = self.client.post(path, json=self._body(context_papers=[]))
        assert response.status_code == 400

    def test_provider_is_recorded_in_manifest(self, monkeypatch):
        # Stub the factory so both requests succeed offline; the point is that
        # the RESOLVED PROVIDER feeds the provenance hash, not the client class.
        monkeypatch.setattr(llm_main, "get_llm_client", lambda provider=None: MockLLMClient())
        vertex = self.client.post("/llm/explain", json=self._body(provider="vertex"))
        openrouter = self.client.post("/llm/explain", json=self._body(provider="openrouter"))
        assert vertex.status_code == 200
        assert openrouter.status_code == 200
        assert (
            vertex.json()["provenance_manifest_id"]
            != openrouter.json()["provenance_manifest_id"]
        )
