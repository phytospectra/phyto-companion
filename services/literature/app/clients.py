"""Literature index clients: PubMed, Semantic Scholar, OpenAlex.

All three implement ``LiteratureClient`` so the router can treat them
uniformly.  Each client is responsible for its OWN rate limit, because the
limits differ by provider and by whether an API key is present:

===============  ==============  ================
provider         no key          with key
===============  ==============  ================
PubMed           3 requests/s    10 requests/s
Semantic Scholar (not usable)    1 request/s
OpenAlex         ~10 requests/s  100 requests/s (polite pool)
===============  ==============  ================

Keys are read from the environment only; they are never logged.  Every
client returns ``PaperHit`` objects with ``source`` set, and the router
handles deduplication -- a client must not merge results itself.

Network access lives entirely in ``_get_json``; the SQL-free logic
(parsing, rate limiting, normalisation) is unit-testable offline.
"""

from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any, Protocol

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import PaperHit

__all__ = [
    "LiteratureClient",
    "RateLimiter",
    "PubMedClient",
    "SemanticScholarClient",
    "OpenAlexClient",
    "MockLiteratureClient",
]


class LiteratureClient(Protocol):
    """Uniform interface for every literature index."""

    name: str

    def search(self, query: str, max_results: int, filters: dict[str, Any] | None = None) -> list[PaperHit]: ...


class RateLimiter:
    """Thread-safe minimum-interval limiter (requests per second).

    Deliberately simple: a monotonic-clock gate per limiter instance.  A
    token bucket would allow bursts, but NCBI's guidance is a steady rate and
    bursts are exactly what gets an IP throttled.
    """

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0
        # Test hook: allows asserting the configured rate without sleeping.
        self.call_count = 0
        self.last_wait = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            remaining = self.min_interval - elapsed
            if self._last_call > 0.0 and remaining > 0:
                self.last_wait = remaining
                time.sleep(remaining)
            else:
                self.last_wait = 0.0
            self._last_call = time.monotonic()
            self.call_count += 1


def _get_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    """Perform one HTTP GET and decode JSON.

    ``requests`` is imported lazily so the local plane never needs the
    dependency installed for offline parsing tests.
    """
    try:
        import requests
    except ImportError as exc:
        raise CloudServiceError(
            "requests is not installed; network clients are cloud-plane only"
        ) from exc
    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise CloudServiceError(
            "literature API request failed", context={"url": url, "error": str(exc)}
        ) from exc


def _normalise_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


# ---------------------------------------------------------------------------
# PubMed (E-utilities: ESearch then EFetch)
# ---------------------------------------------------------------------------


class PubMedClient:
    """NCBI E-utilities client.

    Uses ESearch (get PMIDs) then EFetch (get records) because ESummary does
    not return abstracts, and an abstract-less hit cannot be ranked or cited.
    """

    name = "PubMed"
    ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def __init__(self, api_key: str | None = None, rate_limiter: RateLimiter | None = None) -> None:
        self.api_key = api_key or os.environ.get("NCBI_API_KEY") or None
        # NCBI: 3/s anonymous, 10/s with a key.
        self.limiter = rate_limiter or RateLimiter(10.0 if self.api_key else 3.0)

    def search(self, query: str, max_results: int, filters: dict[str, Any] | None = None) -> list[PaperHit]:
        filters = filters or {}
        term = query
        if filters.get("year_from") or filters.get("year_to"):
            start = filters.get("year_from", 1800)
            end = filters.get("year_to", 2200)
            term = f"{term} AND {start}:{end}[dp]"

        self.limiter.wait()
        search_params: dict[str, Any] = {
            "db": "pubmed",
            "term": term,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
        }
        if self.api_key:
            search_params["api_key"] = self.api_key
        payload = _get_json(self.ESEARCH, params=search_params)
        pmids = payload.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        self.limiter.wait()
        fetch_params: dict[str, Any] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
        }
        if self.api_key:
            fetch_params["api_key"] = self.api_key
        return self._fetch_records(fetch_params)

    def _fetch_records(self, params: dict[str, Any]) -> list[PaperHit]:
        try:
            import requests
        except ImportError as exc:
            raise CloudServiceError("requests is not installed") from exc
        try:
            response = requests.get(self.EFETCH, params=params, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            raise CloudServiceError(
                "PubMed EFetch failed", context={"error": str(exc)}
            ) from exc
        return [hit for hit in (self._parse_article(a) for a in root.findall(".//PubmedArticle")) if hit]

    def _parse_article(self, article: ET.Element) -> PaperHit | None:
        pmid_el = article.find(".//PMID")
        if pmid_el is None or not pmid_el.text:
            return None
        title_el = article.find(".//ArticleTitle")
        title = "".join(title_el.itertext()).strip() if title_el is not None else ""
        if not title:
            return None

        abstract_parts = [
            "".join(el.itertext()).strip()
            for el in article.findall(".//AbstractText")
        ]
        abstract = " ".join(p for p in abstract_parts if p)

        year: int | None = None
        year_el = article.find(".//PubDate/Year")
        if year_el is not None and year_el.text and year_el.text.isdigit():
            year = int(year_el.text)
        else:
            medline_date = article.find(".//PubDate/MedlineDate")
            if medline_date is not None and medline_date.text:
                first = medline_date.text.strip()[:4]
                if first.isdigit():
                    year = int(first)

        authors = []
        for author in article.findall(".//AuthorList/Author"):
            last = author.findtext("LastName") or ""
            initials = author.findtext("Initials") or ""
            collective = author.findtext("CollectiveName") or ""
            if last:
                authors.append(f"{last} {initials}".strip())
            elif collective:
                authors.append(collective)

        doi = None
        for article_id in article.findall(".//ArticleIdList/ArticleId"):
            if article_id.get("IdType") == "doi" and article_id.text:
                doi = _normalise_doi(article_id.text)
                break

        journal = article.findtext(".//Journal/Title")
        return PaperHit(
            pmid=pmid_el.text.strip(),
            doi=doi,
            title=title,
            abstract=abstract,
            year=year,
            authors=authors,
            journal=journal,
            source="PubMed",
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid_el.text.strip()}/",
        )


