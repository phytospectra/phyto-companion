"""NMR structure-search router: fan out to databases, merge, rank (ADR-0031).

The router is deliberately thin.  Matching is a nearest-neighbour assignment
done by each client (``assign_peaks``); the router's job is to combine hits
across databases, keep database provenance intact, and enforce the one rule a
single-database view cannot see: **a commercially restricted record must never
appear in a commercial-tier response**, even if it ranks first.

A failing database never fails the whole search.  As with literature, a
partial result with an explicit gap is more useful than a 500, and the gap is
recorded in ``database_counts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from phyto_shared.nmr_search_models import (
    CompoundShiftResult,
    NMRSpectrumHit,
    NMRSpectrumSearchResult,
)

from .clients import NMRSearchClient, rank_hits

__all__ = ["NMRSearchRouter", "SearchOutcome"]


@dataclass
class SearchOutcome:
    """A search result plus the per-database errors that shaped it."""

    result: NMRSpectrumSearchResult
    errors: dict[str, str] = field(default_factory=dict)


class NMRSearchRouter:
    """Queries every configured database and merges the ranked hits."""

    def __init__(self, clients: list[NMRSearchClient]) -> None:
        if not clients:
            raise ValueError("at least one NMR search client is required")
        self.clients = clients

    def search_by_peaks(
        self,
        peaks: list[tuple[float, float]],
        *,
        h_tolerance: float,
        c_tolerance: float,
        max_results: int,
        databases: list[str] | None = None,
        include_noncommercial: bool = True,
    ) -> SearchOutcome:
        """Search each database and return merged, ranked hits.

        ``include_noncommercial=False`` drops CC-BY-NC records (NP-MRD and the
        local fixture) for the commercial tier.  Filtering happens before
        ranking so a dropped record cannot occupy a result slot.
        """
        selected = [
            client for client in self.clients
            if databases is None or client.name in databases
        ]

        collected: list[NMRSpectrumHit] = []
        counts: dict[str, int] = {}
        errors: dict[str, str] = {}

        for client in selected:
            try:
                hits = client.search_by_peaks(
                    peaks, h_tolerance, c_tolerance, max_results
                )
            except Exception as exc:  # one dead database must not sink the search
                errors[client.name] = str(exc)
                counts[client.name] = 0
                continue
            counts[client.name] = len(hits)
            collected.extend(hits)

        if not include_noncommercial:
            collected = [h for h in collected if h.license.commercial_use]

        ranked = rank_hits(collected, max_results)
        result = NMRSpectrumSearchResult(
            hits=ranked,
            total_count=len(ranked),
            database_counts=counts,
            query_peak_count=len(peaks),
        )
        return SearchOutcome(result=result, errors=errors)

    def get_compound_shifts(
        self, *, name: str | None, database_id: str | None
    ) -> CompoundShiftResult | None:
        """Return the first database record that resolves the identifier."""
        for client in self.clients:
            record = client.get_compound_shifts(name, database_id)
            if record is not None:
                return record
        return None
