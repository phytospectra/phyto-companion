"""FastAPI entrypoint for the NMR preprocessing service (ADR-0068, 0073, 0074).

The preprocess endpoint runs the documented in-house pipeline over
Bruker-style experiment directories and returns the bucketed matrix,
the ppm axis and the full ``PreprocessingRecord``.  The qNMR endpoints
(ADR-0073/0074) deconvolve regions, integrate them, run
internal-standard quantification, and evaluate custom equations.  A
non-usable input returns ``status="skipped"`` with a named reason --
the input is passed through unchanged, never guessed at.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.metabolomics import PreprocessingRecord

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .deconvolution import (
    DeconvolutionParams,
    SkipDeconvolution,
    deconvolve,
)
from .equations import (
    CustomEquation,
    EquationError,
    SpecCheck,
    check_specs,
    evaluate_equation,
)
from .pipeline import (
    PreprocessParams,
    SkipPreprocessing,
    apodise_exponential,
    apply_phase,
    auto_phase,
    fft_spectrum,
    load_experiment,
    preprocess_experiments,
    zero_fill,
)
from .quantify import (
    AnalyteSpec,
    InternalStandard,
    SkipQuantification,
    integrate_region,
    qnmr_calculate,
)

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


# ---------------------------------------------------------------------
# qNMR endpoints (ADR-0073/0074)
# ---------------------------------------------------------------------

class SpectrumInput(BaseModel):
    """A spectrum to analyse: inline arrays or a Bruker experiment dir.

    Raw-FID directories are put through the documented minimal chain
    (apodisation 1 Hz -> zero-fill x2 -> FFT -> auto phase -> real) so
    fitting always sees a real spectrum; processed ``pdata/1`` data is
    used as the vendor left it.  Inline arrays are used verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_dir: str | None = None
    ppm: list[float] | None = None
    intensity: list[float] | None = None


class DeconvolutionParamsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "pseudovoigt"
    sf_mhz: float = 500.0
    snr_threshold: float = 10.0
    fwhm_bounds_hz: tuple[float, float] = (0.5, 80.0)
    max_components: int = 12
    baseline_degree: int = 1
    maxfev_per_component: int = 4000


class DeconvolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrum: SpectrumInput
    regions: list[tuple[float, float]] = Field(..., min_length=1)
    params: DeconvolutionParamsModel = Field(default_factory=DeconvolutionParamsModel)
    extra_seeds_ppm: list[float] = Field(default_factory=list)


class DeconvolveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str  # "applied" | "skipped"
    reason: str | None = None
    fits: list[dict[str, Any]] | None = None
    notes: list[str] = Field(default_factory=list)
    model_id: str | None = None


class IntegrateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectrum: SpectrumInput
    regions: list[tuple[float, float]] = Field(..., min_length=1)
    baseline: str = "linear"


class IntegrateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None
    integrals: list[dict[str, Any]] | None = None


class InternalStandardModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    n_protons: int = Field(..., gt=0)
    # integral is deliberately NOT gt-constrained: a non-physical value
    # must reach the service and come back as a NAMED skip (200), not a
    # faceless 422 -- the house skip convention (ADR-0073).
    integral: float
    moles_nmol: float | None = None
    mass_mg: float | None = None
    molecular_weight: float | None = None
    purity_fraction: float = 1.0


class AnalyteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    n_protons: int = Field(..., gt=0)
    integral: float  # named-skip contract, see InternalStandardModel
    molecular_weight: float | None = None


class QuantifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    standard: InternalStandardModel
    analytes: list[AnalyteModel] = Field(..., min_length=1)
    sample_mass_mg: float | None = None


class QuantifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None
    result: dict[str, Any] | None = None


class EquationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "adhoc"
    expression: str = Field(..., min_length=1)
    variables: dict[str, str] = Field(default_factory=dict)
    description: str = ""


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    expression: str = Field(..., min_length=1)
    level: str = "warning"
    description: str = ""


class EquationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equation: EquationModel | None = None
    specs: list[SpecModel] = Field(default_factory=list)
    values: dict[str, float] = Field(..., min_length=1)


class EquationsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    reason: str | None = None
    result: dict[str, Any] | None = None
    spec_results: list[dict[str, Any]] | None = None


