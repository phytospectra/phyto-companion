"""Peak-purity detection for HPLC-PDA peaks (ADR-0065).

A chromatographic peak that looks clean in the total-ion chromatogram can
still be a co-elution.  Two cheap orthogonal signals expose that:

* **UV spectral dissimilarity across the peak** -- a pure peak has the
  same chromophore at apex, leading edge and trailing edge; a mixture
  drifts (the apex is a weighted blend, the edges are one component).
* **Mass-channel misalignment** -- if the shoulder carries a different
  m/z from the apex, the shoulder is a different compound.

``peak_purity`` returns a 0-1 score.  Low means "treat the identification
as the dominant component of a mixture, not as a pure isolate".  It is a
soft signal: the caller decides what to do with it (the platform forces
AMBIGUOUS below 0.5, ADR-0065).
"""

from __future__ import annotations

from typing import Sequence

from .chromatography import uv_cosine

__all__ = ["peak_purity", "PURITY_AMBIGUITY_THRESHOLD", "purity_caveat"]

PURITY_AMBIGUITY_THRESHOLD = 0.5

PURITY_CAVEAT = (
    "Peak appears to be a mixture; identification may refer to the dominant "
    "component."
)


def _ms_alignment(
    apex_mz: float | None, shoulder_mz: float | None
) -> tuple[float, str]:
    """1.0 aligned / 0.2 misaligned / 0.8 unknown (no shoulder channel)."""
    if apex_mz is None:
        return 0.8, "no MS channel supplied"
    if shoulder_mz is None:
        return 0.8, "no shoulder MS channel (purity unconfirmed)"
    tolerance = max(0.05, apex_mz * 50e-6)  # 50 ppm or 0.05 Da, whichever wider
    delta = abs(apex_mz - shoulder_mz)
    if delta <= tolerance:
        return 1.0, f"apex/shoulder m/z aligned (delta {delta:.4f})"
    if delta <= 3 * tolerance:
        return 0.6, f"apex/shoulder m/z drifting (delta {delta:.4f})"
    return 0.2, f"apex/shoulder m/z misaligned (delta {delta:.4f}): co-elution"


def peak_purity(
    uv_apex: Sequence[tuple[float, float]] | None,
    uv_left: Sequence[tuple[float, float]] | None,
    uv_right: Sequence[tuple[float, float]] | None,
    ms_apex_mz: float | None,
    ms_shoulder_mz: float | None,
) -> float:
    """Score 0-1: how likely the peak is a single component.

    UV agreement is weighted 0.7 and mass alignment 0.3.  Missing UV
    traces contribute a neutral 0.75 rather than a pass -- absence of
    evidence is not purity.  The result is clamped to [0, 1].
    """
    edges = [edge for edge in (uv_left, uv_right) if edge]
    if uv_apex and edges:
        similarities = [
            uv_cosine(uv_apex, edge) for edge in edges  # type: ignore[arg-type]
        ]
        uv_score = sum(similarities) / len(similarities)
    else:
        uv_score = 0.75  # neutral: no PDA edges supplied

    ms_score, _note = _ms_alignment(ms_apex_mz, ms_shoulder_mz)
    score = 0.7 * uv_score + 0.3 * ms_score
    return round(min(1.0, max(0.0, score)), 4)


def purity_caveat(purity_score: float) -> str | None:
    """The evidence-panel caveat to attach when a peak is impure."""
    if purity_score < PURITY_AMBIGUITY_THRESHOLD:
        return PURITY_CAVEAT
    return None
