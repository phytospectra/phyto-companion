"""qNMR region integration and internal-standard quantification (ADR-0073).

The quantitative layer over ``deconvolution.py`` and ``pipeline.py``
outputs.  Two integration routes, both recorded with their method:

* **Region integration** -- trapezoid over a ppm window with an
  optional local linear baseline anchored on the region edges; the
  honest choice for isolated signals.
* **Fit-based integration** -- sum of ANALYTIC component areas from a
  ``RegionFit``; the only route that can integrate one component of
  an overlapped cluster (extract mixtures are why it exists).

Absolute quantification follows the classical internal-standard qNMR
equation

    n_analyte = (I_analyte / N_analyte) / (I_std / N_std) * n_std

with every result carrying its substituted equation as audit text, so
a reviewer can re-derive each number by hand.  The scalar
``calibration_factor`` branch in ``metabolomics/app/normalization.py``
(``method="qnmr"``) is the downstream consumer of factors computed
here; this module is the "real qNMR service" that comment anticipated.

Contract: non-physical inputs (zero/negative integrals, zero proton
counts, unknown standard amounts) raise ``SkipQuantification`` with a
named reason -- never a silent zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .deconvolution import RegionFit

__all__ = [
    "SkipQuantification",
    "RegionIntegral",
    "InternalStandard",
    "AnalyteSpec",
    "AnalyteAmount",
    "QuantificationResult",
    "integrate_region",
    "integral_from_fit",
    "qnmr_calculate",
]

QUANTIFICATION_MODEL_ID = "qnmr-0.1.0"


class SkipQuantification(Exception):
    """Named skip: the quantification inputs are unusable, with a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class RegionIntegral:
    lo_ppm: float
    hi_ppm: float
    area: float  # intensity x ppm
    baseline_method: str  # "none" | "edge-linear"
    n_points: int
    model_id: str = QUANTIFICATION_MODEL_ID


@dataclass
class InternalStandard:
    """The calibrated reference signal everything is measured against."""

    name: str
    n_protons: int
    integral: float  # intensity x ppm (RegionIntegral.area or fit sum)
    # amount, either direct or via mass + molar mass + purity fraction
    moles_nmol: float | None = None
    mass_mg: float | None = None
    molecular_weight: float | None = None  # g/mol
    purity_fraction: float = 1.0

    def standard_moles_nmol(self) -> float:
        if self.moles_nmol is not None:
            return float(self.moles_nmol)
        if self.mass_mg is not None and self.molecular_weight:
            return (
                self.mass_mg
                / 1000.0
                / self.molecular_weight
                * self.purity_fraction
                * 1e9
            )
        raise SkipQuantification(
            f"internal standard {self.name!r} has no usable amount: supply "
            "moles_nmol or mass_mg + molecular_weight"
        )


@dataclass
class AnalyteSpec:
    name: str
    n_protons: int
    integral: float
    molecular_weight: float | None = None  # g/mol, enables mass outputs


@dataclass
class AnalyteAmount:
    name: str
    moles_nmol: float
    mass_mg: float | None = None
    wt_percent: float | None = None  # of sample_mass_mg, when supplied
    equation: str = ""  # substituted audit text


@dataclass
class QuantificationResult:
    standard_name: str
    standard_moles_nmol: float
    analytes: list[AnalyteAmount] = field(default_factory=list)
    sample_mass_mg: float | None = None
    model_id: str = QUANTIFICATION_MODEL_ID


# ---------------------------------------------------------------------
# integration routes
# ---------------------------------------------------------------------