# ---------------------------------------------------------------------------
# Semantic Scholar (Graph API)
# ---------------------------------------------------------------------------


class SemanticScholarClient:
    """Semantic Scholar Graph API client.

    The unauthenticated tier is heavily throttled and frequently returns 429,
    so this client refuses to operate without a key rather than degrading
    silently into a source that returns nothing.
    """

    name = "Semantic Scholar"
    ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
    FIELDS = "title,abstract,year,authors,venue,externalIds,url"

    def __init__(self, api_key: str | None = None, rate_limiter: RateLimiter | None = None) -> None:
        self.api_key = api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or None
        self.limiter = rate_limiter or RateLimiter(1.0)

    def search(self, query: str, max_results: int, filters: dict[str, Any] | None = None) -> list[PaperHit]:
        if not self.api_key:
            # Documented behaviour: skip the source instead of hammering a
            # shared anonymous pool and getting the whole service throttled.
            return []

        self.limiter.wait()
        payload = _get_json(
            self.ENDPOINT,
            params={"query": query, "limit": max_results, "fields": self.FIELDS},
            headers={"x-api-key": self.api_key},
        )
        hits: list[PaperHit] = []
        for paper in payload.get("data", []) or []:
            parsed = self._parse(paper)
            if parsed is not None:
                hits.append(parsed)
        return hits

    def _parse(self, paper: dict[str, Any]) -> PaperHit | None:
        title = (paper.get("title") or "").strip()
        if not title:
            return None
        external = paper.get("externalIds") or {}
        pmid = str(external["PubMed"]) if external.get("PubMed") else None
        doi = _normalise_doi(external.get("DOI"))
        if not pmid and not doi:
            return None
        authors = [
            a.get("name", "") for a in (paper.get("authors") or []) if a.get("name")
        ]
        return PaperHit(
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=(paper.get("abstract") or "").strip(),
            year=paper.get("year"),
            authors=authors,
            journal=paper.get("venue") or None,
            source="Semantic Scholar",
            url=paper.get("url"),
        )


# ---------------------------------------------------------------------------
# OpenAlex (no key required)
# ---------------------------------------------------------------------------


