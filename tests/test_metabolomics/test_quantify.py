"""qNMR region integration and internal-standard quantification (ADR-0073).

Ground truth: synthetic spectra with analytically known areas, and a
synthetic standard/analyte pair whose mole ratio is fixed by
construction.  Tests assert recovery of the known amounts and the
named-skip contract for non-physical inputs.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nmr_preproc.deconvolution import DeconvolutionParams, fit_region
from nmr_preproc.quantify import (
    AnalyteSpec,
    InternalStandard,
    QuantificationResult,
    RegionIntegral,
    SkipQuantification,
    integrate_region,
    integral_from_fit,
    qnmr_calculate,
)

RNG = np.random.default_rng(11)


def _peak_on_grid(
    center: float,
    height: float,
    fwhm_ppm: float,
    ppm: np.ndarray,
    baseline: tuple[float, float] = (0.0, 0.0),
) -> np.ndarray:
    gamma2 = (fwhm_ppm / 2.0) ** 2
    return (
        baseline[0]
        + baseline[1] * (ppm - ppm[0])
        + height * gamma2 / ((ppm - center) ** 2 + gamma2)
    )


class TestIntegrateRegion:
    def test_lorentzian_area_matches_analytic(self) -> None:
        ppm = np.linspace(7.5, 6.5, 20001)
        y = _peak_on_grid(7.0, 1.0, 0.004, ppm)
        # wide window: a Lorentzian keeps ~1.3% of its area beyond +-0.1 ppm
        integral = integrate_region(ppm, y, 6.5, 7.5, baseline="none")
        truth = math.pi * 1.0 * 0.004 / 2.0
        assert integral.area == pytest.approx(truth, rel=0.005)
        assert integral.baseline_method == "none"
        assert integral.n_points > 1000

    def test_linear_baseline_removes_offset_and_slope(self) -> None:
        ppm = np.linspace(7.5, 6.5, 20001)
        y = _peak_on_grid(7.0, 1.0, 0.004, ppm, baseline=(0.5, 0.4))
        truth = math.pi * 1.0 * 0.004 / 2.0
        without = integrate_region(ppm, y, 6.8, 7.2, baseline="none")
        with_base = integrate_region(ppm, y, 6.8, 7.2, baseline="linear")
        assert abs(with_base.area - truth) < abs(without.area - truth)
        assert with_base.area == pytest.approx(truth, rel=0.02)

    def test_too_few_points_raises_named_skip(self) -> None:
        ppm = np.linspace(7.5, 6.5, 3)
        with pytest.raises(SkipQuantification, match="at least 4"):
            integrate_region(ppm, np.ones(3), 7.0, 7.2)

    def test_unknown_baseline_option_raises(self) -> None:
        ppm = np.linspace(7.5, 6.5, 100)
        with pytest.raises(SkipQuantification, match="unknown baseline"):
            integrate_region(ppm, np.ones(100), 7.0, 7.2, baseline="polynomial")


class TestIntegralFromFit:
    """Fit-based integration of an overlapped pair: the analytic component
    areas are what region integration cannot give."""

    def test_subset_of_overlapped_pair(self) -> None:
        ppm = np.linspace(7.35, 7.05, 15001)
        y = (
            _peak_on_grid(7.20, 1.0, 0.003, ppm)
            + _peak_on_grid(7.21, 0.5, 0.003, ppm)
            + RNG.normal(0.0, 0.001, ppm.size)
        )
        fit = fit_region(
            ppm, y, 7.15, 7.25,
            DeconvolutionParams(model="lorentzian", sf_mhz=500.0),
        )
        assert fit.status == "fitted"
        assert len(fit.components) == 2
        truth_a = math.pi * 1.0 * 0.003 / 2.0
        truth_b = math.pi * 0.5 * 0.003 / 2.0
        # components are sorted by area: index 0 is the strong line
        assert fit.components[0].area == pytest.approx(truth_a, rel=0.05)
        assert fit.components[1].area == pytest.approx(truth_b, rel=0.1)
        assert integral_from_fit(fit) == pytest.approx(truth_a + truth_b, rel=0.05)
        assert integral_from_fit(fit, [1]) == pytest.approx(truth_b, rel=0.1)

    def test_failed_fit_is_a_named_error_never_zero(self) -> None:
        fit = fit_region(
            np.linspace(7.3, 7.0, 50), np.zeros(50), 7.1, 7.2,
            DeconvolutionParams(snr_threshold=100.0),
        )
        assert fit.status == "skipped"
        with pytest.raises(SkipQuantification, match="no usable fit"):
            integral_from_fit(fit)

    def test_component_index_out_of_range(self) -> None:
        ppm = np.linspace(7.5, 6.5, 20001)
        y = _peak_on_grid(7.0, 1.0, 0.004, ppm)
        fit = fit_region(
            ppm, y, 6.9, 7.1, DeconvolutionParams(model="lorentzian", sf_mhz=500.0)
        )
        with pytest.raises(SkipQuantification, match="out of range"):
            integral_from_fit(fit, [5])


class TestQnmrCalculate:
    """Classical internal-standard qNMR: the mole ratio is fixed by the
    per-proton integral ratio; recovery must be exact to float precision."""

    def _standard(self) -> InternalStandard:
        # 2-proton standard signal, integral 100 units, exactly 100.0 nmol
        return InternalStandard(
            name="maleate", n_protons=2, integral=100.0, moles_nmol=100.0
        )

    def test_moles_recovered_from_ratio(self) -> None:
        result = qnmr_calculate(
            self._standard(),
            [AnalyteSpec(name="flavone-A", n_protons=3, integral=150.0)],
        )
        assert isinstance(result, QuantificationResult)
        # (150/3) / (100/2) * 100 = 50/50 * 100 = 100.0 nmol
        assert result.analytes[0].moles_nmol == pytest.approx(100.0, rel=1e-9)
        assert "n(flavone-A)" in result.analytes[0].equation
        assert "= 100 nmol" in result.analytes[0].equation  # substituted audit

    def test_mass_and_weight_percent(self) -> None:
        result = qnmr_calculate(
            self._standard(),
            [
                AnalyteSpec(
                    name="flavone-A", n_protons=3, integral=150.0,
                    molecular_weight=300.0,  # g/mol
                )
            ],
            sample_mass_mg=100.0,
        )
        analyte = result.analytes[0]
        # 100 nmol * 300 g/mol * 1e-6 = 0.03 mg
        assert analyte.mass_mg == pytest.approx(0.03, rel=1e-6)
        assert analyte.wt_percent == pytest.approx(0.03, rel=1e-6)

    def test_standard_amount_from_mass_and_purity(self) -> None:
        standard = InternalStandard(
            name="maleate", n_protons=2, integral=100.0,
            mass_mg=0.116, molecular_weight=116.07, purity_fraction=0.99,
        )
        result = qnmr_calculate(
            standard, [AnalyteSpec(name="x", n_protons=2, integral=100.0)]
        )
        # same per-proton integral -> analyte nmol == standard nmol
        expected_nmol = 0.116 / 1000.0 / 116.07 * 0.99 * 1e9
        assert result.analytes[0].moles_nmol == pytest.approx(expected_nmol, rel=1e-6)

    def test_zero_analyte_integral_is_named_error(self) -> None:
        with pytest.raises(SkipQuantification, match="positive n_protons and integral"):
            qnmr_calculate(
                self._standard(),
                [AnalyteSpec(name="x", n_protons=2, integral=0.0)],
            )

    def test_standard_without_amount_is_named_error(self) -> None:
        standard = InternalStandard(name="maleate", n_protons=2, integral=100.0)
        with pytest.raises(SkipQuantification, match="no usable amount"):
            qnmr_calculate(standard, [AnalyteSpec(name="x", n_protons=2, integral=1.0)])

    def test_no_analytes_is_named_error(self) -> None:
        with pytest.raises(SkipQuantification, match="no analytes"):
            qnmr_calculate(self._standard(), [])


class TestRegionIntegralModel:
    def test_result_carries_provenance_id(self) -> None:
        ppm = np.linspace(7.5, 6.5, 500)
        integral = integrate_region(ppm, np.abs(RNG.normal(0, 1, 500)), 7.0, 7.2)
        assert isinstance(integral, RegionIntegral)
        assert integral.model_id == "qnmr-0.1.0"
