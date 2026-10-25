"""Spectral deconvolution (ADR-0073): seeded line-shape fitting of
overlapped 1D regions.

Ground truth: synthetic spectra built from Lorentzians with KNOWN
heights, centers and FWHMs (analytic area = pi*h*w/2).  Tests assert
recovery of the known component parameters, not circularly against
the implementation.  The failure/skip paths assert the fail-loud
contract: a bad region is recorded, never silently dropped.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from nmr_preproc.deconvolution import (
    DeconvolutionParams,
    SkipDeconvolution,
    deconvolve,
    evaluate_region_fit,
    fit_region,
    noise_sigma_1d,
    pick_peaks,
)

SF = 500.0  # MHz: 1 Hz = 0.002 ppm
RNG = np.random.default_rng(73)


def _grid(lo: float = 7.4, hi: float = 7.0, points: int = 12001) -> np.ndarray:
    # descending ppm axis, Bruker convention
    return np.linspace(lo, hi, points)


def _lorentzian_spectrum(
    lines: list[tuple[float, float, float]],  # (center_ppm, height, fwhm_ppm)
    ppm: np.ndarray,
    noise: float = 0.0,
    baseline: tuple[float, float] = (0.0, 0.0),  # (offset, slope per ppm)
) -> np.ndarray:
    y = baseline[0] + baseline[1] * (ppm - ppm[0])
    for center, height, fwhm in lines:
        gamma2 = (fwhm / 2.0) ** 2
        y = y + height * gamma2 / ((ppm - center) ** 2 + gamma2)
    if noise:
        y = y + RNG.normal(0.0, noise, ppm.size)
    return y


def _area(height: float, fwhm_ppm: float) -> float:
    return math.pi * height * fwhm_ppm / 2.0


class TestNoiseEstimate:
    def test_white_noise_sigma_recovered(self) -> None:
        noise = RNG.normal(0.0, 1.0, 20000)
        assert abs(noise_sigma_1d(noise) - 1.0) < 0.2

    def test_smooth_signal_is_not_noise(self) -> None:
        ramp = np.linspace(0.0, 10.0, 5000)
        assert noise_sigma_1d(ramp) < 0.01


class TestPickPeaks:
    def test_finds_known_lines_in_order_of_prominence(self) -> None:
        ppm = _grid()
        y = _lorentzian_spectrum(
            [(7.30, 1.0, 0.003), (7.10, 0.4, 0.003)], ppm, noise=0.002
        )
        peaks = pick_peaks(ppm, y, snr_threshold=10.0, sf_mhz=SF)
        centers = sorted(p.ppm for p in peaks)
        assert len(peaks) == 2
        assert centers[0] == pytest.approx(7.10, abs=0.002)
        assert centers[1] == pytest.approx(7.30, abs=0.002)
        assert peaks[0].prominence >= peaks[1].prominence

    def test_noise_only_finds_nothing(self) -> None:
        ppm = _grid()
        y = RNG.normal(0.0, 1.0, ppm.size)
        assert pick_peaks(ppm, y, snr_threshold=10.0) == []


class TestFitRegionIsolated:
    """An isolated singlet: the fitted area must match the analytic truth
    AND the trapezoid integral of the noiseless line."""

    def test_singlet_area_and_center(self) -> None:
        ppm = _grid()
        lines = [(7.20, 1.0, 0.003)]
        y = _lorentzian_spectrum(lines, ppm, noise=0.001)
        fit = fit_region(
            ppm, y, 7.15, 7.25, DeconvolutionParams(model="lorentzian", sf_mhz=SF)
        )
        assert fit.status == "fitted"
        assert fit.r_squared is not None and fit.r_squared > 0.99
        assert len(fit.components) == 1
        comp = fit.components[0]
        assert comp.center_ppm == pytest.approx(7.20, abs=0.001)
        assert comp.fwhm_hz == pytest.approx(1.5, abs=0.1)  # 0.003 ppm * 500 MHz
        assert comp.area == pytest.approx(_area(1.0, 0.003), rel=0.03)


class TestFitRegionOverlappedDoublets:
    """THE extract case: two doublets whose lines overlap.  Region
    integration cannot split them; component fitting must."""

    LINES = [
        (7.192, 1.0, 0.003), (7.208, 1.0, 0.003),  # doublet A (J = 8 Hz)
        (7.252, 0.8, 0.003), (7.268, 0.8, 0.003),  # doublet B (J = 8 Hz)
    ]

    def test_four_components_recovered_with_areas(self) -> None:
        ppm = _grid()
        y = _lorentzian_spectrum(self.LINES, ppm, noise=0.002)
        fit = fit_region(
            ppm, y, 7.15, 7.31, DeconvolutionParams(model="lorentzian", sf_mhz=SF)
        )
        assert fit.status == "fitted"
        assert fit.r_squared > 0.99
        assert len(fit.components) == 4
        truth = {center: _area(h, w) for center, h, w in self.LINES}
        matched = 0
        for comp in fit.components:
            nearest = min(truth, key=lambda c: abs(c - comp.center_ppm))
            assert abs(nearest - comp.center_ppm) < 0.002
            assert comp.area == pytest.approx(truth[nearest], rel=0.05), (
                f"component at {comp.center_ppm:.4f}: fitted {comp.area:.6f} "
                f"vs truth {truth[nearest]:.6f}"
            )
            matched += 1
        assert matched == 4
        # per-doublet areas: A = 2 equal lines, B = 2 lines at 0.8 height
        doublet_a = sum(c.area for c in fit.components if c.center_ppm < 7.23)
        doublet_b = sum(c.area for c in fit.components if c.center_ppm > 7.23)
        assert doublet_a == pytest.approx(2 * _area(1.0, 0.003), rel=0.05)
        assert doublet_b == pytest.approx(2 * _area(0.8, 0.003), rel=0.05)

    def test_buried_shoulder_needs_manual_seed(self) -> None:
        """Below-resolution separation is a DOCUMENTED limitation: without
        a manual seed the pair is fitted as one component; with
        extra_seeds_ppm both are recovered."""
        ppm = _grid(7.24, 7.16)
        lines = [(7.200, 1.0, 0.003), (7.202, 0.3, 0.003)]  # 0.002 ppm apart
        y = _lorentzian_spectrum(lines, ppm, noise=0.001)
        params = DeconvolutionParams(model="lorentzian", sf_mhz=SF)
        unseeded = fit_region(ppm, y, 7.18, 7.22, params)
        assert unseeded.status == "fitted"
        assert len(unseeded.components) == 1  # merged: the documented limit
        seeded = fit_region(ppm, y, 7.18, 7.22, params, extra_seeds_ppm=[7.202])
        assert seeded.status == "fitted"
        assert len(seeded.components) == 2
        centers = sorted(c.center_ppm for c in seeded.components)
        assert centers[0] == pytest.approx(7.200, abs=0.0015)
        assert centers[1] == pytest.approx(7.202, abs=0.0015)
        areas = {round(c.center_ppm, 4): c.area for c in seeded.components}
        assert areas[round(centers[0], 4)] == pytest.approx(_area(1.0, 0.003), rel=0.1)
        assert areas[round(centers[1], 4)] == pytest.approx(_area(0.3, 0.003), rel=0.15)


class TestFailureAndSkipPaths:
    def test_no_seeds_above_snr_is_skipped_with_reason(self) -> None:
        ppm = _grid(7.3, 7.0)
        y = RNG.normal(0.0, 0.001, ppm.size)  # pure noise, high SNR gate
        fit = fit_region(
            ppm, y, 7.1, 7.2, DeconvolutionParams(snr_threshold=50.0, sf_mhz=SF)
        )
        assert fit.status == "skipped"
        assert "no seed peaks" in fit.reason

    def test_too_few_points_is_skipped(self) -> None:
        ppm = np.linspace(7.3, 7.0, 5)
        fit = fit_region(ppm, np.ones(5), 7.1, 7.2)
        assert fit.status == "skipped"
        assert "at least 8" in fit.reason

    def test_non_convergence_is_failed_not_raised(self) -> None:
        ppm = _grid(7.32, 7.14, points=4001)
        y = _lorentzian_spectrum(
            [(7.192, 1.0, 0.003), (7.208, 1.0, 0.003),
             (7.252, 0.8, 0.003), (7.268, 0.8, 0.003)],
            ppm, noise=0.002,
        )
        starved = DeconvolutionParams(
            model="lorentzian", sf_mhz=SF, maxfev_per_component=1
        )
        fit = fit_region(ppm, y, 7.15, 7.31, starved)
        assert fit.status == "failed"
        assert fit.reason and "converge" in fit.reason

    def test_unknown_model_raises_named_skip(self) -> None:
        ppm = _grid(7.24, 7.16)
        with pytest.raises(SkipDeconvolution, match="unknown line-shape model"):
            fit_region(
                ppm, np.zeros_like(ppm), 7.2, 7.3,
                DeconvolutionParams(model="voigt-ml"),
            )


class TestDeconvolveBatch:
    def test_batch_reports_every_region(self) -> None:
        ppm = _grid()
        y = _lorentzian_spectrum(
            [(7.20, 1.0, 0.003), (7.10, 0.5, 0.003)], ppm, noise=0.001
        )
        result = deconvolve(
            ppm, y, [(7.15, 7.25), (7.05, 7.15), (6.9, 6.95)],
            DeconvolutionParams(model="lorentzian", sf_mhz=SF),
        )
        statuses = [f.status for f in result.fits]
        assert statuses[0] == "fitted"
        assert statuses[1] == "fitted"
        assert statuses[2] == "skipped"  # empty region, recorded not dropped
        assert any("skipped" in note for note in result.notes)
        assert result.model_id == "deconv-0.1.0"
        assert result.params["model"] == "lorentzian"

    def test_mismatched_axes_raise_named_skip(self) -> None:
        with pytest.raises(SkipDeconvolution, match="axes disagree"):
            deconvolve(np.linspace(7.3, 7.0, 100), np.ones(90), [(7.1, 7.2)])

    def test_no_regions_raises_named_skip(self) -> None:
        ppm = np.linspace(7.3, 7.0, 100)
        with pytest.raises(SkipDeconvolution, match="no regions"):
            deconvolve(ppm, np.zeros(100), [])


class TestEvaluateRegionFit:
    def test_model_reproduces_the_data(self) -> None:
        ppm = _grid()
        y = _lorentzian_spectrum(
            [(7.192, 1.0, 0.003), (7.208, 1.0, 0.003)], ppm, noise=0.001
        )
        fit = fit_region(
            ppm, y, 7.15, 7.25, DeconvolutionParams(model="lorentzian", sf_mhz=SF)
        )
        total, components = evaluate_region_fit(fit, ppm)
        assert len(components) == 2
        mask = (ppm >= 7.15) & (ppm <= 7.25)
        residual = y[mask] - total[mask]
        assert float(np.max(np.abs(residual))) < 0.05

    def test_baseline_is_included_in_total(self) -> None:
        ppm = _grid()
        y = _lorentzian_spectrum(
            [(7.20, 1.0, 0.003)], ppm, noise=0.001, baseline=(0.1, 0.05)
        )
        fit = fit_region(
            ppm, y, 7.15, 7.25, DeconvolutionParams(model="lorentzian", sf_mhz=SF)
        )
        total, _ = evaluate_region_fit(fit, ppm)
        mask = (ppm >= 7.17) & (ppm <= 7.23)
        assert float(np.max(np.abs(y[mask] - total[mask]))) < 0.05
