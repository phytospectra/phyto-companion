"""Spectral deconvolution and line-shape fitting (ADR-0073).

Companion to ``pipeline.py``: once a spectrum is processed, extract
mixtures present overlapped multiplets that prominence-based peak
picking cannot separate.  This module fits each user-nominated region
with a sum of line shapes (pseudo-Voigt by default; Lorentzian and
Gaussian selectable) on a low-order local baseline, using seeded
non-linear least squares (``scipy.optimize.curve_fit`` -- the house
fitting precedent, cf. ``metabolomics/app/differential_analysis.py``).

Contract, in the ADR-0068 spirit:

* fitting is per-region and every region produces a ``RegionFit`` with
  an explicit status: ``fitted``, ``failed`` (fit did not converge --
  recorded with the reason, never raised) or ``skipped`` (no seeds
  above the SNR threshold);
* linewidths are quoted in Hz and converted through the spectrometer
  frequency, because a ppm linewidth is meaningless without it;
* component areas are ANALYTIC (height x width closed form), so an
  overlapped doublet-of-doublets yields per-component integrals that
  plain region integration cannot give -- the quantitative payoff the
  ``quantify.py`` module builds on;
* no new dependency: numpy + scipy only, exactly like the rest of the
  in-house NMR path (lmfit was considered and rejected, ADR-0073).

Known limitations, stated because they are the weakest links: (i) the
seeder is prominence-based, so peaks buried entirely under a stronger
neighbour need a manual seed via ``extra_seeds_ppm``; (ii) one shared
baseline is fitted per region -- a curved baseline inside a narrow
region is approximated by a straight line; (iii) multiplet fine
structure is fitted as separate components, not as J-coupled groups.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit

__all__ = [
    "SkipDeconvolution",
    "DeconvolutionParams",
    "PickedPeak",
    "FittedComponent",
    "RegionFit",
    "DeconvolutionResult",
    "LINE_SHAPE_MODELS",
    "noise_sigma_1d",
    "pick_peaks",
    "lorentzian",
    "gaussian",
    "pseudovoigt",
    "fit_region",
    "deconvolve",
    "evaluate_region_fit",
]

#: Provenance id stamped on every result (overlay/report contracts).
DECONVOLUTION_MODEL_ID = "deconv-0.1.0"

#: Selectable line-shape families.  ``pseudovoigt`` is the default
#: because processed 1H lines are neither pure Lorentzian (apodisation
#: adds Gaussian character) nor pure Gaussian.
LINE_SHAPE_MODELS = ("pseudovoigt", "lorentzian", "gaussian")


class SkipDeconvolution(Exception):
    """Named skip: the input cannot be deconvolved, with a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class DeconvolutionParams:
    """Region-fit parameters (all of them recorded in the result)."""

    model: str = "pseudovoigt"  # one of LINE_SHAPE_MODELS
    sf_mhz: float = 500.0  # Hz <-> ppm conversion (required for bounds)
    snr_threshold: float = 10.0  # seed prominence / noise sigma
    fwhm_bounds_hz: tuple[float, float] = (0.5, 80.0)
    max_components: int = 12  # refuse absurd seed explosions
    baseline_degree: int = 1  # 0 = constant, 1 = linear (local)
    maxfev_per_component: int = 4000


@dataclass
class PickedPeak:
    ppm: float
    height: float
    prominence: float


@dataclass
class FittedComponent:
    center_ppm: float
    fwhm_hz: float
    height: float
    area: float  # analytic integral, intensity*ppm units
    model: str
    eta: float | None = None  # pseudo-Voigt mixing fraction


@dataclass
class RegionFit:
    lo_ppm: float
    hi_ppm: float
    status: str  # "fitted" | "failed" | "skipped"
    reason: str | None = None
    components: list[FittedComponent] = field(default_factory=list)
    r_squared: float | None = None
    residual_rmse: float | None = None
    n_points: int = 0
    baseline: list[float] = field(default_factory=list)  # poly coeffs, low->hi
    model_id: str = DECONVOLUTION_MODEL_ID


