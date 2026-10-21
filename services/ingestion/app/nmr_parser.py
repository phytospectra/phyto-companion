"""NMR peak list and spectrum parsers."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Union

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Peak1D, Peak2D, Spectrum
from phyto_shared.provenance import sha256_bytes, sha256_file

__all__ = [
    "parse_hsqc_csv",
    "parse_peak_1d_csv",
    "parse_peak_csv",
]


def _open_csv_reader(source: Union[str, Path, bytes]) -> tuple[csv.DictReader, str | None]:
    provenance_hash = None
    if isinstance(source, Path):
        path = source
        provenance_hash = sha256_file(path)
        text = path.read_text(encoding="utf-8", errors="replace")
    elif isinstance(source, bytes):
        provenance_hash = sha256_bytes(source)
        text = source.decode("utf-8", errors="replace")
    elif isinstance(source, str):
        if "\n" not in source and chr(10) not in source and Path(source).is_file():
            path = Path(source)
            provenance_hash = sha256_file(path)
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            text = source
            provenance_hash = sha256_bytes(text.encode("utf-8"))
    else:
        text = str(source)
        provenance_hash = sha256_bytes(text.encode("utf-8"))

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise InvalidInputError("CSV peak list is empty or has no header", context={})
    return reader, provenance_hash


def parse_hsqc_csv(source: Union[str, Path, bytes], spectrum_id: str = "hsqc_spectrum") -> Spectrum:
    """Parse an HSQC peak list CSV into a validated Spectrum model.

    Supports headers:
    - position, dH, dC (NP-MRD literature format)
    - h_ppm, c_ppm [, intensity] (standard format)
    - 1H, 13C [, intensity]
    """
    reader, prov_hash = _open_csv_reader(source)
    field_map = {f.strip().lower(): f for f in reader.fieldnames}

    h_col = None
    c_col = None
    pos_col = None
    int_col = None

    for candidate in ["dh", "h_ppm", "1h", "h"]:
        if candidate in field_map:
            h_col = field_map[candidate]
            break
    for candidate in ["dc", "c_ppm", "13c", "c"]:
        if candidate in field_map:
            c_col = field_map[candidate]
            break
    for candidate in ["position", "atom", "assignment", "pos"]:
        if candidate in field_map:
            pos_col = field_map[candidate]
            break
    for candidate in ["intensity", "int", "height", "area"]:
        if candidate in field_map:
            int_col = field_map[candidate]
            break

    if not h_col or not c_col:
        raise InvalidInputError(
            "Could not identify 1H and 13C columns in HSQC CSV",
            context={"columns": list(reader.fieldnames)},
        )

    peaks: list[Peak2D] = []
    positions: list[str] = []
    for line_num, row in enumerate(reader, start=2):
        try:
            h_val = float(row[h_col])
            c_val = float(row[c_col])
            intensity = float(row[int_col]) if int_col and row.get(int_col) else 1.0
            peak = Peak2D(
                h_ppm=h_val,
                c_ppm=c_val,
                intensity=intensity,
                experiment_type="HSQC",
            )
            peaks.append(peak)
            if pos_col and row.get(pos_col):
                positions.append(row[pos_col].strip())
        except (ValueError, TypeError) as e:
            raise InvalidInputError(
                f"Invalid float in row {line_num}",
                context={"row": row, "error": str(e)},
            ) from e

    metadata: dict[str, Any] = {"spectrum_id": spectrum_id, "peak_count": len(peaks)}
    if positions:
        metadata["assigned_positions"] = ",".join(positions)

    return Spectrum(
        kind="HSQC",
        data=peaks,
        metadata=metadata,
        provenance_hash=prov_hash,
    )


def parse_peak_1d_csv(
    source: Union[str, Path, bytes], kind: str = "1H", spectrum_id: str = "1d_spectrum"
) -> Spectrum:
    """Parse a 1D (1H or 13C) peak list CSV."""
    reader, prov_hash = _open_csv_reader(source)
    field_map = {f.strip().lower(): f for f in reader.fieldnames}

    ppm_col = None
    for candidate in ["ppm", "shift", "delta", "chemical_shift", "dh", "dc"]:
        if candidate in field_map:
            ppm_col = field_map[candidate]
            break

    if not ppm_col:
        raise InvalidInputError(
            "Could not identify shift/ppm column in 1D CSV",
            context={"columns": list(reader.fieldnames)},
        )

    int_col = None
    for candidate in ["intensity", "int", "height", "area"]:
        if candidate in field_map:
            int_col = field_map[candidate]
            break

    peaks: list[Peak1D] = []
    for line_num, row in enumerate(reader, start=2):
        try:
            ppm_val = float(row[ppm_col])
            intensity = float(row[int_col]) if int_col and row.get(int_col) else 1.0
            peaks.append(Peak1D(ppm=ppm_val, intensity=intensity))
        except (ValueError, TypeError) as e:
            raise InvalidInputError(
                f"Invalid float in row {line_num}",
                context={"row": row, "error": str(e)},
            ) from e

    return Spectrum(
        kind=kind,  # type: ignore[arg-type]
        data=peaks,
        metadata={"spectrum_id": spectrum_id, "peak_count": len(peaks)},
        provenance_hash=prov_hash,
    )


def parse_peak_csv(source: Union[str, Path, bytes]) -> Spectrum:
    """Auto-detect whether CSV is 1D or 2D HSQC and parse accordingly."""
    reader, _ = _open_csv_reader(source)
    headers = {f.strip().lower() for f in reader.fieldnames}
    is_2d = bool({"dh", "dc"} & headers or ({"h_ppm", "c_ppm"} <= headers))
    if is_2d:
        return parse_hsqc_csv(source)
    return parse_peak_1d_csv(source)