def _spectrum_arrays(spec: SpectrumInput) -> tuple[np.ndarray, np.ndarray, float]:
    """Resolve SpectrumInput to (ppm, intensity, sf_mhz) or raise a named skip."""
    if spec.experiment_dir is not None:
        data, meta = load_experiment(spec.experiment_dir)
        sf_mhz = float(meta.get("sf_mhz") or 0.0)
        if meta.get("source") == "raw fid":
            fid = apodise_exponential(data, 1.0, float(meta["sw_hz"]))
            fid = zero_fill(fid, 2)
            spectrum = apply_phase_of(fft_spectrum(fid))
            n = spectrum.size
            sw_ppm = float(meta["sw_hz"]) / sf_mhz if sf_mhz else 0.0
            if sw_ppm <= 0:
                raise SkipPreprocessing(
                    "raw-FID directory carries no spectrometer frequency; "
                    "supply processed pdata or inline arrays"
                )
            ppm = float(meta["offset_ppm"]) - np.arange(n) * (sw_ppm / n)
        else:
            spectrum = np.asarray(meta["spectrum"], dtype=float)
            ppm = np.asarray(meta["ppm"], dtype=float)
        return ppm, spectrum, sf_mhz or 500.0
    if spec.ppm is None or spec.intensity is None:
        raise SkipPreprocessing(
            "spectrum needs either experiment_dir or both ppm and intensity"
        )
    if len(spec.ppm) != len(spec.intensity) or len(spec.ppm) < 8:
        raise SkipPreprocessing(
            f"inline spectrum axes disagree or are too short "
            f"({len(spec.ppm)} ppm / {len(spec.intensity)} intensity; >= 8 points)"
        )
    return (
        np.asarray(spec.ppm, dtype=float),
        np.asarray(spec.intensity, dtype=float),
        500.0,
    )


def apply_phase_of(spectrum: np.ndarray) -> np.ndarray:
    """Auto-phase a complex spectrum and return its real part."""
    ph0, ph1 = auto_phase(spectrum)
    return apply_phase(spectrum, ph0, ph1)


@router.post("/nmr-preproc/deconvolve", response_model=DeconvolveResponse)
def deconvolve_endpoint(request: DeconvolveRequest) -> DeconvolveResponse:
    params = DeconvolutionParams(
        model=request.params.model,
        sf_mhz=request.params.sf_mhz,
        snr_threshold=request.params.snr_threshold,
        fwhm_bounds_hz=request.params.fwhm_bounds_hz,
        max_components=request.params.max_components,
        baseline_degree=request.params.baseline_degree,
        maxfev_per_component=request.params.maxfev_per_component,
    )
    try:
        ppm, intensity, sf_mhz = _spectrum_arrays(request.spectrum)
        if request.params.sf_mhz == 500.0 and sf_mhz != 500.0:
            params.sf_mhz = sf_mhz  # directory carries the true frequency
        result = deconvolve(
            ppm, intensity, request.regions, params,
            extra_seeds_ppm=request.extra_seeds_ppm,
        )
    except (SkipPreprocessing, SkipDeconvolution) as skip:
        return DeconvolveResponse(status="skipped", reason=skip.reason)
    return DeconvolveResponse(
        status="applied",
        fits=[asdict(f) for f in result.fits],
        notes=result.notes,
        model_id=result.model_id,
    )


@router.post("/nmr-preproc/integrate", response_model=IntegrateResponse)
def integrate_endpoint(request: IntegrateRequest) -> IntegrateResponse:
    try:
        ppm, intensity, _sf = _spectrum_arrays(request.spectrum)
        integrals = [
            asdict(integrate_region(ppm, intensity, lo, hi, baseline=request.baseline))
            for lo, hi in request.regions
        ]
    except (SkipPreprocessing, SkipQuantification) as skip:
        return IntegrateResponse(status="skipped", reason=skip.reason)
    return IntegrateResponse(status="applied", integrals=integrals)


@router.post("/nmr-preproc/quantify", response_model=QuantifyResponse)
def quantify_endpoint(request: QuantifyRequest) -> QuantifyResponse:
    standard = InternalStandard(
        name=request.standard.name,
        n_protons=request.standard.n_protons,
        integral=request.standard.integral,
        moles_nmol=request.standard.moles_nmol,
        mass_mg=request.standard.mass_mg,
        molecular_weight=request.standard.molecular_weight,
        purity_fraction=request.standard.purity_fraction,
    )
    try:
        result = qnmr_calculate(
            standard,
            [
                AnalyteSpec(
                    name=a.name,
                    n_protons=a.n_protons,
                    integral=a.integral,
                    molecular_weight=a.molecular_weight,
                )
                for a in request.analytes
            ],
            sample_mass_mg=request.sample_mass_mg,
        )
    except SkipQuantification as skip:
        return QuantifyResponse(status="skipped", reason=skip.reason)
    return QuantifyResponse(status="applied", result=asdict(result))


@router.post("/nmr-preproc/equations/evaluate", response_model=EquationsResponse)
def equations_endpoint(request: EquationsRequest) -> EquationsResponse:
    if request.equation is None and not request.specs:
        return EquationsResponse(
            status="skipped", reason="no equation or specs supplied"
        )
    try:
        result = None
        if request.equation is not None:
            result = asdict(
                evaluate_equation(
                    CustomEquation(
                        name=request.equation.name,
                        expression=request.equation.expression,
                        variables=request.equation.variables,
                        description=request.equation.description,
                    ),
                    request.values,
                )
            )
        spec_results = [
            asdict(s)
            for s in check_specs(
                [
                    SpecCheck(
                        name=s.name,
                        expression=s.expression,
                        level=s.level,
                        description=s.description,
                    )
                    for s in request.specs
                ],
                request.values,
            )
        ] or None
    except EquationError as exc:
        return EquationsResponse(status="skipped", reason=exc.reason)
    return EquationsResponse(
        status="applied", result=result, spec_results=spec_results
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
