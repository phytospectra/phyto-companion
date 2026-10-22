"""FastAPI entrypoint for the literature service.

Local plane: every request is served by ``MockLiteratureClient`` (no network,
no API keys).  Cloud plane: the router fans out to PubMed, Semantic Scholar
and OpenAlex.

Every response carries ``provenance_manifest_id``: a hash over the request and
the retrieval parameters, so a result can be traced back to exactly which
query produced it.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.literature_models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
    PaperHit,
)
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict
from phyto_shared.query_cache import (
    FirestoreQueryCache,
    InMemoryQueryCache,
    QueryCache,
    cache_key,
)

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .clients import (
    LiteratureClient,
    MockLiteratureClient,
    OpenAlexClient,
    PubMedClient,
    SemanticScholarClient,
)
from .router import LiteratureRouter

router = APIRouter()


@lru_cache(maxsize=1)
def get_query_cache() -> QueryCache:
    """Read-through cache (ADR-0046): Firestore in the cloud plane, in-
    process locally.  A cache hit is free (0 tokens) and identical to the
    upstream response."""
    if os.environ.get("PHYTO_ENV") == "cloud":
        return FirestoreQueryCache()
    return InMemoryQueryCache()


@lru_cache(maxsize=1)
def get_router() -> LiteratureRouter:
    """Build the router for the current plane.

    Set ``PHYTO_ENV=cloud`` to use the live indexes; anything else (including
    unset) uses the deterministic mock, so a stray deployment cannot silently
    start billing API calls.
    """
    if os.environ.get("PHYTO_ENV") == "cloud":
        clients: list[LiteratureClient] = [
            PubMedClient(),
            SemanticScholarClient(),
            OpenAlexClient(),
        ]
    else:
        clients = [
            MockLiteratureClient("PubMed"),
            MockLiteratureClient("OpenAlex"),
        ]
    return LiteratureRouter(clients)


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------


class LiteratureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: LiteratureSearchResult
    provenance_manifest_id: str


class CompoundLiteratureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_name: str = Field(..., min_length=1, max_length=200)
    max_results: int = Field(default=10, ge=1, le=100)


class TargetLiteratureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_name: str = Field(..., min_length=1, max_length=200)
    context: str | None = Field(
        default=None, max_length=300, description="Optional pathway or disease context"
    )
    max_results: int = Field(default=10, ge=1, le=100)


def _manifest_id(route: str, payload: dict) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload))
        .with_model_version("literature-service", __version__)
        .with_parameters({"route": route, "plane": os.environ.get("PHYTO_ENV", "local")})
        .with_environment()
        .build()
        .manifest_hash
    )


def _guarded_search(
    query: str,
    max_results: int,
    filters: dict | None,
    route: str,
    payload: dict,
    response: Response | None = None,
) -> LiteratureResponse:
    """Search with a read-through cache (ADR-0046).

    Hit: the identical stored response, ``X-Cache: HIT``, and the
    middleware refunds the reservation (0 tokens).  Miss: upstream call,
    cache write, ``X-Cache: MISS``, normal charge.
    """
    cache = get_query_cache()
    filters_for_key = {**(filters or {}), "max_results": max_results}
    key = cache_key(f"literature:{route}", query, filters_for_key)
    cached = cache.get(key)
    if cached is not None:
        if response is not None:
            response.headers["X-Cache"] = "HIT"
        return LiteratureResponse.model_validate(cached)
    if response is not None:
        response.headers["X-Cache"] = "MISS"
    try:
        result = get_router().search(query, max_results=max_results, filters=filters)
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    literature_response = LiteratureResponse(
        result=result, provenance_manifest_id=_manifest_id(route, payload)
    )
    cache.put(key, literature_response.model_dump(mode="json"))
    return literature_response


@router.post("/literature/search", response_model=LiteratureResponse)
def literature_search(
    request: LiteratureSearchRequest, response: Response
) -> LiteratureResponse:
    return _guarded_search(
        request.query,
        request.max_results,
        request.filters,
        "literature.search",
        request.model_dump(),
        response,
    )


@router.post("/literature/compound", response_model=LiteratureResponse)
def literature_compound(
    request: CompoundLiteratureRequest, response: Response
) -> LiteratureResponse:
    # Phrase the query for a compound rather than passing a bare name: a bare
    # name retrieves homonyms (e.g. a plant genus sharing a drug's name).
    query = f'"{request.compound_name}" AND (bioactivity OR mechanism OR pharmacology)'
    return _guarded_search(
        query, request.max_results, None, "literature.compound", request.model_dump(), response
    )


@router.post("/literature/target", response_model=LiteratureResponse)
def literature_target(
    request: TargetLiteratureRequest, response: Response
) -> LiteratureResponse:
    clause = f'"{request.target_name}"'
    if request.context:
        clause += f" AND ({request.context})"
    return _guarded_search(
        clause, request.max_results, None, "literature.target", request.model_dump(), response
    )


class SpeciesLiteratureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_name: str = Field(..., min_length=1, max_length=200)
    species_name: str = Field(..., min_length=1, max_length=200)
    max_results: int = Field(default=10, ge=1, le=100)


@router.post("/literature/species_search", response_model=LiteratureResponse)
def literature_species_search(
    request: SpeciesLiteratureRequest, response: Response
) -> LiteratureResponse:
    """Papers reporting this compound FROM this species (ADR-0052).

    The (compound AND species) query built by ``build_species_query``
    constrains retrieval to the organism, so hits are usable as
    ``reported_in_species_paper`` provenance on candidates and as LLM
    context for mechanism hypotheses.
    """
    from .species_search import build_species_query

    try:
        query = build_species_query(request.compound_name, request.species_name)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _guarded_search(
        query, request.max_results, None,
        "literature.species_search", request.model_dump(), response,
    )


@router.get("/literature/paper/{pmid}", response_model=PaperHit)
def literature_paper(pmid: str) -> PaperHit:
    """Fetch one paper by PMID.

    Served from a live search restricted to that PMID so the response is a
    real record rather than a reconstruction from cache.
    """
    if not pmid.isdigit():
        raise HTTPException(status_code=422, detail="PMID must be numeric")
    try:
        result = get_router().search(f"{pmid}[pmid]", max_results=1)
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    match = next((p for p in result.papers if p.pmid == pmid), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"PMID {pmid} not found")
    return match


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed."""
    application = FastAPI(
        title="PhytoCompanion Literature Service",
        version=__version__,
        description="Multi-index literature retrieval with deduplication and BM25 ranking.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "literature",
            "version": __version__,
            "plane": os.environ.get("PHYTO_ENV", "local"),
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