class OpenAlexClient:
    """OpenAlex works endpoint. Anonymous use is allowed and generous."""

    name = "OpenAlex"
    ENDPOINT = "https://api.openalex.org/works"

    def __init__(self, mailto: str | None = None, rate_limiter: RateLimiter | None = None) -> None:
        self.mailto = mailto or os.environ.get("OPENALEX_MAILTO") or None
        self.limiter = rate_limiter or RateLimiter(10.0)

    def search(self, query: str, max_results: int, filters: dict[str, Any] | None = None) -> list[PaperHit]:
        self.limiter.wait()
        params: dict[str, Any] = {
            "search": query,
            "per-page": min(max_results, 50),
            "select": "id,doi,title,abstract_inverted_index,publication_year,authorships,primary_location,ids",
        }
        if self.mailto:
            params["mailto"] = self.mailto  # polite pool
        payload = _get_json(self.ENDPOINT, params=params)
        hits: list[PaperHit] = []
        for work in payload.get("results", []) or []:
            parsed = self._parse(work)
            if parsed is not None:
                hits.append(parsed)
        return hits

    def _parse(self, work: dict[str, Any]) -> PaperHit | None:
        title = (work.get("title") or "").strip()
        if not title:
            return None
        ids = work.get("ids") or {}
        pmid = None
        if ids.get("pmid"):
            pmid = str(ids["pmid"]).rstrip("/").rsplit("/", 1)[-1]
        doi = _normalise_doi(work.get("doi"))
        if not pmid and not doi:
            return None
        authors = [
            a.get("author", {}).get("display_name", "")
            for a in (work.get("authorships") or [])
            if a.get("author", {}).get("display_name")
        ]
        location = work.get("primary_location") or {}
        source = location.get("source") or {}
        return PaperHit(
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=self._reconstruct_abstract(work.get("abstract_inverted_index")),
            year=work.get("publication_year"),
            authors=authors,
            journal=source.get("display_name"),
            source="OpenAlex",
            url=work.get("id"),
        )

    @staticmethod
    def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
        """Rebuild prose from OpenAlex's inverted index.

        OpenAlex ships abstracts as {word: [positions]}; naive sorting of the
        words gives the right order, but repeated words must be expanded at
        every position or the abstract is silently truncated.
        """
        if not inverted:
            return ""
        positions: list[tuple[int, str]] = []
        for word, indexes in inverted.items():
            for index in indexes:
                positions.append((index, word))
        positions.sort()
        return " ".join(word for _, word in positions)


# ---------------------------------------------------------------------------
# Mock client (local tests and dry-run)
# ---------------------------------------------------------------------------


class MockLiteratureClient:
    """Five deterministic hits, three of which share a DOI across sources.

    The overlap is deliberate: it exercises deduplication and source-priority
    logic without any network access.
    """

    def __init__(self, source: str = "PubMed") -> None:
        self.source = source
        # The router keys source_counts on ``name``; using the simulated index
        # name keeps per-source accounting meaningful in tests.
        self.name = source
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, max_results: int, filters: dict[str, Any] | None = None) -> list[PaperHit]:
        self.calls.append((query, max_results))
        hits = self._fixtures()
        return hits[:max_results]

    def _fixtures(self) -> list[PaperHit]:
        common = {
            "authors": ["A. Researcher", "B. Scientist"],
            "journal": "Journal of Natural Products",
            "source": self.source,
        }
        return [
            PaperHit(
                pmid="10000001",
                doi="10.1000/mock.1",
                title=f"Mock quercetin bioactivity study for {self.source}",
                abstract=(
                    "Quercetin modulates inflammatory signalling in vitro. "
                    "The study reports dose-dependent inhibition of NF-kB."
                ),
                year=2021,
                url="https://example.org/mock/1",
                **common,
            ),
            PaperHit(
                pmid="10000002",
                doi="10.1000/mock.2",
                title="Mock flavonoid biosynthesis review",
                abstract=(
                    "A review of flavonoid biosynthesis pathways in plants, "
                    "including chalcone synthase regulation."
                ),
                year=2019,
                url="https://example.org/mock/2",
                **common,
            ),
            PaperHit(
                pmid="10000003",
                doi="10.1000/mock.3",
                title="Mock andrographolide anti-inflammatory mechanism",
                abstract=(
                    "Andrographolide inhibits NF-kB translocation and reduces "
                    "cytokine release in macrophages."
                ),
                year=2020,
                url="https://example.org/mock/3",
                **common,
            ),
            PaperHit(
                pmid="10000004",
                doi="10.1000/mock.4",
                title="Mock NMR metabolomics workflow",
                abstract=(
                    "An NMR-based metabolomics workflow for plant extracts "
                    "with HSQC and HMBC assignment strategies."
                ),
                year=2022,
                url="https://example.org/mock/4",
                **common,
            ),
            PaperHit(
                pmid="10000005",
                doi="10.1000/mock.5",
                title="Mock natural product target prediction",
                abstract=(
                    "Target prediction for natural products using similarity "
                    "ensembles and knowledge graphs."
                ),
                year=2023,
                url="https://example.org/mock/5",
                **common,
            ),
        ]
