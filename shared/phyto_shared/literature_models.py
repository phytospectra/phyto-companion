"""Literature retrieval and LLM I/O models (ADR-0029, ADR-0030).

Design notes
------------
* ``PaperHit.source`` records WHICH index supplied a reference.  After
  deduplication a paper may be found in several indexes; ``also_found_in``
  preserves that fact instead of silently discarding it, because source
  coverage is itself evidence about how well-known a paper is.
* An identifier is optional per source (OpenAlex has no PMIDs), but every
  hit must carry at least one of PMID or DOI, otherwise it cannot be
  deduplicated or cited and is therefore not a usable citation target.
* ``LLMResponse.grounding_score`` is computed locally and deterministically
  from the returned text; it is never self-reported by the model.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .models import Candidate, EvidenceGraph, StrictModel

__all__ = [
    "LiteratureSource",
    "PaperHit",
    "LiteratureSearchRequest",
    "LiteratureSearchResult",
    "LLMOperation",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
]


LiteratureSource = Literal["PubMed", "Semantic Scholar", "OpenAlex"]

# Routing target for Tier-3 generation (ADR-0034).  Omitted (``None``) means
# "use the deployment default": Vertex in the cloud plane, the offline mock
# locally, so a request stays valid in both planes.
LLMProvider = Literal["vertex", "openrouter", "local"]

# PubMed > Semantic Scholar > OpenAlex (ADR-0030). Lower index wins a
# deduplication conflict, so the more curated record is the one kept.
SOURCE_PRIORITY: tuple[str, ...] = ("PubMed", "Semantic Scholar", "OpenAlex")

LLMOperation = Literal[
    "explain",
    "hypothesize",
    "recommend_experiments",
    "summarize_literature",
]

RelevanceScore = Annotated[float, Field(ge=0.0, le=1.0)]


class PaperHit(StrictModel):
    """One retrieved publication."""

    pmid: str | None = Field(
        default=None, description="PubMed ID (digits only), when the index supplies one"
    )
    doi: str | None = Field(default=None, description="Normalised DOI, e.g. '10.1021/x'")
    title: str = Field(..., min_length=1)
    abstract: str = Field(default="", description="Empty when the index returns none")
    year: int | None = Field(default=None, ge=1800, le=2200)
    authors: list[str] = Field(default_factory=list)
    journal: str | None = None
    source: LiteratureSource
    url: str | None = None
    relevance_score: RelevanceScore = 0.0
    also_found_in: list[LiteratureSource] = Field(
        default_factory=list,
        description="Other indexes that returned this same paper (deduplication provenance)",
    )

    @model_validator(mode="after")
    def _has_identifier(self) -> "PaperHit":
        if not self.pmid and not self.doi:
            raise ValueError(
                "a PaperHit needs at least one of pmid or doi to be citable"
            )
        return self

    @property
    def dedup_key(self) -> str:
        """Stable identity used to merge hits across indexes."""
        if self.doi:
            return f"doi:{self.doi.lower().strip()}"
        return f"pmid:{self.pmid}"


class LiteratureSearchRequest(StrictModel):
    """A free-text literature search."""

    query: str = Field(..., min_length=2, max_length=500)
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific filters (year range, journal, open access...)",
    )
    max_results: int = Field(default=10, ge=1, le=100)


class LiteratureSearchResult(StrictModel):
    """Ranked, deduplicated papers plus their provenance breakdown."""

    papers: list[PaperHit] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    source_counts: dict[str, int] = Field(
        default_factory=dict,
        description="How many hits each index returned BEFORE deduplication",
    )


class LLMRequest(StrictModel):
    """Input to a Tier-3 LLM operation.

    ``context_papers`` is REQUIRED and must be non-empty: the platform's
    RAG rule (ADR-0029) is that the model never answers without retrieved
    evidence.  The field is required (omitting it is a schema error) but
    deliberately NOT ``min_length=1``: the API converts an empty list into a
    specific HTTP 400 rather than Pydantic's generic 422, because the caller
    needs to be told to run a literature search first.  The QA/Critic agent
    enforces the same rule again before anything is persisted.
    """

    operation: LLMOperation
    provider: LLMProvider | None = Field(
        default=None,
        description="Which LLM backend to use; None = deployment default (vertex in cloud, mock offline)",
    )
    context_papers: list[PaperHit] = Field(
        ...,
        description="Retrieved evidence; must be non-empty (enforced with HTTP 400)",
    )
    candidate: Candidate | None = None
    evidence_graph: EvidenceGraph | None = None
    question: str | None = Field(
        default=None, max_length=2000, description="Optional user question"
    )


class LLMResponse(StrictModel):
    """Output of a Tier-3 operation.

    ``citations`` lists the identifiers the generated text actually cites;
    ``grounding_score`` is computed independently of the model.
    """

    text: str = Field(..., min_length=1)
    citations: list[str] = Field(default_factory=list)
    grounding_score: Annotated[float, Field(ge=0.0, le=1.0)]
    model_id: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    claims_total: int = Field(
        default=0, ge=0, description="Number of factual claims detected in text"
    )
    claims_uncited: int = Field(
        default=0, ge=0, description="Claims carrying no citation (must be 0 to pass QA)"
    )
