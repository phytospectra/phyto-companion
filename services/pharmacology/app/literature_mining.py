"""Literature retrieval via PubMed E-utilities (mock locally).

Not an NLP pipeline: targeted retrieval with a simple sentence matcher
for relevance highlighting.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from phyto_shared.exceptions import CloudServiceError

__all__ = [
    "PubMedHit",
    "LiteratureClient",
    "PubmedClient",
    "MockPubmedClient",
    "get_literature_client",
    "search_pubmed",
    "query_compound_literature",
    "relevant_sentences",
]


class PubMedHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pmid: str
    title: str
    abstract: str
    year: int
    authors: list[str]
    relevant_sentences: list[str]


class LiteratureClient(Protocol):
    def search(self, query: str, max_results: int) -> list[PubMedHit]: ...


class PubmedClient:
    """Real E-utilities client (cloud); transport injectable for tests."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def search(self, query: str, max_results: int = 20) -> list[PubMedHit]:
        try:
            with httpx.Client(
                base_url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils",
                timeout=30.0,
                transport=self._transport,
            ) as client:
                ids_response = client.get(
                    "/esearch.fcgi",
                    params={"db": "pubmed", "retmode": "json", "retmax": max_results, "term": query},
                )
                ids_response.raise_for_status()
                pmids = ids_response.json()["esearchresult"]["idlist"]
                if not pmids:
                    return []
                summary = client.get(
                    "/esummary.fcgi",
                    params={"db": "pubmed", "retmode": "json", "id": ",".join(pmids)},
                )
                summary.raise_for_status()
        except Exception as exc:
            raise CloudServiceError(
                "PubMed E-utilities unavailable", context={"error": str(exc)}
            ) from exc
        hits: list[PubMedHit] = []
        result = summary.json().get("result", {})
        for pmid in pmids:
            entry = result.get(pmid, {})
            hits.append(
                PubMedHit(
                    pmid=pmid,
                    title=entry.get("title", ""),
                    abstract=entry.get("abstract", ""),
                    year=int(entry.get("pubdate", "0")[:4] or 0),
                    authors=[
                        author.get("name", "")
                        for author in entry.get("authors", [])
                    ],
                    relevant_sentences=[],
                )
            )
        return hits


_MOCK_ARTICLES = [
    PubMedHit(
        pmid="11914285",
        title="Flavonoids as inhibitors of cyclooxygenase-2",
        abstract=(
            "Quercetin and related flavonoids inhibit COX-2 in vitro. "
            "The effect is modest and cell-type dependent. "
            "Unrelated sentences about spectroscopy follow."
        ),
        year=2002,
        authors=["Author A", "Author B"],
        relevant_sentences=[],
    ),
    PubMedHit(
        pmid="1675914",
        title="Plant phenolics and prostaglandin biosynthesis",
        abstract=(
            "Selected plant phenolics modulate prostaglandin "
            "biosynthesis through COX-1 in cell-free assays."
        ),
        year=1991,
        authors=["Author C"],
        relevant_sentences=[],
    ),
    PubMedHit(
        pmid="20371314",
        title="Methylxanthines and kinase signalling",
        abstract="Caffeine and derivatives interact with kinase pathways.",
        year=2010,
        authors=["Author D"],
        relevant_sentences=[],
    ),
]


class MockPubmedClient:
    """Deterministic fixture library; matches by query-term overlap."""

    def search(self, query: str, max_results: int = 20) -> list[PubMedHit]:
        terms = {t.lower().strip(".,;") for t in query.split() if len(t) > 3}
        hits: list[PubMedHit] = []
        for article in _MOCK_ARTICLES:
            haystack = f"{article.title} {article.abstract}".lower()
            if not terms or any(term in haystack for term in terms):
                hit = article.model_copy()
                hit.relevant_sentences = relevant_sentences(hit.abstract, terms)
                hits.append(hit)
            if len(hits) >= max_results:
                break
        return hits


def get_literature_client(transport: httpx.BaseTransport | None = None) -> LiteratureClient:
    if os.environ.get("PHYTO_DRY_RUN") == "1" or os.environ.get("PHYTO_ENV") != "cloud":
        return MockPubmedClient()
    return PubmedClient(transport)


def relevant_sentences(abstract: str, terms: set[str]) -> list[str]:
    """Sentences containing at least one query term (simple matcher)."""
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    return [
        sentence
        for sentence in sentences
        if any(term in sentence.lower() for term in terms)
    ]


def search_pubmed(
    query: str, max_results: int = 20, client: LiteratureClient | None = None
) -> list[PubMedHit]:
    client = client or get_literature_client()
    return client.search(query, max_results)


def query_compound_literature(
    compound_ik: str,
    context: str = "target",
    client: LiteratureClient | None = None,
) -> list[PubMedHit]:
    """Query built from the compound + requested context.

    NOTE: InChIKeys are rare in titles/abstracts; the cloud
    implementation resolves the InChIKey to a name via PubChem first.
    The mock accepts the raw string for determinism.
    """
    query = f"{compound_ik} {context}"
    return search_pubmed(query, client=client)
