"""FastAPI entrypoint for the ingestion service."""

from __future__ import annotations

import base64
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Spectrum
from phyto_shared.provenance import ProvenanceBuilder, sha256_bytes

from . import __version__
from .nmr_parser import parse_hsqc_csv, parse_peak_1d_csv, parse_peak_csv

app = FastAPI(
    title="PhytoCompanion Ingestion Service",
    version=__version__,
    description="Spectral file parsing, validation, and provenance hashing.",
)


class PeakCSVRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_b64: str = Field(..., min_length=1)
    spectrum_id: str = "spectrum_1"
    kind: str | None = None  # None for auto-detect, or '1H', '13C', 'HSQC'


class SpectrumResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrum: Spectrum
    provenance_manifest_id: str


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "ingestion", "version": __version__}


@app.post("/ingest/peak_csv", response_model=SpectrumResponse)
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

    manifest_hash = (
        ProvenanceBuilder()
        .with_input_hash("peak_csv", sha256_bytes(raw_bytes))
        .with_model_version("ingestion-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )
    return SpectrumResponse(spectrum=spectrum, provenance_manifest_id=manifest_hash)
