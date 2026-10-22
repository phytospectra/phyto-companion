"""FastAPI entrypoint for the taxonomy service.

Local plane: no network -- the mock resolver answers for the pilot
species set.  Cloud plane: the same endpoints front GBIF with ITIS
fallback (ADR-0053).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict
from phyto_shared.taxonomy_models import TaxonNode, TaxonResolution

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .clients import GBIFClient, ITISClient, MockTaxonomyClient, get_lineage, resolve_species

router = APIRouter()

_USE_MOCK = True  # local plane; the cloud deployment swaps the live pair in


def _clients() -> tuple[Any, Any]:
    if _USE_MOCK:
        mock = MockTaxonomyClient()
        return mock, mock
    return GBIFClient(), ITISClient()


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)


class ResolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: TaxonResolution
    provenance_manifest_id: str


class LineageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxon_key: str = Field(..., min_length=1)
    source: str = Field(default="GBIF")


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lineage: list[TaxonNode]
    provenance_manifest_id: str


def _manifest(payload: BaseModel) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("taxonomy-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )


@router.post("/taxonomy/resolve", response_model=ResolveResponse)
def taxonomy_resolve(request: ResolveRequest) -> ResolveResponse:
    gbif, itis = _clients()
    try:
        resolution = resolve_species(request.name, gbif=gbif, itis=itis)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CloudServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ResolveResponse(
        resolution=resolution, provenance_manifest_id=_manifest(request)
    )


@router.post("/taxonomy/lineage", response_model=LineageResponse)
def taxonomy_lineage(request: LineageRequest) -> LineageResponse:
    if _USE_MOCK:
        lineage: list[TaxonNode] = MockTaxonomyClient().lineage(request.taxon_key)
    else:
        lineage = get_lineage(request.taxon_key, source=request.source)
    return LineageResponse(
        lineage=lineage, provenance_manifest_id=_manifest(request)
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(
        title="PhytoCompanion Taxonomy Service",
        version=__version__,
        description="Species resolution (GBIF primary, ITIS fallback) and lineage walks.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "taxonomy",
            "version": __version__,
            "resolver": "mock" if _USE_MOCK else "live",
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
