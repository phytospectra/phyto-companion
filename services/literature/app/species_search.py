"""Compound-in-species literature search (ADR-0052, Part 6).

``search_compound_in_species`` constrains PubMed to papers reporting a
named compound FROM a named species -- ``(compound) AND (species)`` --
so the hits become "this organism makes this molecule" evidence rather
than generic compound bioactivity papers.  The reranker attaches those
hits to candidates as ``reported_in_species_paper`` provenance, and the
mechanism-hypothesis workflow feeds them to the LLM as context.

The query builder is pure and offline-testable; the network client is
injected, defaulting to a real ``PubMedClient``.
"""

from __future__ import annotations

from typing import Protocol

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.literature_models import PaperHit

from .clients import PubMedClient

__all__ = ["build_species_query", "search_compound_in_species", "SpeciesSearchClient"]


def build_species_query(compound_name: str, species_name: str) -> str:
    """The PubMed term for "this compound from this species".

    Both operands are quoted so multi-word names (subspecies authors,
    compound qualifiers) stay one operand each; a bare AND of unquoted
    phrases would silently OR word-by-word inside PubMed's parser.
    """
    compound = (compound_name or "").strip()
    species = (species_name or "").strip()
    if not compound or not species:
        raise InvalidInputError(
            "compound and species names are both required",
            context={"compound_name": compound_name, "species_name": species_name},
        )
    return f'("{compound}") AND ("{species}")'


class SpeciesSearchClient(Protocol):
    """Anything with a PubMed-shaped ``search`` method."""

    def search(self, query: str, max_results: int, filters: dict | None = None) -> list[PaperHit]: ...


def search_compound_in_species(
    compound_name: str,
    species_name: str,
    max_results: int = 10,
    client: SpeciesSearchClient | None = None,
) -> list[PaperHit]:
    """Papers reporting ``compound_name`` from ``species_name`` (PubMed).

    Raises ``InvalidInputError`` for blank names; transport failures
    surface as ``CloudServiceError`` from the underlying client.
    """
    query = build_species_query(compound_name, species_name)
    active = client or PubMedClient()
    return active.search(query, max_results)
