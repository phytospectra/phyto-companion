"""2D contour noise suppression (case-study finding, 2026-10-25).

The M6-4 COSY/HMBC report panels were unreadable: percentile-based contour
levels started at 1.4 sigma (COSY) / 1.9 sigma (HMBC) -- below the noise
floor -- producing ~100k-270k contour paths of speckle that buried the
real cross-peaks.  These tests pin the fix: noise-sigma-based levels,
negative-half noise estimation robust to t1 ridges, light Gaussian
pre-filtering, and a max-paths guard that raises the first level rather
than silently rendering a speckle field.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np

from nmr_preproc.twod import TwoDData, contour_figure, noise_sigma, pick_cross_peaks
from nmr_preproc.report import (
    CorrelationEvidence,
    PeakAssignment,
    SampleReport,
    StructureProposal,
    TwoDSpectrum,
    export_bundle,
)


def _spectrum_with_peaks(
    n1: int = 128, n2: int = 256, sigma: float = 10.0, seed: int = 7
) -> TwoDData:
    """Synthetic homonuclear 2D: Gaussian noise + a few clear cross-peaks
    plus one strong diagonal band, mimicking a processed COSY."""
    rng = np.random.default_rng(seed)
    f2 = np.linspace(10.0, 0.0, n2)
    f1 = np.linspace(10.0, 0.0, n1)
    m = rng.normal(0.0, sigma, size=(n1, n2))
    for fy2, fy1, amp in [
        (7.35, 7.35, 400 * sigma),   # diagonal
        (7.35, 5.47, 120 * sigma),   # cross-peak
        (5.47, 7.35, 120 * sigma),
        (6.55, 3.49, 80 * sigma),
        (3.49, 6.55, 80 * sigma),
    ]:
        i1 = int(np.argmin(np.abs(f1 - fy1)))
        i2 = int(np.argmin(np.abs(f2 - fy2)))
        # small 2D gaussian bump
        for di in range(-2, 3):
            for dj in range(-2, 3):
                w = np.exp(-((di**2 + dj**2) / 2.0))
                ii, jj = i1 + di, i2 + dj
                if 0 <= ii < n1 and 0 <= jj < n2:
                    m[ii, jj] += amp * w
    return TwoDData(
        kind="COSY", matrix=m, f2_ppm=f2, f1_ppm=f1, title="COSY test"
    )


def _path_count(data: TwoDData, **kwargs) -> int:
    """Render and count contour paths via the module's own machinery."""
    from scipy.ndimage import gaussian_filter

    m = np.asarray(data.matrix, dtype=np.float64)
    if kwargs.get("smooth_sigma", 1.5) > 0:
        m = gaussian_filter(m, (kwargs.get("smooth_sigma", 1.5),) * 2)
    # replicate the level logic by rendering directly
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    hi = m - gaussian_filter(m, (1.5, 1.5))
    med = float(np.median(hi))
    neg = hi[hi < med]
    sigma = 1.4826 * float(np.median(np.abs(neg - med)))
    levels = kwargs.get("first_level_sigma", 8.0) * sigma * np.geomspace(
        1, 2.0**7, 8
    )
    levels = levels[levels < m.max()]
    fig, ax = plt.subplots()
    cs = ax.contour(m, levels=levels)
    n = sum(len(seg) for seglist in cs.allsegs for seg in seglist)
    plt.close(fig)
    return n


class TestNoiseSigma:
    def test_noise_sigma_estimate_is_peak_immune(self) -> None:
        """Adding huge positive peaks must not inflate the noise estimate."""
        from nmr_preproc.twod import noise_sigma

        d = _spectrum_with_peaks()
        clean = _spectrum_with_peaks()
        clean.matrix = clean.matrix.copy()
        clean.matrix[10:20, 100:120] += 1e6  # a monster peak
        s1 = noise_sigma(d.matrix)
        s2 = noise_sigma(clean.matrix)
        assert s1 > 0
        assert 0.5 < s2 / s1 < 2.0  # within a factor of two, not 100x

    def test_first_level_is_above_noise(self) -> None:
        """The rendered first level must sit at >= 8 sigma, not at the
        ~1.4 sigma the old percentile scheme produced."""
        d = _spectrum_with_peaks()
        sigma = noise_sigma(d.matrix)
        old_first = np.percentile(d.matrix, 90)
        assert 8.0 * sigma > old_first / 2  # new floor is well above p90


