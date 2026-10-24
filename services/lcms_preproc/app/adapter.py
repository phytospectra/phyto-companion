"""LC-MS feature-table adapter: XCMS, MZmine 3, MS-DIAL (ADR-0069).

ADAPTER ONLY.  This module never detects peaks, aligns, or gap-fills --
it recognises exported feature tables from the three standard tools,
normalises their column schemas to the internal layout (feature_id,
mz, rt, per-sample intensities), and validates required columns with
NAMED reasons.  A file recognisable as none of the three schemas is a
named skip listing the expected schemas, never a parse error and never
a guessed mapping.

Accepted schemas (header signatures, matched case-insensitively):
* MZmine 3 feature table: columns "row m/z" and "row retention time"
  plus one column per sample (wide).
* XCMS peaklist: columns "mz" and "rt" (rt in seconds) plus one column
  per sample (wide); an unnamed or "feature" first column is the id.
* MS-DIAL alignment result: a header row containing "average rt (min)"
  and "average mz" (found by scanning past MS-DIAL's metadata lines),
  plus one column per sample (wide).
* Long format (any tool): columns feature_id, mz, rt, sample,
  intensity -- one row per (sample, feature).

RT is normalised to minutes (XCMS seconds are converted; the conversion
is recorded in the notes, not applied silently).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from phyto_shared.metabolomics import PreprocessingRecord

__all__ = [
    "AdaptedTable",
    "SkipAdaptation",
    "EXPECTED_SCHEMAS",
    "detect_tool",
    "adapt_csv",
]


class SkipAdaptation(Exception):
    """Named skip: the file is not a recognised export, with a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


EXPECTED_SCHEMAS: tuple[str, ...] = (
    "MZmine 3 feature table (wide): 'row ID', 'row m/z', "
    "'row retention time', one column per sample",
    "XCMS peaklist (wide): 'mz', 'rt' (seconds), one column per sample",
    "MS-DIAL alignment result (wide): 'Alignment ID', 'Average Rt (min)', "
    "'Average Mz', one column per sample",
    "long format (any tool): feature_id, mz, rt, sample, intensity",
)

# Non-sample metadata columns MZmine / MS-DIAL exports may carry between
# the id/mz/rt block and the sample columns.  A label must be listed here
# to be skipped as metadata; anything else after the known block is a
# sample column.  Sample NAMES never parse as numbers either, so the
# values-based heuristics are not enough on their own.
METADATA_COLUMN_LABELS: tuple[str, ...] = (
    "feature area",
    "peak area",
    "area",
    "max height",
    "height",
    "rt range",
    "mz range",
    "fwhm",
    "comment",
    "identification",
    "isotope pattern",
    "charge",
    "quality",
    "spectrum",
)


@dataclass
class AdaptedTable:
    feature_ids: list[str]
    mz: list[float]
    rt_min: list[float]
    intensities: dict[str, dict[str, float]] = field(default_factory=dict)
    sample_ids: list[str] = field(default_factory=list)
    tool_name: str | None = None
    tool_version: str | None = None
    rt_tolerance_s: float | None = None
    mz_tolerance_ppm: float | None = None
    notes: list[str] = field(default_factory=list)


def _read_rows(csv_text: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(csv_text))
        if any(cell.strip() for cell in row)
    ]


def _header_row(rows: list[list[str]]) -> tuple[int, list[str]]:
    """MS-DIAL buries the header under metadata lines; scan for it."""
    for index, row in enumerate(rows[:30]):
        lowered = {cell.lower() for cell in row}
        if any("average rt" in cell for cell in lowered):
            return index, row
        if "row m/z" in lowered or ("mz" in lowered and "rt" in lowered):
            return index, row
        lowered_long = {cell.lower().replace(" ", "_") for cell in row}
        if {"feature_id", "sample", "intensity"}.issubset(lowered_long):
            return index, row
    return 0, rows[0] if rows else []


def _skip_metadata_columns(header: list[str], start: int) -> int:
    """Advance ``start`` past known non-sample metadata columns."""
    while start < len(header) and header[start].strip().lower() in METADATA_COLUMN_LABELS:
        start += 1
    return start


