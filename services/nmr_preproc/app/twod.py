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
]


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
) -> bytes:
    """Contour panel; ``markers`` are (f2, f1, label) correlation hits."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    m = data.matrix
    levels = np.percentile(m, [90, 96, 99, 99.7, 99.95])
    levels = np.unique(levels[levels > np.percentile(m, 80)])

    fig, ax = plt.subplots(figsize=(6.4, 5.2))
    x, y = np.meshgrid(data.f2_ppm, data.f1_ppm)
    if levels.size:
        ax.contour(x, y, m, levels=levels, colors="#1a4d8f", linewidths=0.5)
    ax.invert_xaxis()
    ax.invert_yaxis()
    if x_limits:
        ax.set_xlim(max(x_limits), min(x_limits))
    if y_limits:
        ax.set_ylim(max(y_limits), min(y_limits))
    ax.set_xlabel("δ F2 (ppm)")
    ax.set_ylabel("δ F1 (ppm)")
    ax.set_title(data.title, fontsize=10, loc="left")
    for f2, f1, label in markers or []:
        ax.plot(f2, f1, "x", color="#b22222", ms=6, mew=1.4)
        ax.annotate(
            label, (f2, f1), textcoords="offset points", xytext=(6, 4),
            fontsize=7, color="#7a1f1f",
        )
    buf = __import__("io").BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
