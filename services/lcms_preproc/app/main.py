"""FastAPI entrypoint for the LC-MS preprocessing adapter (ADR-0069).

Ingests an exported XCMS / MZmine 3 / MS-DIAL feature table (or the
tool-neutral long format), normalises the schema, applies opt-in
sample normalisation (pass-through by default) and returns the
normalised matrix plus a ``PreprocessingRecord`` recording what was
found in the file -- never invented.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.metabolomics import PreprocessingRecord

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .adapter import SkipAdaptation, adapt_csv, apply_normalisation, build_record

router = APIRouter()


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv_text: str = Field(..., min_length=1)
    filename: str | None = None
    normalisation: Literal["none", "tic", "sum"] = "none"


class IngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "applied" | "skipped"
    reason: str | None = None
    feature_ids: list[str] = Field(default_factory=list)
    mz: list[float] = Field(default_factory=list)
    rt_min: list[float] = Field(default_factory=list)
    sample_ids: list[str] = Field(default_factory=list)
    matrix: list[list[float]] | None = None
    record: PreprocessingRecord | None = None
    notes: list[str] = Field(default_factory=list)


@router.post("/lcms-preproc/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest) -> IngestResponse:
    try:
        table = adapt_csv(request.csv_text)
        table = apply_normalisation(table, request.normalisation)
    except SkipAdaptation as skip:
        return IngestResponse(status="skipped", reason=skip.reason)
    matrix = [
        [table.intensities[f].get(s, 0.0) for s in table.sample_ids]
        for f in table.feature_ids
    ]
    return IngestResponse(
        status="applied",
        feature_ids=table.feature_ids,
        mz=table.mz,
        rt_min=table.rt_min,
        sample_ids=table.sample_ids,
        matrix=matrix,
        record=build_record(table, request.normalisation),
        notes=table.notes,
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed (ADR-0039)."""
    application = FastAPI(
        title="PhytoCompanion LC-MS Preprocessing Adapter",
        version=__version__,
        description="XCMS / MZmine 3 / MS-DIAL feature-table adapter "
        "(ADR-0069): schema normalisation, no peak picking.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "lcms_preproc",
            "version": __version__,
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
