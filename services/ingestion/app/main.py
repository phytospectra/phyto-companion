"""FastAPI entrypoint for the ingestion service."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from phyto_shared.data_governance import (
    ABSStatus,
    DataTier,
    DEFAULT_ABS_STATUS,
    DEFAULT_DATA_TIER,
    SOURCE_DATASET_IDS,
)
from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Spectrum
from phyto_shared.provenance import ProvenanceBuilder, sha256_bytes

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .nmr_parser import parse_hsqc_csv, parse_peak_1d_csv, parse_peak_csv

router = APIRouter()


class PeakCSVRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_b64: str = Field(..., min_length=1)
    spectrum_id: str = "spectrum_1"
    kind: str | None = None  # None for auto-detect, or '1H', '13C', 'HSQC'
    # Governance (ADR-0056): defaulted to the most restrictive settings so
    # nothing becomes public or licensable by accident.
    data_tier: DataTier = DEFAULT_DATA_TIER
    abs_status: ABSStatus = DEFAULT_ABS_STATUS
    source_dataset: str = "user_upload"

    @model_validator(mode="after")
    def _source_dataset_known(self) -> "PeakCSVRequest":
        if self.source_dataset not in SOURCE_DATASET_IDS:
            raise ValueError(
                f"unknown source_dataset {self.source_dataset!r}; must be one "
                f"of {SOURCE_DATASET_IDS}"
            )
        return self


class SpectrumResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrum: Spectrum
    provenance_manifest_id: str


@router.post("/ingest/peak_csv", response_model=SpectrumResponse)
def ingest_peak_csv(request: PeakCSVRequest) -> SpectrumResponse:
    try:
        raw_bytes = base64.b64decode(request.content_b64, validate=True)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 payload: {e}") from e

    try:
        if request.kind == "HSQC":
            spectrum = parse_hsqc_csv(raw_bytes, spectrum_id=request.spectrum_id)
        elif request.kind in ("1H", "13C"):
            spectrum = parse_peak_1d_csv(raw_bytes, kind=request.kind, spectrum_id=request.spectrum_id)
        else:
            spectrum = parse_peak_csv(raw_bytes)
    except InvalidInputError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    spectrum.data_tier = request.data_tier
    spectrum.abs_status = request.abs_status
    spectrum.source_dataset = request.source_dataset

    manifest_hash = (
        ProvenanceBuilder()
        .with_input_hash("peak_csv", sha256_bytes(raw_bytes))
        .with_model_version("ingestion-service", __version__)
        .with_data_governance(
            data_tier=request.data_tier,
            abs_status=request.abs_status,
            source_datasets=[request.source_dataset],
        )
        .with_environment()
        .build()
        .manifest_hash
    )
    return SpectrumResponse(spectrum=spectrum, provenance_manifest_id=manifest_hash)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed.

    Middleware is installed before routes are added so no endpoint can be
    registered outside the metering boundary.
    """
    application = FastAPI(
        title="PhytoCompanion Ingestion Service",
        version=__version__,
        description="Spectral file parsing, validation, and provenance hashing.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "ingestion",
            "version": __version__,
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