@dataclass
class DeconvolutionResult:
    fits: list[RegionFit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model_id: str = DECONVOLUTION_MODEL_ID
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------
# noise estimate + seeding
# ---------------------------------------------------------------------

def noise_sigma_1d(intensity: np.ndarray) -> float:
    """Robust noise sigma from first differences (MAD-based).

    ``diff`` of white noise has sigma*sqrt(2) spread and kills any
    smooth baseline/signal, so sigma_hat = MAD(diff) * 1.4826 / sqrt(2)
    is the honest noise estimate for a processed 1D spectrum.
    """
    y = np.asarray(intensity, dtype=float)
    if y.size < 3:
        return 0.0
    return float(np.median(np.abs(np.diff(y))) * 1.4826 / math.sqrt(2.0))


def pick_peaks(
    ppm: np.ndarray,
    intensity: np.ndarray,
    *,
    snr_threshold: float = 10.0,
    sf_mhz: float = 500.0,
    max_peaks: int = 64,
) -> list[PickedPeak]:
    """Prominence-gated 1D peak picker (production gap ADR-0073 closes).

    Threshold basis: asserted, provisional.  ``snr_threshold=10``
    follows the convention that a seedable peak stands one order of
    magnitude above the noise; tighten on high-S/N processed data.
    """
    from scipy.signal import find_peaks  # scipy is a declared dependency

    x = np.asarray(ppm, dtype=float)
    y = np.asarray(intensity, dtype=float)
    sigma = noise_sigma_1d(y)
    if sigma <= 0.0:
        sigma = float(np.max(np.abs(y))) * 1e-6 or 1e-12
    min_prominence = snr_threshold * sigma
    indices, props = find_peaks(y, prominence=min_prominence, height=min_prominence)
    prominences = np.asarray(props["prominences"], dtype=float)
    heights = np.asarray(props["peak_heights"], dtype=float)
    order = np.argsort(-prominences)[:max_peaks]
    peaks: list[PickedPeak] = []
    for pos in order:  # pos indexes the props arrays; indices[pos] the sample
        sample = int(indices[pos])
        peaks.append(
            PickedPeak(
                ppm=float(x[sample]),
                height=float(heights[pos]),
                prominence=float(prominences[pos]),
            )
        )
    return peaks


# ---------------------------------------------------------------------
# line shapes (height A at center c, FWHM gamma -- areas analytic)
# ---------------------------------------------------------------------

def lorentzian(x: np.ndarray, height: float, center: float, fwhm: float) -> np.ndarray:
    gamma2 = (fwhm / 2.0) ** 2
    return height * gamma2 / ((x - center) ** 2 + gamma2)


def gaussian(x: np.ndarray, height: float, center: float, fwhm: float) -> np.ndarray:
    return height * np.exp(-4.0 * math.log(2.0) * ((x - center) / fwhm) ** 2)


def pseudovoigt(
    x: np.ndarray, height: float, center: float, fwhm: float, eta: float
) -> np.ndarray:
    return eta * lorentzian(x, height, center, fwhm) + (1.0 - eta) * gaussian(
        x, height, center, fwhm
    )


def component_area(height: float, fwhm: float, model: str, eta: float) -> float:
    """Analytic area under one component (intensity x ppm units)."""
    if model == "lorentzian":
        return math.pi * height * fwhm / 2.0
    if model == "gaussian":
        return height * fwhm * math.sqrt(math.pi / (4.0 * math.log(2.0)))
    # pseudo-Voigt with shared height and width: linear mix of the two areas
    lorentz_area = math.pi * height * fwhm / 2.0
    gauss_area = height * fwhm * math.sqrt(math.pi / (4.0 * math.log(2.0)))
    return eta * lorentz_area + (1.0 - eta) * gauss_area


# ---------------------------------------------------------------------
# region fitting
# ---------------------------------------------------------------------

def _region_model(x: np.ndarray, *params: float, model: str, baseline_degree: int,
                  x_mid: float, n_components: int = 0) -> np.ndarray:
    """Sum of baseline polynomial + line shapes.  Parameter layout:
    ``[baseline coeffs (ascending powers)] + per-component blocks``."""
    del n_components  # component count is implied by the parameter count
    total = np.zeros_like(x, dtype=float)
    idx = 0
    for coeff in params[: baseline_degree + 1]:
        total += coeff * ((x - x_mid) ** idx)
        idx += 1
    rest = params[baseline_degree + 1 :]
    block = 3 if model in ("lorentzian", "gaussian") else 4
    for start in range(0, len(rest), block):
        chunk = rest[start : start + block]
        height, center, fwhm = chunk[0], chunk[1], chunk[2]
        if model == "pseudovoigt":
            total = total + pseudovoigt(x, height, center, fwhm, chunk[3])
        elif model == "lorentzian":
            total = total + lorentzian(x, height, center, fwhm)
        else:
            total = total + gaussian(x, height, center, fwhm)
    return total


def fit_region(
    ppm: np.ndarray,
    intensity: np.ndarray,
    lo_ppm: float,
    hi_ppm: float,
    params: DeconvolutionParams | None = None,
    *,
    extra_seeds_ppm: list[float] | None = None,
) -> RegionFit:
    """Fit one region with seeded line shapes + local baseline.

    Failures (no seeds, non-convergence, degenerate window) come back
    as ``status="failed"/"skipped"`` RegionFit objects -- never as
    exceptions, so a batch over many regions cannot lose the good
    ones to one bad window.
    """
    params = params or DeconvolutionParams()
    if params.model not in LINE_SHAPE_MODELS:
        raise SkipDeconvolution(
            f"unknown line-shape model {params.model!r}; "
            f"known: {list(LINE_SHAPE_MODELS)}"
        )
    lo, hi = min(lo_ppm, hi_ppm), max(lo_ppm, hi_ppm)
    fit = RegionFit(lo_ppm=lo, hi_ppm=hi, status="skipped")

    x = np.asarray(ppm, dtype=float)
    y = np.asarray(intensity, dtype=float)
    mask = (x >= lo) & (x <= hi)
    fit.n_points = int(mask.sum())
    if fit.n_points < 8:
        fit.reason = (
            f"region [{lo}, {hi}] ppm has {fit.n_points} points; "
            "at least 8 required for fitting"
        )
        return fit

    xr, yr = x[mask], y[mask]
    seeds = [p for p in pick_peaks(
        xr, yr,
        snr_threshold=params.snr_threshold,
        sf_mhz=params.sf_mhz,
        max_peaks=params.max_components,
    ) if lo <= p.ppm <= hi]
    if extra_seeds_ppm:
        for seed_ppm in extra_seeds_ppm:
            if lo <= seed_ppm <= hi:
                nearest = int(np.argmin(np.abs(xr - seed_ppm)))
                seeds.append(PickedPeak(
                    ppm=float(xr[nearest]),
                    height=float(yr[nearest]),
                    prominence=float(abs(yr[nearest])),
                ))
    seeds.sort(key=lambda p: -p.prominence)
    seeds = seeds[: params.max_components]
    if not seeds:
        fit.reason = (
            f"no seed peaks above {params.snr_threshold:.0f}x noise sigma "
            f"in region [{lo}, {hi}] ppm"
        )
        return fit

    hz_per_ppm = params.sf_mhz
    fwhm_lo = params.fwhm_bounds_hz[0] / hz_per_ppm
    fwhm_hi = params.fwhm_bounds_hz[1] / hz_per_ppm
    x_mid = float(np.mean(xr))
    baseline_estimate = float(np.percentile(yr, 5))

    n_base = params.baseline_degree + 1
    block = 4 if params.model == "pseudovoigt" else 3
    p0: list[float] = [baseline_estimate] + [0.0] * (n_base - 1)
    lower: list[float] = [-np.inf] * n_base
    upper: list[float] = [np.inf] * n_base
    default_fwhm = min(max(3.0 / hz_per_ppm, fwhm_lo), fwhm_hi)
    for seed in seeds:
        height0 = max(seed.height - baseline_estimate, 1e-12)
        p0 += [height0, seed.ppm, default_fwhm]
        lower += [0.0, lo, fwhm_lo]
        upper += [np.inf, hi, fwhm_hi]
        if params.model == "pseudovoigt":
            p0.append(0.5)
            lower.append(0.0)
            upper.append(1.0)

    def model_on(xr_: np.ndarray, *p: float) -> np.ndarray:
        return _region_model(
            xr_, *p, model=params.model, n_components=len(seeds),
            baseline_degree=params.baseline_degree, x_mid=x_mid,
        )

    try:
        popt, _ = curve_fit(
            model_on,
            xr,
            yr,
            p0=p0,
            bounds=(lower, upper),
            maxfev=params.maxfev_per_component * len(seeds),
        )
    except (RuntimeError, ValueError) as exc:  # non-convergence is data, not a crash
        fit.status = "failed"
        fit.reason = f"least-squares fit did not converge: {exc}"
        return fit

    residuals = yr - model_on(xr, *popt)
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((yr - np.mean(yr)) ** 2))
    fit.status = "fitted"
    fit.r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    fit.residual_rmse = float(np.sqrt(ss_res / yr.size))
    fit.baseline = [float(v) for v in popt[:n_base]]

    rest = popt[n_base:]
    for start in range(0, len(rest), block):
        chunk = rest[start : start + block]
        height, center, fwhm_ppm = float(chunk[0]), float(chunk[1]), float(chunk[2])
        eta = float(chunk[3]) if params.model == "pseudovoigt" else None
        fit.components.append(
            FittedComponent(
                center_ppm=center,
                fwhm_hz=fwhm_ppm * hz_per_ppm,
                height=height,
                area=component_area(height, fwhm_ppm, params.model, eta or 0.0),
                model=params.model,
                eta=eta,
            )
        )
    fit.components.sort(key=lambda c: -c.area)
    return fit


