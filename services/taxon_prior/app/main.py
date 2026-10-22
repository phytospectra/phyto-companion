"""FastAPI entrypoint for the taxon-prior service.

Local plane: snapshot-backed clients and the mock taxonomy resolver, so
the whole prior builds offline.  Cloud plane: the same endpoint fronts
the live LOTUS/KNApSAcK/NPASS clients (ADR-0054).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict
from phyto_shared.taxonomy_models import TaxonPrior

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .clients import KNApSAcKClient, LOTUSClient, NPASSClient
from .markers import evaluate_markers, load_markers
from .prior import build_taxon_prior

router = APIRouter()

# Local plane: mock resolver (taxonomy service contract), snapshot clients.
from taxonomy.clients import MockTaxonomyClient  # noqa: E402

_RESOLVER = MockTaxonomyClient()


class TaxonPriorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: str | None = Field(default=None, min_length=1)
    genus: str | None = Field(default=None, min_length=1)
    family: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _at_least_one_rank(self) -> "TaxonPriorRequest":
        if not (self.species or self.genus or self.family):
            raise ValueError("provide at least one of species, genus, family")
        return self


class TaxonPriorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prior: TaxonPrior
    provenance_manifest_id: str


def _manifest(payload: BaseModel) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("taxon-prior-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )


@router.post("/taxon_prior/build", response_model=TaxonPriorResponse)
def taxon_prior_build(request: TaxonPriorRequest) -> TaxonPriorResponse:
    try:
        prior = build_taxon_prior(
            request.species,
            request.genus,
            request.family,
            clients=[LOTUSClient(), KNApSAcKClient(), NPASSClient()],
            resolver=_RESOLVER,
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CloudServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TaxonPriorResponse(prior=prior, provenance_manifest_id=_manifest(request))


class MarkerCheckRequest(BaseModel):
    """One observed peak checked against the species' pharmacopoeial
    markers (ADR-0066).  Inputs are optional; unchecked expectations
    stay None."""

    model_config = ConfigDict(extra="forbid")

    species: str = Field(..., min_length=1)
    observed_rt_min: float | None = Field(default=None, ge=0.0)
    observed_uv_maxima_nm: list[float] | None = None


class MarkerCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: str
    n_markers: int
    hits: list[dict[str, Any]]
    provenance_manifest_id: str


@router.post("/pharmacopoeial_markers/check", response_model=MarkerCheckResponse)
def pharmacopoeial_markers_check(request: MarkerCheckRequest) -> MarkerCheckResponse:
    """Evaluate the species' curated pharmacopoeial markers (ADR-0066)."""
    hits = evaluate_markers(
        request.species, request.observed_rt_min, request.observed_uv_maxima_nm
    )
    return MarkerCheckResponse(
        species=request.species,
        n_markers=len(hits),
        hits=[h.model_dump() for h in hits],
        provenance_manifest_id=_manifest(request),
    )


@router.get("/pharmacopoeial_markers/{species}")
def pharmacopoeial_markers_for_species(species: str) -> dict[str, Any]:
    """The curated marker list for a species (empty when none -- graceful)."""
    from .markers import markers_for_species

    markers = markers_for_species(species)
    return {
        "species": species,
        "n_markers": len(markers),
        "markers": [m.model_dump() for m in markers],
    }


def create_app(config: AuthConfig | None = None) -> FastAPI:
    application = FastAPI(
        title="PhytoCompanion Taxon Prior Service",
        version=__version__,
        description="Compounds reported from a taxon: LOTUS, KNApSAcK, NPASS "
        "with per-report provenance (ADR-0054).",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "taxon_prior",
            "version": __version__,
            "clients": ["LOTUS", "KNApSAcK", "NPASS"],
            "mode": "snapshot",
            "pharmacopoeial_marker_species": len(load_markers()),
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
