"""Compound-in-species search tests (ADR-0052, Part 6)."""

from __future__ import annotations

import pytest

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.literature_models import PaperHit

from literature.species_search import build_species_query, search_compound_in_species


class _RecordingClient:
    """Offline PubMed stand-in that records the query it was asked."""

    def __init__(self, hits: int = 1) -> None:
        self.calls: list[tuple[str, int]] = []
        self._hits = hits

    def search(self, query, max_results, filters=None):
        self.calls.append((query, max_results))
        return [
            PaperHit(
                pmid=f"3000000{index}",
                title=f"Report {index} of the compound from the species",
                source="PubMed",
            )
            for index in range(min(self._hits, max_results))
        ]


def test_query_constrains_both_operands_and_rejects_blanks() -> None:
    assert (
        build_species_query("panduratin A", "Boesenbergia rotunda")
        == '("panduratin A") AND ("Boesenbergia rotunda")'
    )
    with pytest.raises(InvalidInputError):
        build_species_query("", "Boesenbergia rotunda")
    with pytest.raises(InvalidInputError):
        build_species_query("panduratin A", "   ")


def test_search_uses_the_and_query_and_returns_hits() -> None:
    client = _RecordingClient(hits=2)
    hits = search_compound_in_species(
        "panduratin A", "Boesenbergia rotunda", max_results=5, client=client
    )
    query, max_results = client.calls[0]
    assert '"panduratin A"' in query and '"Boesenbergia rotunda"' in query
    assert " AND " in query
    assert max_results == 5
    assert len(hits) == 2
