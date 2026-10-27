"""Tier-1 home-library dereplication: match observed peaks against the
laboratory's curated reference shift tables (ADR-0061 lineage).

The deterministic Level-2 path: a confident match against the library's
curated reference tables stamps MSI Level 2 -- *putatively annotated
compound* in the standard's own words (Sumner et al. 2007), because the
tables are published/literature data, not an authentic standard
analysed in-house under identical conditions.  Level 1 ("identified
compound") requires exactly that authentic-standard comparison, which
is the home-library CALIBRATION service's job (ADR-0061: reference
standards + orthogonal property matching); this module never stamps
Level 1 (ADR-0075).  Reference tables are plain CSV --
``name,nucleus,solvent,position,shift,multiplicity,citation`` -- so
curating a published table into the library is a data act, not a code
act, and every row carries its citation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["LibraryEntry", "MatchResult", "load_library", "match_entry",
           "dereplicate"]


@dataclass
class LibraryEntry:
    name: str
    nucleus: str
    solvent: str
    citation: str
    shifts: list[tuple[str, float]] = field(default_factory=list)
    multiplicity_by_position: dict[str, str] = field(default_factory=dict)


@dataclass
class MatchResult:
    entry: LibraryEntry
    matched: int
    coverage: float  # matched / library size
    mean_abs_deviation: float  # ppm
    per_peak: list[tuple[str, float, float]]  # (position, observed, library)


def load_library(path: str | Path) -> list[LibraryEntry]:
    entries: dict[tuple[str, str, str], LibraryEntry] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row["name"], row["nucleus"], row["solvent"])
            if key not in entries:
                entries[key] = LibraryEntry(
                    name=row["name"], nucleus=row["nucleus"],
                    solvent=row["solvent"], citation=row["citation"],
                )
            entries[key].shifts.append((row["position"], float(row["shift"])))
            if row.get("multiplicity"):
                entries[key].multiplicity_by_position[row["position"]] = row["multiplicity"]
    return list(entries.values())


def match_entry(
    observed_peaks: list[float],
    entry: LibraryEntry,
    tolerance: float = 0.03,
) -> MatchResult:
    """Greedy nearest matching of observed peaks to library shifts."""
    remaining = sorted(observed_peaks, reverse=True)
    per_peak: list[tuple[str, float, float]] = []
    for position, ref in sorted(entry.shifts, key=lambda s: -s[1]):
        best = None
        for obs in remaining:
            if abs(obs - ref) <= tolerance and (best is None or abs(obs - ref) < abs(best - ref)):
                best = obs
        if best is not None:
            per_peak.append((position, best, ref))
            remaining.remove(best)
    coverage = len(per_peak) / max(len(entry.shifts), 1)
    mean_dev = (
        sum(abs(o - r) for _, o, r in per_peak) / len(per_peak)
        if per_peak else float("inf")
    )
    return MatchResult(entry=entry, matched=len(per_peak), coverage=coverage,
                       mean_abs_deviation=mean_dev, per_peak=per_peak)


def dereplicate(
    observed_peaks: list[float],
    entries: list[LibraryEntry],
    tolerance: float = 0.03,
) -> tuple[MatchResult | None, str]:
    """Best library match with an honest verdict string.

    Verdicts follow the MSI levels as the standard defines them
    (Sumner et al. 2007; ADR-0075): a curated-table match is Level 2
    ("putatively annotated") at best, because Level 1 requires an
    authentic standard analysed under identical conditions -- the
    calibration service's evidence, never a published table.  Weak
    partial matches degrade to Level 3 ("putatively characterized
    compound class") and no match to Level 4 ("unknown").
    """
    if not entries:
        return None, "home library empty: dereplication not run (recorded gap)"
    results = [match_entry(observed_peaks, e, tolerance) for e in entries]
    best = max(results, key=lambda r: (r.coverage, -r.mean_abs_deviation))
    if best.coverage >= 0.80 and best.mean_abs_deviation <= 0.02:
        verdict = (
            "MSI Level 2 (putatively annotated; curated reference-table "
            f"match, coverage {best.coverage:.0%}, mean |delta| "
            f"{best.mean_abs_deviation:.3f} ppm) -- Level 1 requires an "
            "authentic standard under identical conditions (calibrate/match)"
        )
    elif best.coverage >= 0.60:
        verdict = (
            f"MSI Level 2 (putatively annotated, coverage {best.coverage:.0%}); "
            "confirmatory evidence required"
        )
    elif best.coverage >= 0.40:
        verdict = (
            f"MSI Level 3 (putatively characterized compound class; best "
            f"table match coverage {best.coverage:.0%})"
        )
    else:
        verdict = (
            f"MSI Level 4 (unknown; no confident library match, best "
            f"coverage {best.coverage:.0%}); proceed to structure elucidation"
        )
    return best, verdict
