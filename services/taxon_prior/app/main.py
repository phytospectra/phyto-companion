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
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
