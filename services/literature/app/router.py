"""Literature router: fan-out to all indexes, deduplicate, rank with BM25.

Responsibilities are split cleanly:

* ``deduplicate`` merges records that describe the same paper.  Identity is
  DOI first, then PMID, because a DOI is stable across indexes while PMID
  assignment is PubMed-specific (OpenAlex often has no PMID at all).
* ``bm25_rank`` scores title + abstract against the query.  BM25 rather than
  raw TF-IDF because abstracts vary in length by an order of magnitude and
  BM25's length normalisation stops long reviews from dominating.
* ``LiteratureRouter.search`` orchestrates the two and records which index
  supplied which hit.

A failing index never fails the whole search: the platform would rather
return three sources' worth of results with an explicit gap than nothing.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from phyto_shared.literature_models import (
    SOURCE_PRIORITY,
    LiteratureSearchResult,
    PaperHit,
)

from .clients import LiteratureClient

__all__ = ["LiteratureRouter", "deduplicate", "bm25_rank", "tokenize"]

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """
    a an the and or of in on for to with from by as at is are was were be been
    this that these those it its we our they their which who whom what when
    where how not no than then there here such can may might will would could
    should have has had do does did but if into over under between among
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


def deduplicate(hits: list[PaperHit]) -> list[PaperHit]:
    """Merge duplicates across indexes, honouring source priority (ADR-0030).

    The highest-priority record is kept as the primary hit and the other
    indexes are recorded in ``also_found_in``.  Fields missing from the
    primary record are filled from the duplicates -- OpenAlex, for example,
    is the only one of the three that reliably has no PMID, so a PubMed
    duplicate can complete an OpenAlex-sourced record.
    """
    by_key: dict[str, list[PaperHit]] = {}

    for hit in hits:
        keys = [hit.dedup_key]
        # A record with both identifiers may be duplicated on either side.
        if hit.doi and hit.pmid:
            keys.append(f"pmid:{hit.pmid}")
        for key in keys:
            by_key.setdefault(key, []).append(hit)

    # Union-find over the key graph so DOI-linked and PMID-linked records
    # collapse into one group even when the two identifiers differ.
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for group_keys in by_key.values():
        first = group_keys[0].dedup_key
        for hit in group_keys:
            union(first, hit.dedup_key)
            if hit.doi and hit.pmid:
                union(f"pmid:{hit.pmid}", hit.dedup_key)

    grouped: dict[str, list[PaperHit]] = {}
    for hit in hits:
        grouped.setdefault(find(hit.dedup_key), []).append(hit)

    merged: list[PaperHit] = []
    for group in grouped.values():
        primary = min(
            group,
            key=lambda h: SOURCE_PRIORITY.index(h.source)
            if h.source in SOURCE_PRIORITY
            else len(SOURCE_PRIORITY),
        )
        others = sorted(
            {h.source for h in group if h.source != primary.source},
            key=lambda s: SOURCE_PRIORITY.index(s) if s in SOURCE_PRIORITY else 99,
        )
        # Fill gaps from the highest-priority duplicate available.
        fillers = sorted(
            (h for h in group if h is not primary),
            key=lambda h: SOURCE_PRIORITY.index(h.source)
            if h.source in SOURCE_PRIORITY
            else len(SOURCE_PRIORITY),
        )
        updates: dict[str, Any] = {"source": primary.source, "also_found_in": others}
        if not primary.pmid:
            pmid_filler = next((h for h in fillers if h.pmid), None)
            if pmid_filler:
                updates["pmid"] = pmid_filler.pmid
        if not primary.doi:
            doi_filler = next((h for h in fillers if h.doi), None)
            if doi_filler:
                updates["doi"] = doi_filler.doi
        if not primary.abstract:
            abstract_filler = next((h for h in fillers if h.abstract), None)
            if abstract_filler:
                updates["abstract"] = abstract_filler.abstract
        if primary.year is None:
            year_filler = next((h for h in fillers if h.year is not None), None)
            if year_filler:
                updates["year"] = year_filler.year

        merged.append(primary.model_copy(update=updates))

    return merged


def bm25_rank(
    hits: list[PaperHit],
    query: str,
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[PaperHit]:
    """Score and sort hits by BM25 over title + abstract (higher is better).

    ``k1`` controls term-frequency saturation and ``b`` the length
    normalisation; the defaults are the standard Robertson/Sparck Jones
    values and were not tuned on this corpus.
    """
    if not hits:
        return []

    query_terms = tokenize(query)
    if not query_terms:
        return sorted(hits, key=lambda h: h.title.lower())

    docs = [tokenize(f"{h.title} {h.abstract}") for h in hits]
    n_docs = len(docs)
    avg_len = sum(len(d) for d in docs) / n_docs or 1.0

    # Document frequency per query term; a term must appear in some document
    # to contribute, so terms absent from every abstract cannot inflate idf.
    dfs: Counter[str] = Counter()
    for doc in docs:
        for term in set(query_terms) & set(doc):
            dfs[term] += 1

    def idf(term: str) -> float:
        df = dfs.get(term, 0)
        return math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))

    scored: list[tuple[float, PaperHit]] = []
    for hit, doc in zip(hits, docs):
        counts = Counter(doc)
        length = len(doc) or 1
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            numerator = tf * (k1 + 1.0)
            denominator = tf + k1 * (1.0 - b + b * length / avg_len)
            score += idf(term) * numerator / denominator
        scored.append((score, hit))

    scored.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
    return [
        hit.model_copy(update={"relevance_score": round(score, 6)})
        for score, hit in scored
    ]


class LiteratureRouter:
    """Queries every configured index, deduplicates, and ranks."""

    def __init__(self, clients: list[LiteratureClient]) -> None:
        if not clients:
            raise ValueError("at least one literature client is required")
        self.clients = clients

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> LiteratureSearchResult:
        filters = filters or {}
        collected: list[PaperHit] = []
        source_counts: dict[str, int] = {}
        errors: dict[str, str] = {}

        for client in self.clients:
            try:
                hits = client.search(query, max_results, filters)
            except Exception as exc:  # one dead index must not sink the search
                errors[client.name] = str(exc)
                source_counts[client.name] = 0
                continue
            source_counts[client.name] = len(hits)
            collected.extend(hits)

        merged = deduplicate(collected)
        ranked = bm25_rank(merged, query)[:max_results]

        result = LiteratureSearchResult(
            papers=ranked,
            total_count=len(ranked),
            source_counts=source_counts,
        )
        if errors:
            # Surfaced on the object rather than raised: partial results with
            # an explicit gap are more useful than a 500.
            result = result.model_copy(update={"source_counts": {**source_counts, **{f"{k}_error": 1 for k in errors}}})
        return result