def integrate_region(
    ppm: np.ndarray,
    intensity: np.ndarray,
    lo_ppm: float,
    hi_ppm: float,
    *,
    baseline: str = "linear",
    edge_fraction: float = 0.05,
) -> RegionIntegral:
    """Trapezoid integration over [lo, hi] ppm with edge-anchored baseline.

    ``baseline="linear"`` anchors a straight line on the mean of the
    outer ``edge_fraction`` of points at each edge -- the local-baseline
    convention qNMR practice uses when a global baseline is not
    trustworthy inside a busy extract spectrum.  ``baseline="none"``
    integrates the raw trace.
    """
    if baseline not in ("linear", "none"):
        raise SkipQuantification(
            f"unknown baseline option {baseline!r}; known: ['linear', 'none']"
        )
    x = np.asarray(ppm, dtype=float)
    y = np.asarray(intensity, dtype=float)
    lo, hi = min(lo_ppm, hi_ppm), max(lo_ppm, hi_ppm)
    mask = (x >= lo) & (x <= hi)
    n_points = int(mask.sum())
    if n_points < 4:
        raise SkipQuantification(
            f"region [{lo}, {hi}] ppm has {n_points} points; at least 4 required"
        )
    order = np.argsort(x[mask])
    xr, yr = x[mask][order], y[mask][order]

    if baseline == "linear":
        n_edge = max(2, int(np.ceil(n_points * edge_fraction)))
        left = float(np.mean(yr[:n_edge]))
        right = float(np.mean(yr[-n_edge:]))
        weight = (xr - xr[0]) / max(xr[-1] - xr[0], 1e-12)
        yr = yr - (left + (right - left) * weight)

    area = float(np.trapezoid(yr, xr))
    return RegionIntegral(
        lo_ppm=lo, hi_ppm=hi, area=area, baseline_method=baseline, n_points=n_points
    )


def integral_from_fit(fit: RegionFit, component_indices: list[int] | None = None) -> float:
    """Sum of analytic component areas from a fitted region.

    ``component_indices`` selects a subset (e.g. one component of an
    overlapped pair); default is all components.  A failed/skipped fit
    is a named error, never a zero -- a number you cannot trust must
    not enter a quantification chain silently.
    """
    if fit.status != "fitted" or not fit.components:
        raise SkipQuantification(
            f"region [{fit.lo_ppm}, {fit.hi_ppm}] ppm has no usable fit "
            f"(status={fit.status!r}, reason={fit.reason!r})"
        )
    components = fit.components
    if component_indices is not None:
        try:
            components = [fit.components[i] for i in component_indices]
        except IndexError as exc:
            raise SkipQuantification(
                f"component index out of range for region "
                f"[{fit.lo_ppm}, {fit.hi_ppm}] ppm: {exc}"
            ) from exc
    return float(sum(c.area for c in components))


# ---------------------------------------------------------------------
# internal-standard qNMR
# ---------------------------------------------------------------------

def qnmr_calculate(
    standard: InternalStandard,
    analytes: list[AnalyteSpec],
    *,
    sample_mass_mg: float | None = None,
) -> QuantificationResult:
    """Classical internal-standard qNMR for each analyte.

    Every analyte result records the substituted equation as text; a
    non-physical input (non-positive integral or proton count, no
    analytes) raises ``SkipQuantification`` with the named reason.
    """
    if not analytes:
        raise SkipQuantification("no analytes supplied; nothing to quantify")
    n_std_mol = standard.standard_moles_nmol()
    if standard.n_protons <= 0 or standard.integral <= 0:
        raise SkipQuantification(
            f"internal standard {standard.name!r} needs positive n_protons "
            "and integral"
        )
    result = QuantificationResult(
        standard_name=standard.name,
        standard_moles_nmol=n_std_mol,
        sample_mass_mg=sample_mass_mg,
    )
    per_proton_std = standard.integral / standard.n_protons
    for analyte in analytes:
        if analyte.n_protons <= 0 or analyte.integral <= 0:
            raise SkipQuantification(
                f"analyte {analyte.name!r} needs positive n_protons and integral"
            )
        moles = (analyte.integral / analyte.n_protons) / per_proton_std * n_std_mol
        equation = (
            f"n({analyte.name}) = (I/N)_analyte / (I/N)_std * n_std = "
            f"({analyte.integral:.4g}/{analyte.n_protons}) / "
            f"({standard.integral:.4g}/{standard.n_protons}) * "
            f"{n_std_mol:.4g} nmol = {moles:.4g} nmol"
        )
        mass = (
            # nmol -> mol (1e-9), * g/mol = g, -> mg (1e3): net 1e-6
            analyte.molecular_weight * moles * 1e-6
            if analyte.molecular_weight
            else None
        )
        wt_percent = (
            mass / sample_mass_mg * 100.0 if mass is not None and sample_mass_mg else None
        )
        result.analytes.append(
            AnalyteAmount(
                name=analyte.name,
                moles_nmol=moles,
                mass_mg=mass,
                wt_percent=wt_percent,
                equation=equation,
            )
        )
    return result
