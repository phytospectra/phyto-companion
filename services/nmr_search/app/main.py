"""FastAPI entrypoint for the NMR structure-search service (ADR-0031).

Local plane: every request is served by ``MockNMRSearchClient`` (fixture data,
no network).  Cloud plane (``PHYTO_ENV=cloud``): fans out to NP-MRD, BMRB and
nmrshiftdb2.

Every response carries ``provenance_manifest_id``, and every hit carries its
own database source plus licence metadata -- because licence terms differ per
database and decide whether a record may be used commercially (ADR-0026).
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError
from phyto_shared.nmr_search_models import (
    CompoundShiftRequest,
    CompoundShiftResult,
    NMRSpectrumSearchRequest,
    NMRSpectrumSearchResult,
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
    BMRBClient,
    MockNMRSearchClient,
    NMRSearchClient,
    NMRShiftDB2Client,
    NPMRDClient,
)
from .router import NMRSearchRouter

router = APIRouter()


@lru_cache(maxsize=1)
def get_query_cache() -> QueryCache:
    """Read-through cache (ADR-0046): Firestore in the cloud plane,
    in-process locally."""
    if os.environ.get("PHYTO_ENV") == "cloud":
        return FirestoreQueryCache()
    return InMemoryQueryCache()


@lru_cache(maxsize=1)
def get_router() -> NMRSearchRouter:
    """Build the router for the current plane.

    Only ``PHYTO_ENV=cloud`` enables live databases, so a stray deployment
    cannot silently start consuming provider quotas.
    """
    if os.environ.get("PHYTO_ENV") == "cloud":
        clients: list[NMRSearchClient] = [
            NPMRDClient(),
            BMRBClient(),
            NMRShiftDB2Client(),
        ]
    else:
        clients = [MockNMRSearchClient()]
    return NMRSearchRouter(clients)


class NMRSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: NMRSpectrumSearchResult
    provenance_manifest_id: str


class CompoundShiftsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: CompoundShiftResult
    provenance_manifest_id: str


def _manifest_id(route: str, payload: dict) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload))
        .with_model_version("nmr-search-service", __version__)
        .with_parameters({"route": route, "plane": os.environ.get("PHYTO_ENV", "local")})
        .with_environment()
        .build()
        .manifest_hash
    )


@router.post("/nmr_search/peaks", response_model=NMRSearchResponse)
def search_peaks(
    request: NMRSpectrumSearchRequest, response: Response
) -> NMRSearchResponse:
    """Peak search with a read-through cache (ADR-0046): hits are free
    (0 tokens) and identical to the upstream response."""
    peaks_normalized = sorted(
        (list(peak) for peak in request.peaks),
        key=lambda peak: (peak[0], peak[1]),
    )
    cache_key_value = cache_key(
        "nmr_search:peaks",
        "peaks",
        {
            "peaks": peaks_normalized,
            "h_tolerance_ppm": request.h_tolerance_ppm,
            "c_tolerance_ppm": request.c_tolerance_ppm,
            "max_results": request.max_results,
            "databases": sorted(request.databases),
            "include_noncommercial": request.include_noncommercial,
        },
    )
    cache = get_query_cache()
    cached = cache.get(cache_key_value)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return NMRSearchResponse.model_validate(cached)
    response.headers["X-Cache"] = "MISS"
    try:
        outcome = get_router().search_by_peaks(
            request.peaks,
            h_tolerance=request.h_tolerance_ppm,
            c_tolerance=request.c_tolerance_ppm,
            max_results=request.max_results,
            databases=request.databases,
            include_noncommercial=request.include_noncommercial,
        )
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    payload = NMRSearchResponse(
        result=outcome.result,
        provenance_manifest_id=_manifest_id("nmr_search.peaks", request.model_dump(mode="json")),
    )
    cache.put(cache_key_value, payload.model_dump(mode="json"))
    return payload


@router.post("/nmr_search/compound_shifts", response_model=CompoundShiftsResponse)
def compound_shifts(
    request: CompoundShiftRequest, response: Response
) -> CompoundShiftsResponse:
    cache_key_value = cache_key(
        "nmr_search:compound_shifts",
        request.name.strip().lower(),
        {"database_id": request.database_id},
    )
    cache = get_query_cache()
    cached = cache.get(cache_key_value)
    if cached is not None:
        response.headers["X-Cache"] = "HIT"
        return CompoundShiftsResponse.model_validate(cached)
    response.headers["X-Cache"] = "MISS"
    try:
        result = get_router().get_compound_shifts(
            name=request.name, database_id=request.database_id
        )
    except CloudServiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="no database record found")
    payload = CompoundShiftsResponse(
        result=result,
        provenance_manifest_id=_manifest_id(
            "nmr_search.compound_shifts", request.model_dump(mode="json")
        ),
    )
    cache.put(cache_key_value, payload.model_dump(mode="json"))
    return payload


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed."""
    application = FastAPI(
        title="PhytoCompanion NMR Structure Search Service",
        version=__version__,
        description="Peak-list structure search across NP-MRD, BMRB and nmrshiftdb2 with per-record licence metadata.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "nmr_search",
            "version": __version__,
            "plane": os.environ.get("PHYTO_ENV", "local"),
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