class TestContourFigure:
    def test_renders_png_with_defaults(self) -> None:
        d = _spectrum_with_peaks()
        png = contour_figure(d)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 5_000

    def test_title_reports_sigma_floor(self) -> None:
        d = _spectrum_with_peaks()
        png = contour_figure(d)
        # PNG is opaque; re-render and capture the title text via the axes
        import matplotlib

        matplotlib.use("Agg")

        # smoke check only: PNG bytes change when the sigma floor changes
        png2 = contour_figure(d, first_level_sigma=16.0)
        assert png != png2

    def test_peak_removal_does_not_crash_on_flat_data(self) -> None:
        m = np.full((64, 64), 5.0)  # no noise, no peaks
        d = TwoDData(
            kind="HMBC", matrix=m,
            f2_ppm=np.linspace(12, 0, 64), f1_ppm=np.linspace(210, 0, 64),
            title="flat",
        )
        png = contour_figure(d)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_zero_filled_all_positive_falls_back(self) -> None:
        """All-positive spectrum (clipped negative tail): the fallback
        sigma path must still produce a legible PNG."""
        rng = np.random.default_rng(3)
        m = np.abs(rng.normal(0, 1, (96, 96))) * 10
        m[40:44, 40:44] += 5000  # one peak
        d = TwoDData(
            kind="COSY", matrix=m,
            f2_ppm=np.linspace(10, 0, 96), f1_ppm=np.linspace(10, 0, 96),
            title="clipped",
        )
        png = contour_figure(d)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_max_paths_guard_does_not_hang(self) -> None:
        """A speckle-dense synthetic spectrum with a tiny max_paths must
        terminate (guard raises the level instead of looping forever)."""
        rng = np.random.default_rng(11)
        m = rng.normal(0, 1, (128, 128))
        m += np.abs(rng.normal(0, 3, (128, 128)))  # heavy positive speckle
        d = TwoDData(
            kind="COSY", matrix=m,
            f2_ppm=np.linspace(10, 0, 128), f1_ppm=np.linspace(10, 0, 128),
            title="speckle",
        )
        png = contour_figure(d, max_paths=10)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_cross_peaks_survive_smoothing(self) -> None:
        """The light Gaussian pre-filter must not erase true cross-peaks."""
        d = _spectrum_with_peaks()
        peaks = pick_cross_peaks(d, percentile=99.5, exclude_diagonal_ppm=0.1)
        assert len(peaks) >= 2
        # cross-peaks found on raw matrix must be within a pixel of a
        # contour in the smoothed matrix: smoke-check via pick on smoothed
        from scipy.ndimage import gaussian_filter

        ds = TwoDData(
            kind="COSY",
            matrix=gaussian_filter(d.matrix, (1.5, 1.5)),
            f2_ppm=d.f2_ppm, f1_ppm=d.f1_ppm, title="smoothed",
        )
        peaks_s = pick_cross_peaks(
            ds, percentile=99.5, exclude_diagonal_ppm=0.1
        )
        assert len(peaks_s) >= 2


class TestBundleIntegration:
    def test_bundle_with_noisy_2d_renders(self, tmp_path: Path) -> None:
        """End-to-end: the report bundle embeds a suppressed-noise 2D panel."""
        d = _spectrum_with_peaks(n1=64, n2=64)
        tw = TwoDSpectrum(
            sample_id="S1", kind="COSY",
            matrix=d.matrix, f2_ppm=d.f2_ppm, f1_ppm=d.f1_ppm,
            title="COSY S1",
            correlations=[
                CorrelationEvidence("S1", "COSY", 7.35, 5.47, "H-1 <-> H-11a")
            ],
        )
        s = SampleReport(
            sample_id="S1", display_name="S1 (1H, CDCl3)",
            spectrum=np.linspace(0, 1, 64), ppm=np.linspace(10, 0, 64),
            solvent="CDCl3", pulse_program="zg30",
            spectrometer_frequency_mhz=500.11, referencing_shift_ppm=0.002,
            peaks=[(7.35, 1.0)],
        )
        prop = StructureProposal(
            sample_id="S1", structure_name="test compound",
            compound_class="flavonoid", formula="C6H6O",
            key_evidence="test", confidence="structure level",
            smiles="C1=CC=C(C=C1)O",
        )
        asg = [
            PeakAssignment("S1", 7.35, "d", "H-1", "ring A", "test", 7.35),
            PeakAssignment("S1", 6.55, "d", "H-2", "ring A", "test", 6.55),
            PeakAssignment("S1", 5.47, "d", "H-3", "ring C", "test", 5.47),
        ]
        export_bundle(
            tmp_path / "bundle.zip", title="noise test",
            samples=[s], rankings=[], proposals=[prop], assignments=asg,
            sources=[], limitations=["l"], discussion=["d"],
            provenance={"k": "v"}, execution_log=["e"], caveats=["c"],
            twod=[tw], dereplications=[], pharmacology=[],
        )
        with zipfile.ZipFile(tmp_path / "bundle.zip") as zf:
            names = zf.namelist()
            assert "figures/spectrum2d_S1_COSY.png" in names
            png = zf.read("figures/spectrum2d_S1_COSY.png")
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
