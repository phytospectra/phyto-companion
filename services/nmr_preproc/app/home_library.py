"""Tier-1 home-library dereplication: match observed peaks against the
laboratory's curated reference shift tables (ADR-0061 lineage).

The deterministic MSI Level-1 path: a confident match against the lab's
own reference spectra stamps Level 1 *pending human sign-off*.  Reference
tables are plain CSV -- ``name,nucleus,solvent,position,shift,multiplicity,
citation`` -- so curating a published table into the library is a data
act, not a code act, and every row carries its citation.
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

    Verdicts follow the platform's confidence tiers: Level 1 is only
    *pending human sign-off* even on a perfect score, never auto-stamped.
    """
    if not entries:
        return None, "home library empty: dereplication not run (recorded gap)"
    results = [match_entry(observed_peaks, e, tolerance) for e in entries]
    best = max(results, key=lambda r: (r.coverage, -r.mean_abs_deviation))
    if best.coverage >= 0.80 and best.mean_abs_deviation <= 0.02:
        verdict = ("MSI Level 1 pending human sign-off "
                   f"(coverage {best.coverage:.0%}, mean |delta| {best.mean_abs_deviation:.3f} ppm)")
    elif best.coverage >= 0.60:
        verdict = (f"Level 2 candidate (coverage {best.coverage:.0%}); "
                   "confirmatory evidence required")
    else:
        verdict = (f"no confident library match (best coverage {best.coverage:.0%}); "
                   "proceed to structure elucidation")
    return best, verdict
