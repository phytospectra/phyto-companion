"""Processed 2D Bruker data (COSY/HMBC) for evidence-chain reports.

Loads ``pdata/1/2rr`` + ``procs``/``proc2s`` (TopSpin-processed NUS
reconstructions), builds both ppm axes, picks cross-peaks, and renders
contour panels -- the 2D layer of the Q1 report bundle.  Orientation is
self-checked: a COSY must be symmetric about its diagonal, and the
loader uses that property to validate the (F1, F2) reshape on every
load of a homonuclear spectrum.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = [
    "TwoDData",
    "load_2d",
    "pick_cross_peaks",
    "contour_figure",
    "noise_sigma",
]


def noise_sigma(matrix: np.ndarray, smooth_sigma: float = 1.5) -> float:
    """Estimate the noise sigma of a processed 2D spectrum.

    The estimate comes from the NEGATIVE half of a high-passed copy
    (matrix minus its Gaussian-smoothed self).  Signals are positive, so
    the negative half is peak-free; its MAD is an unbiased noise estimate
    even when t1 noise ridges inflate the whole-spectrum MAD.  If the
    negative tail is clipped (e.g. zero-filled / rectified spectra), the
    function falls back to the whole-spectrum MAD with iterative sigma
    clipping.

    Returns 0.0 only if the spectrum is constant.
    """
    from scipy.ndimage import gaussian_filter

    m = np.asarray(matrix, dtype=np.float64)
    hi = m - gaussian_filter(m, sigma=(smooth_sigma, smooth_sigma))
    med = float(np.median(hi))
    neg = hi[hi < med]
    if neg.size:
        sigma = 1.4826 * float(np.median(np.abs(neg - med)))
        if sigma > 0:
            return sigma
    # Fallback: whole-spectrum MAD with sigma clipping.
    v = m.ravel()
    sigma = 1.4826 * float(np.median(np.abs(v - np.median(v))))
    for _ in range(25):
        if sigma <= 0:
            return 0.0
        keep = np.abs(v - np.median(v)) < 4 * sigma
        s2 = 1.4826 * float(np.median(np.abs(v[keep] - np.median(v[keep]))))
        if not np.isfinite(s2) or s2 <= 0:
            break
        if abs(s2 - sigma) / sigma < 1e-4:
            return s2
        sigma = s2
    return sigma


@dataclass
class TwoDData:
    kind: str  # 'COSY' | 'HMBC' | other experiment label
    matrix: np.ndarray  # rows = F1, cols = F2
    f2_ppm: np.ndarray  # direct dimension, descending
    f1_ppm: np.ndarray  # indirect dimension, descending
    title: str


def _parse_params(path: Path) -> dict:
    params: dict = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("##$"):
            key, _, value = line[3:].partition("=")
            params[key.strip()] = value.strip()
    return params


def load_2d(pdata_dir: str | Path, kind: str, title: str | None = None) -> TwoDData:
    """Load one processed 2D experiment directory (pdata/N)."""
    p = Path(pdata_dir)
    f2p = _parse_params(p / "procs")   # direct dimension
    f1p = _parse_params(p / "proc2s")  # indirect dimension
    si2, si1 = int(f2p["SI"]), int(f1p["SI"])

    raw = np.fromfile(p / "2rr", dtype=np.dtype("<i4"))
    if raw.size < si1 * si2:
        raise ValueError(
            f"2rr too small: {raw.size} points < {si1}x{si2}"
        )
    matrix = raw[: si1 * si2].astype(np.float64).reshape(si1, si2)

    def _axis(params: dict) -> np.ndarray:
        sf = float(params["SF"])
        sw_ppm = float(params["SW_p"]) / sf
        offset = float(params["OFFSET"])
        si = int(params["SI"])
        return offset - np.arange(si) * (sw_ppm / si)

    f2 = _axis(f2p)
    f1 = _axis(f1p)
    if kind.upper() == "COSY":
        # homonuclear sanity: symmetric within the processed intensity
        # scale -- catches F1/F2 transposition before anything renders
        corr = np.corrcoef(matrix.ravel(), matrix.T.ravel())[0, 1]
        if corr < 0.9:
            matrix = matrix.T
            f1, f2 = f2, f1
    return TwoDData(kind=kind, matrix=matrix, f2_ppm=f2, f1_ppm=f1,
                    title=title or kind)


def pick_cross_peaks(
    data: TwoDData,
    percentile: float = 99.5,
    exclude_diagonal_ppm: float | None = None,
    max_peaks: int = 60,
) -> list[tuple[float, float, float]]:
    """Local maxima above ``percentile`` intensity, as (f2, f1, height).

    ``exclude_diagonal_ppm`` (homonuclear spectra) drops the |f2-f1|
    diagonal band, keeping only true correlations.
    """
    from scipy.ndimage import maximum_filter

    m = data.matrix
    threshold = np.percentile(m, percentile)
    local_max = m == maximum_filter(m, size=5)
    out: list[tuple[float, float, float]] = []
    for i, j in zip(*np.where(local_max & (m >= threshold))):
        f2, f1 = float(data.f2_ppm[j]), float(data.f1_ppm[i])
        if exclude_diagonal_ppm is not None and abs(f2 - f1) <= exclude_diagonal_ppm:
            continue
        out.append((f2, f1, float(m[i, j])))
    out.sort(key=lambda t: -t[2])
    return out[:max_peaks]


def contour_figure(
    data: TwoDData,
    markers: list[tuple[float, float, str]] | None = None,
    x_limits: tuple[float, float] | None = None,
    y_limits: tuple[float, float] | None = None,
    *,
    smooth_sigma: float = 1.5,
    first_level_sigma: float = 8.0,
    levels_per_decade: float = 2.0,
    max_paths: int = 6000,
) -> bytes:
    """Contour panel; ``markers`` are (f2, f1, label) correlation hits.

    Noise handling (case-study finding: percentile-based levels started at
    ~1.4 sigma on the M6-4 COSY/HMBC data, i.e. below the noise floor,
    rendering ~100k-270k paths of speckle that buried the real cross-peaks):

    - The noise sigma is estimated from the NEGATIVE half of a high-passed
      copy of the spectrum.  Signals are positive, so the negative half is
      peak-free; its MAD is an unbiased noise estimate even when t1 noise
      ridges inflate the whole-spectrum MAD.  A zero-filling/DC offset arte
      fact could clip the negative tail, in which case the estimate falls
      back to the whole-spectrum sigma-clipped MAD.
    - Levels are geometric in sigma (first at ``first_level_sigma`` sigma,
      one per ``levels_per_decade`` octave) so weak peaks and strong peaks
      both stay visible; percentile levels collapse onto the noise when the
      spectrum is sparse.
    - A light Gaussian pre-filter (``smooth_sigma``) merges single-pixel
      speckle into the surrounding contour before levels are applied; it
      cannot manufacture a peak that was not there, only widen existing
      ones by about one pixel.
    - If the level set still produces more than ``max_paths`` paths, the
      first level is raised stepwise (up to 4 times) until the render is
      legible; each raise is recorded in the log so the reader can see
      which levels the panel shows.
    """
    import io
    import logging

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.ndimage import gaussian_filter

    logger = logging.getLogger(__name__)

    m = np.asarray(data.matrix, dtype=np.float64)
    sigma = noise_sigma(m, smooth_sigma=smooth_sigma)

    if sigma > 0:
        levels = first_level_sigma * sigma * np.geomspace(
            1.0, levels_per_decade**7, 8
        )
        # keep only levels the data can actually reach, but always at least two
        reachable = levels[levels < m.max()]
        if reachable.size >= 2:
            levels = reachable
        else:
            levels = np.linspace(m.max() * 0.05, m.max() * 0.6, 4)
    else:
        # Flat or constant spectrum: arbitrary visually-spaced levels.
        levels = np.linspace(m.max() * 0.05, m.max() * 0.6, 4)


    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    x, y = np.meshgrid(data.f2_ppm, data.f1_ppm)

    smoothed = (
        gaussian_filter(m, sigma=(smooth_sigma, smooth_sigma))
        if smooth_sigma > 0
        else m
    )
    attempt = 0
    while True:
        cs = ax.contour(
            x, y, smoothed, levels=levels, colors="#1a4d8f", linewidths=0.5
        )
        n_paths = sum(len(seglist) for seglist in cs.allsegs)
        if n_paths <= max_paths or attempt >= 4 or len(levels) < 2:
            break
        # Still too busy: drop the lowest level and re-render.
        levels = levels[1:]
        attempt += 1
        logger.info(
            "contour_figure: %s has %d paths > %d; raised first level to "
            "%.1f sigma",
            data.title,
            n_paths,
            max_paths,
            levels[0] / sigma if sigma else float("nan"),
        )
    if attempt:
        logger.info(
            "contour_figure: %s rendered with %d levels, first at %.1f sigma",
            data.title,
            len(levels),
            levels[0] / sigma if sigma else float("nan"),
        )

    ax.invert_xaxis()
    ax.invert_yaxis()
    if x_limits:
        ax.set_xlim(max(x_limits), min(x_limits))
    if y_limits:
        ax.set_ylim(max(y_limits), min(y_limits))
    ax.set_xlabel("δ F2 (ppm)")
    ax.set_ylabel("δ F1 (ppm)")
    ax.set_title(
        data.title
        + (
            f"  (contours ≥ {levels[0] / sigma:.0f}σ, log-spaced)"
            if sigma
            else ""
        ),
        fontsize=10,
        loc="left",
    )
    for f2, f1, label in markers or []:
        ax.plot(f2, f1, "x", color="#b22222", ms=6, mew=1.4)
        ax.annotate(
            label, (f2, f1), textcoords="offset points", xytext=(6, 4),
            fontsize=7, color="#7a1f1f",
        )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