def detect_tool(header: list[str]) -> str | None:
    lowered = [cell.lower() for cell in header]
    lowered_set = set(lowered)
    normalised = {cell.lower().replace(" ", "_") for cell in header}
    if any(cell.startswith("row m/z") for cell in lowered):
        return "mzmine3"
    if any("average rt (min)" in cell for cell in lowered):
        return "msdial"
    if "mz" in lowered_set and "rt" in lowered_set and len(lowered) > 3:
        return "xcms"
    if {"feature_id", "sample", "intensity"}.issubset(normalised):
        return "long"
    return None


def _require(header: list[str], needed: list[str], tool: str) -> dict[str, int]:
    """Column index map with a named reason when a column is missing."""
    lowered = {cell.lower(): i for i, cell in enumerate(header)}
    aliases = {
        "feature_id": ["feature_id", "feature", "row id", "alignment id"],
        "mz": ["mz", "row m/z", "average mz"],
        "rt": ["rt", "row retention time", "average rt (min)"],
        "sample": ["sample", "sample_id"],
        "intensity": ["intensity", "peak area"],
    }
    found: dict[str, int] = {}
    missing: list[str] = []
    for need in needed:
        for alias in aliases[need]:
            if alias in lowered:
                found[need] = lowered[alias]
                break
        else:
            missing.append(need)
    if missing:
        raise SkipAdaptation(
            f"{tool} schema detected but required column(s) missing: "
            f"{', '.join(missing)}; header was: {header}"
        )
    return found


