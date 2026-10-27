"""Lipid-region masks for 13C assignment curation (ADR-0076).

Case-study finding: the M6-4 13C spectrum carried a large lipid/glycerol
backbone envelope (roughly 60-80 ppm -- glycerol C1/C3 ~63 ppm, C2 ~72
ppm, plus aliphatic chain carbons below 40 ppm) that its first-pass
curation did NOT exclude.  The result was a 10-peak analyte list
dominated by lipid signals, which both wastes the matching budget and
manufactures false assignment candidates in the 60-80 ppm window where
pterocarpan C-6/C-11 carbons actually resonate.

The standard exclusion windows are defined ONCE here so every consumer
(assignment curation, dereplication, candidate ranking) uses the same
mask and the mask is citable, testable and auditable.  Values follow the
conventional 13C resonance ranges of glycerolipid/fatty-acyl contaminant
backbones in plant extract spectra:

* 0-40 ppm    -- fatty-acyl chain methylenes and terminal methyls
* 55-90 ppm   -- glycerol backbone (C1/C3 ~63, C2 ~72) plus
                 glycosidic/sugar carbons of glycolipids
* 125-135 ppm -- fatty-acyl olefinic carbons (cis double bonds)
* 172-180 ppm -- ester/carboxyl carbons of acyl lipids

Region edges are INCLUSIVE bounds the caller may tighten; nothing here
is Bruker-specific.  A peak inside an excluded window is flagged, never
silently rewritten: :func:`flag_lipid_peaks` returns the offending
indices and :func:`curate_13c_assignment` drops them into a recorded
"lipid-excluded" bucket so the report can show what was excluded and
why.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "LIPID_REGIONS_13C",
    "DEFAULT_LIPID_REGIONS_13C",
    "LipidCuration",
    "is_in_regions",
    "flag_lipid_peaks",
    "curate_13c_assignment",
]

#: (low_ppm, high_ppm, reason) triples covering the common lipid/acyl
#: contaminant envelopes of plant 13C spectra.  Catalog of ALL known
#: windows; the DEFAULT mask below is the safe subset.
LIPID_REGIONS_13C: tuple[tuple[float, float, str], ...] = (
    (10.0, 40.0, "fatty-acyl chain methylenes/terminal methyls"),
    (55.0, 75.0, "glycerol backbone C1/C3 (~63) and glycolipid sugars"),
    (125.0, 135.0, "fatty-acyl olefinic carbons"),
    (172.0, 174.5, "triacylglycerol ester carbonyls"),
)

#: Default exclusion mask: the aliphatic fatty-acyl envelope ONLY.  This
#: mirrors the case-study curation ([10, 40] ppm) and is the one window
#: that cannot overlap natural-product carbons of the platform's target
#: classes (pterocarpans, flavonoids, isoflavones): their oxygenated
#: aliphatic carbons sit at 60-90 ppm, aromatics at 93-160 ppm, and
#: carbonyls at 165-185 ppm.  The other catalog windows (glycerol,
#: olefinic, ester C=O) DO overlap those analyte carbons, so they are
#: opt-in: pass them explicitly when the sample's chemistry justifies
#: it, and the report records what was excluded and why.
DEFAULT_LIPID_REGIONS_13C: tuple[tuple[float, float], ...] = ((10.0, 40.0),)


def is_in_regions(
    ppm: float, regions: tuple[tuple[float, float], ...] = DEFAULT_LIPID_REGIONS_13C
) -> bool:
    """True when ``ppm`` falls inside any (low, high) window (inclusive)."""
    return any(lo <= ppm <= hi for lo, hi in regions)


@dataclass
class LipidCuration:
    """Outcome of applying the lipid mask to a 13C peak list."""

    kept: list[tuple[float, float]] = field(default_factory=list)
    excluded: list[tuple[float, float, str]] = field(default_factory=list)
    # (ppm, prominence, reason) for every dropped peak

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_excluded(self) -> int:
        return len(self.excluded)

    def summary(self) -> str:
        if not self.excluded:
            return "lipid curation: 0 peaks excluded"
        reasons = "; ".join(
            f"[{lo:g}, {hi:g}] ppm ({reason})"
            for (lo, hi), reason in self._reason_by_window().items()
        )
        return (
            f"lipid curation: {self.n_excluded} peak(s) excluded in "
            f"{reasons}; {self.n_kept} kept"
        )

    def excluded_ppm(self) -> list[float]:
        return [ppm for ppm, _prom, _reason in self.excluded]

    def _reason_by_window(self) -> dict[tuple[float, float], str]:
        by_window: dict[tuple[float, float], str] = {}
        for ppm, _prom, reason in self.excluded:
            for lo, hi, r in LIPID_REGIONS_13C:
                if lo <= ppm <= hi and r == reason:
                    by_window[(lo, hi)] = r
                    break
        return by_window


def flag_lipid_peaks(
    peaks: list[tuple[float, float]],
    regions: tuple[tuple[float, float], ...] = DEFAULT_LIPID_REGIONS_13C,
) -> list[int]:
    """Indices of peaks inside the lipid windows (peaks are (ppm, prom))."""
    return [i for i, (ppm, _prom) in enumerate(peaks) if is_in_regions(ppm, regions)]


def curate_13c_assignment(
    peaks: list[tuple[float, float]],
    regions: tuple[tuple[float, float], ...] = DEFAULT_LIPID_REGIONS_13C,
) -> LipidCuration:
    """Split a 13C analyte peak list into kept vs lipid-excluded peaks.

    Every excluded peak carries the reason string of the window that
    excluded it, so a report can state WHAT was dropped and WHY instead
    of silently narrowing the assignment table.
    """
    curation = LipidCuration()
    for ppm, prom in peaks:
        reason = next(
            (
                r
                for lo, hi, r in LIPID_REGIONS_13C
                if (lo, hi) in regions and lo <= ppm <= hi
            ),
            None,
        )
        if reason is None and any(
            lo <= ppm <= hi
            for lo, hi in regions
            if (lo, hi) not in {(l, h) for l, h, _ in LIPID_REGIONS_13C}
        ):
            reason = "caller-supplied exclusion window"
        if reason is None:
            curation.kept.append((ppm, prom))
        else:
            curation.excluded.append((ppm, prom, reason))
    return curation