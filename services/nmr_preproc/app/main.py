"""FastAPI entrypoint for the NMR preprocessing service (ADR-0068).

One endpoint runs the documented in-house pipeline over Bruker-style
experiment directories and returns the bucketed matrix, the ppm axis
and the full ``PreprocessingRecord``.  A non-NMR input returns
``status="skipped"`` with a named reason and no matrix -- the input is
passed through unchanged, never guessed at.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.metabolomics import PreprocessingRecord

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .pipeline import PreprocessParams, SkipPreprocessing, preprocess_experiments

router = APIRouter()


class PreprocessParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lb_hz: float = 1.0
    zero_fill_factor: int = 2
    spectral_mode: str = "absorption"
    reference_ppm: float = 0.0
    reference_window_ppm: float = 0.25
    baseline_lambda: float = 1e5
    baseline_p: float = 0.01
    bucket_width_ppm: float = 0.04
    excised_regions: list[tuple[float, float]] = Field(default_factory=list)
    solvent: str | None = None


class PreprocessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_dirs: list[str] = Field(..., min_length=1)


class PreprocessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "applied" | "skipped"
    reason: str | None = None
    matrix: list[list[float]] | None = None
    ppm: list[float] | None = None
    sample_ids: list[str] | None = None
    record: PreprocessingRecord | None = None
    notes: list[str] = Field(default_factory=list)


@router.post("/nmr-preproc/preprocess", response_model=PreprocessResponse)
def preprocess(request: PreprocessRequest) -> PreprocessResponse:
    params = PreprocessParamsModel()
    try:
        result = preprocess_experiments(
            request.experiment_dirs,
            PreprocessParams(
                lb_hz=params.lb_hz,
                zero_fill_factor=params.zero_fill_factor,
                spectral_mode=params.spectral_mode,
                reference_ppm=params.reference_ppm,
                reference_window_ppm=params.reference_window_ppm,
                baseline_lambda=params.baseline_lambda,
                baseline_p=params.baseline_p,
                bucket_width_ppm=params.bucket_width_ppm,
                excised_regions=params.excised_regions,
                solvent=params.solvent,
            ),
        )
    except SkipPreprocessing as skip:
        return PreprocessResponse(status="skipped", reason=skip.reason)
    return PreprocessResponse(
        status="applied",
        matrix=[[float(v) for v in row] for row in result.matrix],
        ppm=[float(v) for v in result.ppm],
        sample_ids=result.sample_ids,
        record=result.record,
        notes=result.notes,
    )


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed (ADR-0039)."""
    application = FastAPI(
        title="PhytoCompanion NMR Preprocessing Service",
        version=__version__,
        description="Apodisation, zero-fill, FFT, phase, referencing, "
        "asLS baseline, alignment, PQN, bucketing (ADR-0068).",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    @application.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "nmr_preproc",
            "version": __version__,
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