def deconvolve(
    ppm: np.ndarray,
    intensity: np.ndarray,
    regions: list[tuple[float, float]],
    params: DeconvolutionParams | None = None,
    *,
    extra_seeds_ppm: list[float] | None = None,
) -> DeconvolutionResult:
    """Batch ``fit_region`` over regions; validates input loudly."""
    params = params or DeconvolutionParams()
    x = np.asarray(ppm, dtype=float)
    y = np.asarray(intensity, dtype=float)
    if x.size != y.size or x.size == 0:
        raise SkipDeconvolution(
            f"ppm ({x.size}) and intensity ({y.size}) axes disagree or are empty"
        )
    if not regions:
        raise SkipDeconvolution("no regions supplied; nothing to deconvolve")
    result = DeconvolutionResult(
        params={
            "model": params.model,
            "sf_mhz": params.sf_mhz,
            "snr_threshold": params.snr_threshold,
            "fwhm_bounds_hz": list(params.fwhm_bounds_hz),
            "max_components": params.max_components,
            "baseline_degree": params.baseline_degree,
        }
    )
    for lo, hi in regions:
        result.fits.append(
            fit_region(x, y, lo, hi, params, extra_seeds_ppm=extra_seeds_ppm)
        )
    n_failed = sum(1 for f in result.fits if f.status == "failed")
    n_skipped = sum(1 for f in result.fits if f.status == "skipped")
    if n_failed:
        result.notes.append(f"{n_failed} region fit(s) failed to converge")
    if n_skipped:
        result.notes.append(f"{n_skipped} region(s) skipped: no seeds above SNR")
    return result


