"""Literature service tests: deduplication, BM25 ranking, clients, endpoints.

No network: live clients are exercised only for their offline parsing and
rate-limiter logic, and the endpoints use the deterministic mock router.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.literature_models import PaperHit

import literature.main as literature_main
from literature.clients import (
    MockLiteratureClient,
    OpenAlexClient,
    PubMedClient,
    RateLimiter,
    SemanticScholarClient,
)
from literature.router import LiteratureRouter, bm25_rank, deduplicate, tokenize


def _hit(source: str, pmid: str | None, doi: str | None, title: str, abstract: str = "") -> PaperHit:
    return PaperHit(
        pmid=pmid,
        doi=doi,
        title=title,
        abstract=abstract,
        year=2020,
        source=source,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Deduplication (ADR-0030)
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_same_doi_across_sources_collapses_to_one(self):
        merged = deduplicate(
            [
                _hit("OpenAlex", None, "10.1000/x", "OpenAlex copy"),
                _hit("PubMed", "11111111", "10.1000/x", "PubMed copy"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].source == "PubMed"
        assert merged[0].also_found_in == ["OpenAlex"]

    def test_pmid_priority_over_semantic_scholar(self):
        merged = deduplicate(
            [
                _hit("Semantic Scholar", "11111111", "10.1000/x", "SS"),
                _hit("PubMed", "11111111", "10.1000/x", "PM"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].source == "PubMed"
        assert merged[0].title == "PM"

    def test_doi_only_matches_pmid_only_via_linking_record(self):
        # OpenAlex record has only a DOI; PubMed record has both.  The PubMed
        # record links the two keys, so all three collapse into one group.
        merged = deduplicate(
            [
                _hit("OpenAlex", None, "10.1000/x", "OpenAlex copy"),
                _hit("PubMed", "11111111", "10.1000/x", "PubMed copy"),
                _hit("Semantic Scholar", "11111111", None, "SS copy"),
            ]
        )
        assert len(merged) == 1
        assert merged[0].source == "PubMed"
        assert set(merged[0].also_found_in) == {"OpenAlex", "Semantic Scholar"}

    def test_missing_abstract_filled_from_duplicate(self):
        merged = deduplicate(
            [
                _hit("OpenAlex", None, "10.1000/x", "OpenAlex copy", abstract=""),
                _hit("PubMed", "11111111", "10.1000/x", "PubMed copy", abstract="Rich abstract."),
            ]
        )
        assert merged[0].abstract == "Rich abstract."

    def test_distinct_papers_are_not_merged(self):
        merged = deduplicate(
            [
                _hit("PubMed", "11111111", "10.1000/a", "Paper A"),
                _hit("PubMed", "22222222", "10.1000/b", "Paper B"),
            ]
        )
        assert len(merged) == 2

    def test_no_identifiers_is_rejected_at_model_level(self):
        with pytest.raises(ValueError, match="pmid or doi"):
            PaperHit(title="orphan", source="PubMed")


# ---------------------------------------------------------------------------
# BM25 ranking
# ---------------------------------------------------------------------------


class TestBM25:
    def test_query_terms_rank_relevant_hit_first(self):
        hits = [
            _hit("PubMed", "1", None, "Unrelated paper about geology"),
            _hit("PubMed", "2", None, "Quercetin inhibits NF-kB signalling", "quercetin nf-kb"),
        ]
        ranked = bm25_rank(hits, "quercetin nf-kb")
        assert ranked[0].pmid == "2"
        assert ranked[0].relevance_score > ranked[1].relevance_score

    def test_empty_query_falls_back_to_title_order(self):
        hits = [
            _hit("PubMed", "1", None, "Zeta"),
            _hit("PubMed", "2", None, "Alpha"),
        ]
        assert [h.title for h in bm25_rank(hits, "")] == ["Alpha", "Zeta"]

    def test_empty_hits(self):
        assert bm25_rank([], "query") == []

    def test_scores_are_non_negative_and_bounded(self):
        hits = [_hit("PubMed", "1", None, "Quercetin study", "quercetin")]
        ranked = bm25_rank(hits, "quercetin")
        assert 0.0 <= ranked[0].relevance_score <= 1.0

    def test_tokenize_drops_stopwords(self):
        assert tokenize("The study of a compound") == ["study", "compound"]


# ---------------------------------------------------------------------------
# Clients: offline logic only
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError):
            RateLimiter(0)

    def test_first_call_does_not_wait(self):
        limiter = RateLimiter(1000.0)
        limiter.wait()
        assert limiter.last_wait == 0.0
        assert limiter.call_count == 1


class TestPubMedParsing:
    XML = """<?xml version="1.0"?>
    <PubmedArticleSet>
      <PubmedArticle>
        <MedlineCitation>
          <PMID>12345678</PMID>
          <Article>
            <ArticleTitle>Quercetin <i>in vitro</i> study</ArticleTitle>
            <Abstract><AbstractText>Quercetin inhibits NF-kB.</AbstractText></Abstract>
            <Journal><Title>J Nat Prod</Title></Journal>
            <AuthorList>
              <Author><LastName>Doe</LastName><Initials>J</Initials></Author>
            </AuthorList>
          </Article>
        </MedlineCitation>
        <PubmedData>
          <ArticleIdList>
            <ArticleId IdType="doi">10.1000/XYZ</ArticleId>
          </ArticleIdList>
        </PubmedData>
      </PubmedArticle>
    </PubmedArticleSet>
    """

    def test_parse_article_extracts_fields_and_normalises_doi(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self.XML)
        client = PubMedClient(api_key="")
        hit = client._parse_article(root.find(".//PubmedArticle"))
        assert hit is not None
        assert hit.pmid == "12345678"
        assert hit.doi == "10.1000/xyz"  # lowercased, prefix stripped
        assert "Quercetin" in hit.title and "in vitro" in hit.title
        assert hit.abstract == "Quercetin inhibits NF-kB."
        assert hit.authors == ["Doe J"]
        assert hit.source == "PubMed"

    def test_article_without_pmid_is_skipped(self):
        import xml.etree.ElementTree as ET

        xml = "<PubmedArticle><MedlineCitation><Article><ArticleTitle>X</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
        client = PubMedClient(api_key="")
        assert client._parse_article(ET.fromstring(xml)) is None


class TestSemanticScholar:
    def test_no_key_returns_empty_without_network(self, monkeypatch):
        monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)
        client = SemanticScholarClient(api_key="")
        assert client.search("anything", 5) == []

    def test_parse_requires_an_identifier(self):
        client = SemanticScholarClient(api_key="k")
        assert client._parse({"title": "No ids here"}) is None
        hit = client._parse(
            {
                "title": "Has DOI",
                "externalIds": {"DOI": "10.1000/ABC"},
                "authors": [{"name": "A. Author"}],
                "year": 2020,
                "venue": "J Test",
            }
        )
        assert hit is not None and hit.doi == "10.1000/abc"


class TestOpenAlex:
    def test_reconstruct_abstract_handles_repeated_words(self):
        inverted = {"the": [0, 4], "quick": [1], "brown": [2], "fox": [3], "jumps": [5]}
        assert OpenAlexClient._reconstruct_abstract(inverted) == "the quick brown fox the jumps"

    def test_reconstruct_abstract_empty(self):
        assert OpenAlexClient._reconstruct_abstract(None) == ""

    def test_parse_extracts_pmid_from_url(self):
        client = OpenAlexClient()
        hit = client._parse(
            {
                "id": "https://openalex.org/W1",
                "title": "A work",
                "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/12345678/"},
                "publication_year": 2021,
            }
        )
        assert hit is not None and hit.pmid == "12345678"


# ---------------------------------------------------------------------------
# Router behaviour
# ---------------------------------------------------------------------------


class _FailingClient:
    name = "Broken"

    def search(self, query, max_results, filters=None):
        raise RuntimeError("index down")


class TestRouter:
    def test_requires_at_least_one_client(self):
        with pytest.raises(ValueError):
            LiteratureRouter([])

    def test_two_mock_sources_deduplicate_to_five(self):
        router = LiteratureRouter(
            [MockLiteratureClient("PubMed"), MockLiteratureClient("OpenAlex")]
        )
        result = router.search("quercetin", max_results=10)
        # Both mocks return the same five papers by DOI.
        assert len(result.papers) == 5
        assert result.source_counts == {"PubMed": 5, "OpenAlex": 5}
        assert all(p.source == "PubMed" for p in result.papers)
        assert all("OpenAlex" in p.also_found_in for p in result.papers)

    def test_failing_index_yields_partial_results_with_gap(self):
        router = LiteratureRouter([MockLiteratureClient("PubMed"), _FailingClient()])
        result = router.search("quercetin", max_results=10)
        assert len(result.papers) == 5
        assert result.source_counts["Broken"] == 0
        assert result.source_counts["Broken_error"] == 1

    def test_respects_max_results(self):
        router = LiteratureRouter([MockLiteratureClient("PubMed")])
        assert len(router.search("quercetin", max_results=2).papers) == 2


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class TestEndpoints:
    def setup_method(self):
        literature_main.get_router.cache_clear()
        self.client = TestClient(literature_main.app)

    def teardown_method(self):
        literature_main.get_router.cache_clear()

    def test_health(self):
        assert self.client.get("/health").json()["service"] == "literature"

    def test_search_returns_ranked_papers_and_manifest(self):
        response = self.client.post(
            "/literature/search", json={"query": "quercetin inflammation", "max_results": 5}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["result"]["papers"]
        assert payload["provenance_manifest_id"]

    def test_query_too_short_is_422(self):
        assert self.client.post("/literature/search", json={"query": "q"}).status_code == 422

    def test_compound_route(self):
        response = self.client.post(
            "/literature/compound", json={"compound_name": "quercetin", "max_results": 3}
        )
        assert response.status_code == 200
        assert response.json()["result"]["papers"]

    def test_target_route(self):
        response = self.client.post(
            "/literature/target",
            json={"target_name": "NF-kB", "context": "inflammation", "max_results": 3},
        )
        assert response.status_code == 200

    def test_paper_lookup_rejects_non_numeric_pmid(self):
        assert self.client.get("/literature/paper/abc").status_code == 422

    def test_paper_lookup_returns_known_mock(self):
        response = self.client.get("/literature/paper/10000001")
        assert response.status_code == 200
        assert response.json()["pmid"] == "10000001"
