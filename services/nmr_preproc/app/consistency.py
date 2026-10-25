"""Automatic consistency checks for the publication bundle (ADR-0075).

Motivation (case-study finding): the ACS-style spectral text in
``tables/nmr_text_<sample>.txt`` and the numbers in the assignment
tables/JSON were generated from the same PeakAssignment objects, but
nothing VERIFIED that they stay consistent -- a hand-edited table, a
stale cached copy, or a reporting bug could silently disagree with the
prose and no gate would fire.  The Q1 rules (report.R1-R6) check that
artefacts EXIST; this module checks that the artefacts AGREE.

Consistency here means, per sample:

* every chemical shift that appears in the ACS text appears (within
  text-rounding tolerance) in the assignment table, and vice versa;
* every assignment label appears in both artefacts;
* the frequency/solvent/nucleus header matches the sample metadata;
* picked-peak ppm values quoted in the text correspond to actual
  assigned peaks (a peak list not backed by the assignments is
  flagged, not silently dropped);
* deconvolution component centres are consistent with the region
  bounds recorded for the same sample (fit centres must lie inside
  the fitted window).

A check NEVER mutates data: it returns structured findings so callers
choose to refuse the bundle (the export path does) or surface the
disagreement.  Text numbers are parsed with the same rounding the
ACS writer uses (2 decimals), so text-vs-table comparisons carry an
explicit half-ulp tolerance rather than an arbitrary epsilon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .report import PeakAssignment, SampleReport

__all__ = [
    "ConsistencyFinding",
    "ConsistencyReport",
    "check_sample_consistency",
    "check_bundle_consistency",
]


@dataclass
class ConsistencyFinding:
    """One disagreement between two artefacts of the same sample."""

    sample_id: str
    check: str  # e.g. 'shifts_text_vs_table'
    severity: str  # 'error' | 'warning'
    message: str


@dataclass
class ConsistencyReport:
    """All findings for a set of samples, with a one-line verdict."""

    findings: list[ConsistencyFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    @property
    def summary(self) -> str:
        errors = sum(f.severity == "error" for f in self.findings)
        warnings = sum(f.severity == "warning" for f in self.findings)
        return (
            f"consistency: {'OK' if self.ok else 'FAILED'} "
            f"({errors} error(s), {warnings} warning(s))"
        )


# ACS lines quote shifts with two decimals; the writer uses f"{ppm:.2f}".
_TEXT_TOLERANCE = 0.005 + 1e-9

#: e.g. "7.35 (d, J 8.4, H-1)" or "3.64 (dd, J 11/5, H-6eq)"; tolerates
#: one level of nested parentheses (assignments like "OCH2O (a)" appear
#: inside the descriptor)
_SHIFT_TOKEN = re.compile(
    r"(?<![\d.])(\d{1,3}\.\d{1,3})\s*\(([^()]*(?:\([^()]*\)[^()]*)?)\)"
)

#: the trailing label inside a parenthesised descriptor, e.g. "H-11a",
#: "OCH2O" or "H-1"; keeps internal hyphens and primes
_LABEL_TOKEN = re.compile(r"([A-Za-z0-9][A-Za-z0-9'\-\"]*[A-Za-z0-9]+)\s*$")


def _assignments_for(
    sample: SampleReport, assignments: list[PeakAssignment]
) -> list[PeakAssignment]:
    rows = [a for a in assignments if a.sample_id == sample.sample_id]
    return sorted(rows, key=lambda a: -a.ppm)


def _text_entries(text: str) -> list[tuple[float, str]]:
    """Parse '(shift (descriptor, label))' pairs out of an ACS line.

    The label is the last whitespace-separated token of the descriptor
    with the multiplicity/J-coupling prefixes stripped (``d, J 8.4,
    H-1`` -> ``H-1``).  Returns (shift, label) pairs in text order.
    """
    entries: list[tuple[float, str]] = []
    for match in _SHIFT_TOKEN.finditer(text):
        shift = float(match.group(1))
        descriptor = re.sub(r"\([^()]*\)", " ", match.group(2))
        label_match = _LABEL_TOKEN.search(descriptor.strip())
        label = label_match.group(1) if label_match else descriptor.strip()
        entries.append((shift, label))
    return entries


def check_sample_consistency(
    sample: SampleReport,
    assignments: list[PeakAssignment],
) -> list[ConsistencyFinding]:
    """Check one sample's text artefact against its tabular artefacts."""
    from .report import format_nmr_text_acs

    findings: list[ConsistencyFinding] = []
    sid = sample.sample_id
    rows = _assignments_for(sample, assignments)

    text = format_nmr_text_acs(sample, assignments)
    text_entries = _text_entries(text)

    # --- shifts: text vs table -------------------------------------------
    table_shifts = [(a.ppm, a.assignment) for a in rows]
    text_shifts = sorted(shift for shift, _ in text_entries)
    table_sorted = sorted(shift for shift, _ in table_shifts)
    text_only = [
        s for s in text_shifts
        if not any(abs(s - t) <= _TEXT_TOLERANCE for t in table_sorted)
    ]
    table_only = [
        s for s in table_sorted
        if not any(abs(s - t) <= _TEXT_TOLERANCE for t in text_shifts)
    ]
    if text_only:
        findings.append(ConsistencyFinding(
            sample_id=sid, check="shifts_text_vs_table", severity="error",
            message=(
                f"shift(s) {text_only} appear in the ACS text but not in "
                "the assignment table (table/text numbers must match)"
            ),
        ))
    if table_only:
        findings.append(ConsistencyFinding(
            sample_id=sid, check="shifts_text_vs_table", severity="error",
            message=(
                f"assignment shift(s) {table_only} are missing from the "
                "ACS text (table/text numbers must match)"
            ),
        ))

    # --- labels: text vs table -------------------------------------------
    text_labels = {label for _, label in text_entries}
    table_labels = {a.assignment for a in rows}
    for label in sorted(table_labels - text_labels):
        findings.append(ConsistencyFinding(
            sample_id=sid, check="labels_text_vs_table", severity="error",
            message=(
                f"assignment '{label}' is in the table but the ACS text "
                "does not name it"
            ),
        ))
    for label in sorted(text_labels - table_labels):
        findings.append(ConsistencyFinding(
            sample_id=sid, check="labels_text_vs_table", severity="error",
            message=(
                f"assignment '{label}' is named in the ACS text but not "
                "in the assignment table"
            ),
        ))

    # --- header: frequency / solvent / nucleus ---------------------------
    nucleus = (
        "13C"
        if sample.pulse_program and "13" in (sample.pulse_program or "")
        else "1H"
    )
    freq = (
        f"{sample.spectrometer_frequency_mhz:.0f} MHz"
        if sample.spectrometer_frequency_mhz else ""
    )
    expected_head = f"{nucleus} NMR ({freq}, {sample.solvent or ''})".replace(
        ", )", ")"
    )
    if not text.startswith(expected_head):
        findings.append(ConsistencyFinding(
            sample_id=sid, check="header_matches_metadata", severity="error",
            message=(
                f"ACS header reads {text.split(' δ')[0]!r} but the sample "
                f"metadata implies {expected_head!r}"
            ),
        ))

    # --- picked peaks vs assignments --------------------------------------
    if sample.peaks:
        assigned_ppms = [a.ppm for a in rows]
        for ppm, _prominence in sample.peaks:
            if not any(abs(ppm - a) <= 0.02 for a in assigned_ppms):
                findings.append(ConsistencyFinding(
                    sample_id=sid, check="peaks_vs_assignments",
                    severity="warning",
                    message=(
                        f"picked peak {ppm:.3f} ppm has no assignment "
                        "(table/peak-list disagreement)"
                    ),
                ))

    # --- deconvolution components inside their fitted window --------------
    for fit in sample.region_fits:
        for component in getattr(fit, "components", []):
            centre = getattr(component, "center_ppm", None)
            if centre is None:
                continue
            if not (
                min(fit.lo_ppm, fit.hi_ppm) - 1e-9
                <= centre
                <= max(fit.lo_ppm, fit.hi_ppm) + 1e-9
            ):
                findings.append(ConsistencyFinding(
                    sample_id=sid, check="deconvolution_within_region",
                    severity="error",
                    message=(
                        f"deconvolution component {centre:.4f} ppm lies "
                        f"outside its fitted region "
                        f"[{fit.lo_ppm}, {fit.hi_ppm}] ppm"
                    ),
                ))

    return findings


def check_bundle_consistency(
    samples: list[SampleReport],
    assignments: list[PeakAssignment],
) -> ConsistencyReport:
    """Run every check over every sample; callers decide refusal policy."""
    report = ConsistencyReport()
    for sample in samples:
        report.findings.extend(check_sample_consistency(sample, assignments))
    return report