def _float_or_none(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def adapt_csv(csv_text: str) -> AdaptedTable:
    """Normalise one exported feature table, or skip with a named reason."""
    rows = _read_rows(csv_text)
    if len(rows) < 2:
        raise SkipAdaptation(
            "file has no data rows; expected one of:\n  - "
            + "\n  - ".join(EXPECTED_SCHEMAS)
        )
    header_index, header = _header_row(rows)
    tool = detect_tool(header)
    if tool is None:
        raise SkipAdaptation(
            "file is not recognisable as an XCMS, MZmine 3 or MS-DIAL "
            "export (or the tool-neutral long format); expected one of:\n"
            "  - " + "\n  - ".join(EXPECTED_SCHEMAS)
            + f"\nheader found (row {header_index + 1}): {header}"
        )
    data_rows = rows[header_index + 1 :]

    if tool == "long":
        columns = _require(header, ["feature_id", "mz", "rt", "sample", "intensity"], "long format")
        table = AdaptedTable(tool_name=None)
        table.notes.append("long format: tool not identifiable; tool_name recorded as absent")
        seen_features: dict[str, int] = {}
        seen_samples: list[str] = []
        for row in data_rows:
            if len(row) <= max(columns.values()):
                continue
            feature_id = row[columns["feature_id"]]
            sample_id = row[columns["sample"]]
            intensity = _float_or_none(row[columns["intensity"]])
            mz = _float_or_none(row[columns["mz"]])
            rt = _float_or_none(row[columns["rt"]])
            if intensity is None:
                table.notes.append(f"non-numeric intensity skipped: {feature_id}/{sample_id}")
                continue
            if feature_id not in seen_features:
                seen_features[feature_id] = len(table.feature_ids)
                table.feature_ids.append(feature_id)
                table.mz.append(mz if mz is not None else float("nan"))
                table.rt_min.append(rt if rt is not None else float("nan"))
            if sample_id not in seen_samples:
                seen_samples.append(sample_id)
                table.sample_ids.append(sample_id)
            table.intensities.setdefault(feature_id, {})[sample_id] = intensity
        if not table.feature_ids or not table.sample_ids:
            raise SkipAdaptation("long format parsed but no (feature, sample) intensity rows survived validation")
        return table

    # wide formats
    if tool == "mzmine3":
        columns = _require(header, ["feature_id", "mz", "rt"], "MZmine 3")
        tool_name = "MZmine 3"
        sample_start = max(columns.values()) + 1
        sample_start = _skip_metadata_columns(header, sample_start)
        rt_in_minutes = True
    elif tool == "msdial":
        columns = _require(header, ["feature_id", "mz", "rt"], "MS-DIAL")
        tool_name = "MS-DIAL"
        sample_start = max(columns.values()) + 1
        sample_start = _skip_metadata_columns(header, sample_start)
        rt_in_minutes = True
    else:
        columns = _require(header, ["mz", "rt"], "XCMS")
        tool_name = "XCMS"
        # xcms peaklists often lead with an unnamed id column
        sample_start = max(columns.values()) + 1
        rt_in_minutes = False
        if header[0] == "":
            columns["feature_id"] = 0

    sample_ids = [cell for cell in header[sample_start:] if cell]
    if not sample_ids:
        raise SkipAdaptation(
            f"{tool_name} schema detected but no sample columns follow the "
            "m/z and RT columns; a feature table without samples is not a feature table"
        )

    table = AdaptedTable(
        feature_ids=[],
        mz=[],
        rt_min=[],
        tool_name=tool_name,
        sample_ids=sample_ids,
        rt_tolerance_s=None,
        mz_tolerance_ppm=None,
    )
    if not rt_in_minutes:
        table.notes.append("XCMS rt column is in seconds; converted to minutes")
    generated = 0
    for row in data_rows:
        if len(row) <= max(columns.values()):
            continue
        mz = _float_or_none(row[columns["mz"]])
        rt = _float_or_none(row[columns["rt"]])
        if mz is None or rt is None:
            table.notes.append(f"row with non-numeric mz/rt skipped: {row[:3]}...")
            continue
        feature_id = (
            row[columns["feature_id"]] if "feature_id" in columns else ""
        )
        if not feature_id:
            generated += 1
            feature_id = f"FT{generated}"
            if generated == 1:
                table.notes.append("no id column found; ids generated as FT1..FTn")
        table.feature_ids.append(feature_id)
        table.mz.append(mz)
        table.rt_min.append(rt if rt_in_minutes else rt / 60.0)
        per_sample: dict[str, float] = {}
        for offset, sample_id in enumerate(sample_ids):
            index = sample_start + offset
            value = _float_or_none(row[index]) if index < len(row) else None
            if value is not None:
                per_sample[sample_id] = value
        table.intensities[feature_id] = per_sample
    if not table.feature_ids:
        raise SkipAdaptation(
            f"{tool_name} schema detected but no data row carried numeric mz and rt"
        )
    return table


def apply_normalisation(table: AdaptedTable, method: str) -> AdaptedTable:
    """Opt-in sample normalisation; pass-through unless requested."""
    if method == "none":
        return table
    if method not in ("tic", "sum"):
        raise SkipAdaptation(
            f"unknown normalisation {method!r}; expected 'none', 'tic' or 'sum'"
        )
    totals = {
        sample_id: sum(
            intensities.get(sample_id, 0.0) for intensities in table.intensities.values()
        )
        for sample_id in table.sample_ids
    }
    if method == "sum":
        factors = {
            sample_id: total if total != 0.0 else 1.0
            for sample_id, total in totals.items()
        }
    else:  # tic: rescale to the mean total, mirroring metabolomics normalize
        mean_total = sum(totals.values()) / max(len(totals), 1) or 1.0
        factors = {
            sample_id: (total / mean_total if total != 0.0 else 1.0)
            for sample_id, total in totals.items()
        }
    for feature_id, per_sample in table.intensities.items():
        table.intensities[feature_id] = {
            sample_id: value / factors[sample_id]
            for sample_id, value in per_sample.items()
        }
    table.notes.append(f"{method} normalisation applied on ingest (opt-in)")
    return table


def build_record(table: AdaptedTable, normalisation: str) -> PreprocessingRecord:
    return PreprocessingRecord(
        modality="lcms",
        tool_name=table.tool_name,
        tool_version=table.tool_version,
        normalisation_method=normalisation,
        rt_tolerance_s=table.rt_tolerance_s,
        mz_tolerance_ppm=table.mz_tolerance_ppm,
        sample_ids=table.sample_ids,
    )