def evaluate_region_fit(
    fit: RegionFit, x_ppm: np.ndarray, params: DeconvolutionParams | None = None
) -> tuple[np.ndarray, list[np.ndarray]]:
    """Evaluate a fitted region: (total model, per-component curves).

    Used by the report overlay so a reader SEES the decomposition an
    overlapped region was resolved into, not just its numbers.
    """
    params = params or DeconvolutionParams()
    x = np.asarray(x_ppm, dtype=float)
    components: list[np.ndarray] = []
    for comp in fit.components:
        fwhm_ppm = comp.fwhm_hz / params.sf_mhz
        if comp.model == "pseudovoigt":
            components.append(
                pseudovoigt(x, comp.height, comp.center_ppm, fwhm_ppm, comp.eta or 0.5)
            )
        elif comp.model == "lorentzian":
            components.append(lorentzian(x, comp.height, comp.center_ppm, fwhm_ppm))
        else:
            components.append(gaussian(x, comp.height, comp.center_ppm, fwhm_ppm))
    if not fit.baseline:
        return (np.sum(components, axis=0) if components else np.zeros_like(x)), components
    x_mid = 0.5 * (fit.lo_ppm + fit.hi_ppm)
    baseline = np.zeros_like(x)
    for power, coeff in enumerate(fit.baseline):
        baseline = baseline + coeff * ((x - x_mid) ** power)
    total = baseline + (np.sum(components, axis=0) if components else 0.0)
    return total, components
