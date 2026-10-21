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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.literature_models import (
    LiteratureSearchRequest,
    LiteratureSearchResult,
    PaperHit,
)
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

from . import __version__
from .clients import (
    LiteratureClient,
    MockLiteratureClient,
    OpenAlexClient,
    PubMedClient,
    SemanticScholarClient,
)
from .router import LiteratureRouter

app = FastAPI(
    title="PhytoCompanion Literature Service",
    version=__version__,
    description="Multi-index literature retrieval with deduplication and BM25 ranking.",
)


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


def _guarded_search(query: str, max_results: int, filters: dict | None, route: str, payload: dict) -> LiteratureResponse:
    try:
        result = get_router().search(query, max_results=max_results, filters=filters)
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return LiteratureResponse(result=result, provenance_manifest_id=_manifest_id(route, payload))


@app.get("/health")
def health() -> dict[str, str]:
    plane = os.environ.get("PHYTO_ENV", "local")
    return {"status": "ok", "service": "literature", "version": __version__, "plane": plane}


@app.post("/literature/search", response_model=LiteratureResponse)
def literature_search(request: LiteratureSearchRequest) -> LiteratureResponse:
    return _guarded_search(
        request.query,
        request.max_results,
        request.filters,
        "literature.search",
        request.model_dump(),
    )


@app.post("/literature/compound", response_model=LiteratureResponse)
def literature_compound(request: CompoundLiteratureRequest) -> LiteratureResponse:
    # Phrase the query for a compound rather than passing a bare name: a bare
    # name retrieves homonyms (e.g. a plant genus sharing a drug's name).
    query = f'"{request.compound_name}" AND (bioactivity OR mechanism OR pharmacology)'
    return _guarded_search(
        query, request.max_results, None, "literature.compound", request.model_dump()
    )


@app.post("/literature/target", response_model=LiteratureResponse)
def literature_target(request: TargetLiteratureRequest) -> LiteratureResponse:
    clause = f'"{request.target_name}"'
    if request.context:
        clause += f" AND ({request.context})"
    return _guarded_search(
        clause, request.max_results, None, "literature.target", request.model_dump()
    )


@app.get("/literature/paper/{pmid}", response_model=PaperHit)
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
