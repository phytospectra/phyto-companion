"""Literature retrieval: mock client, relevance sentences, API shape."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import CloudServiceError
from pharmacology.literature_mining import (
    MockPubmedClient,
    PubmedClient,
    query_compound_literature,
    relevant_sentences,
    search_pubmed,
)


class TestMockClient:
    def test_returns_fixture_hits(self):
        hits = MockPubmedClient().search("quercetin COX-2", max_results=10)
        assert hits
        assert all(hit.pmid.isdigit() for hit in hits)
        assert all(hit.title for hit in hits)

    def test_term_matching(self):
        hits = MockPubmedClient().search("caffeine kinase")
        assert len(hits) == 1
        assert hits[0].pmid == "20371314"

    def test_max_results_respected(self):
        hits = MockPubmedClient().search("", max_results=1)
        assert len(hits) == 1


class TestRelevance:
    def test_sentences_containing_terms(self):
        abstract = "Quercetin inhibits COX-2. Unrelated filler. Another finding."
        sentences = relevant_sentences(abstract, {"quercetin"})
        assert sentences == ["Quercetin inhibits COX-2."]

    def test_no_terms_gives_nothing(self):
        assert relevant_sentences("One sentence.", set()) == []


class TestFunctions:
    def test_search_pubmed_uses_mock_in_dry_run(self, monkeypatch):
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        hits = search_pubmed("quercetin")
        assert hits

    def test_compound_query(self):
        hits = query_compound_literature("REFJWTPEDVJJIY", context="target")
        assert isinstance(hits, list)

    def test_relevance_populated_on_hits(self):
        hits = MockPubmedClient().search("flavonoids COX-2")
        assert any(hit.relevant_sentences for hit in hits)


class TestRealClient:
    def test_fails_closed_without_network(self):
        import httpx

        def handler(request):
            return httpx.Response(500)

        client = PubmedClient(transport=httpx.MockTransport(handler))
        with pytest.raises(CloudServiceError):
            client.search("anything")
