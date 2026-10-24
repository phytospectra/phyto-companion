#!/usr/bin/env python3
"""Read-only inventory of a local NMR data folder.

Safety rules baked in:
  * The source tree is NEVER written to, renamed, moved, or extracted.
  * Google Drive / cloud placeholder files (logical size > 0, zero
    allocated blocks) are detected and their CONTENTS ARE NEVER READ --
    opening them would trigger a large network download.
  * All file scanning is streamed; no file is loaded fully into memory.

Usage:
    python scripts/inventory_dataset.py \
        --root "/path/to/My Drive/NMR-Data" \
        --out data/inventory/dataset_inventory.json
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCANNABLE = {".csv", ".tsv", ".mgf", ".mzml", ".mzxml", ".json", ".txt", ".sd"}
BRUKER_MARKERS = ("acqus", "fid", "pdata")
VENDOR_DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
NMR_EXT = {".mnova", ".jdf", ".jda", ".fid", ".1r", ".2rr", ".ser"}
ARCHIVE_EXT = {".zip", ".tar", ".tgz", ".gz", ".7z", ".rar"}
STRUCT_EXT = {".sdf", ".sd", ".smi", ".mol", ".mol2"}
MODEL_EXT = {".pt", ".pth", ".onnx", ".h5", ".pkl", ".joblib"}
PARQUET_EXT = {".parquet", ".feather", ".arrow"}

# Peaks in a Bruker acqus file look like:  ##$PULPROG= <h2>zg30</h2>
ACQUS_PARAM = re.compile(r"##\$(?P<name>[A-Za-z0-9_]+)=\s*(?:<(?P<type>\w+)>\s*)?(?P<value>[^\n<]*)")

PLACEHOLDER_MIN_BYTES = 1_000_000  # never read placeholder contents above this


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------


def allocated_blocks(path: Path) -> int:
    st = os.stat(path)
    return st.st_blocks


def is_cloud_placeholder(path: Path, size: int) -> bool:
    """True for files whose bytes are not on disk (Drive/FileStream).

    A materialized file occupies at least a few blocks per MiB; a
    placeholder occupies zero blocks while reporting a non-zero logical
    size.  Zero-byte files are genuinely empty, not placeholders.
    """
    if size <= 0:
        return False
    return allocated_blocks(path) == 0


def stream_line_count(path: Path, limit_bytes: int = 512 * 1024 * 1024) -> int:
    """Count newline-terminated lines without loading the file."""
    count = 0
    read = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            read += len(block)
            count += block.count(b"\n")
            if read >= limit_bytes:
                break
    return count


def stream_gz_line_count(path: Path, limit_lines: int = 2_000_000) -> int:
    count = 0
    with gzip.open(path, "rb") as handle:
        for _ in handle:
            count += 1
            if count >= limit_lines:
                break
    return count


def sha256_head(path: Path, max_bytes: int = 4 * 1024 * 1024) -> str:
    """Hash of the first ``max_bytes`` (bounded work; full hashes are in
    the integration step, not the inventory)."""
    digest = hashlib.sha256()
    remaining = max_bytes
    with path.open("rb") as handle:
        while remaining > 0:
            block = handle.read(min(1 << 20, remaining))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def sniff_magic(path: Path) -> str:
    """Identify archive/document magic bytes (cheap, first 8 bytes)."""
    with path.open("rb") as handle:
        head = handle.read(8)
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"<?xml"):
        return "xml"
    return "unknown"


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def parse_acqus(acqus_path: Path) -> dict[str, Any]:
    """Extract the handful of parameters the spec asks for."""
    wanted = {"PULPROG", "NUC1", "SOLVENT", "SFO1", "BF1", "TD", "NS", "TE"}
    values: dict[str, Any] = {}
    try:
        text = acqus_path.read_text(errors="replace")
    except OSError:
        return values
    for match in ACQUS_PARAM.finditer(text):
        name = match.group("name")
        if name in wanted:
            raw = match.group("value").strip()
            values[name] = float(raw) if name in ("SFO1", "BF1") and raw.replace(".", "", 1).isdigit() else raw
    return values


def find_bruker_context(path: Path) -> dict[str, Any] | None:
    """Detect a Bruker experiment by 'acqus'/'fid' in the file's parent."""
    parent = path.parent
    acqus = parent / "acqus"
    fid = parent / "fid"
    pdata = parent / "pdata"
    if not (acqus.exists() or fid.exists() or pdata.exists()):
        return None
    info: dict[str, Any] = {
        "has_acqus": acqus.exists(),
        "has_fid": fid.exists(),
        "has_pdata": pdata.exists() and (pdata / "1").is_dir(),
    }
    if acqus.exists():
        info.update(parse_acqus(acqus))
    expno = parent.name
    sample_dir = parent.parent
    info["experiment_number"] = expno
    info["experiment_folder"] = str(parent)
    info["sample_folder"] = str(sample_dir)
    return info


def classify_experiment(acqus: dict[str, Any] | None, path: Path) -> str:
    """Map a Bruker pulse program / nucleus to a coarse experiment type."""
    if acqus:
        pulprog = str(acqus.get("PULPROG", "")).lower()
        nuc1 = str(acqus.get("NUC1", "")).lower()
        # 2D first: pulse programs are more specific than nuclei
        if "hsqc" in pulprog:
            return "HSQC"
        if "hmbc" in pulprog:
            return "HMBC"
        if "cosy" in pulprog:
            return "COSY"
        if "tocsy" in pulprog:
            return "TOCSY"
        if "noesy" in pulprog or "roesy" in pulprog:
            return "NOESY"
        if "jres" in pulprog:
            return "JRES"
        if "13c" in nuc1 or "13c" in pulprog:
            return "13C"
        if "1h" in nuc1 or "1h" in pulprog:
            return "1H"
    name = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in {".mgf", ".mzml", ".mzxml"}:
        return "MS"
    if "hsqc" in name:
        return "HSQC"
    if "hmbc" in name:
        return "HMBC"
    if "cosy" in name:
        return "COSY"
    if "1h" in name or "proton" in name:
        return "1H"
    if "13c" in name or "carbon" in name:
        return "13C"
    if "ir" in suffix or "infrared" in name:
        return "IR"
    return "other"


def detect_format(path: Path, parent_has_acqus: bool) -> str:
    suffix = path.suffix.lower()
    if parent_has_acqus:
        return "bruker"
    if path.name in ("procpar",):
        return "varian"
    if path.name == "fid" and not parent_has_acqus:
        return "varian"
    if suffix in (".jdf", ".jda"):
        return "jeol"
    if suffix == ".mnova":
        return "mestrenova"
    if suffix in (".mzml", ".mzxml"):
        return "mzml_or_mzxml"
    if suffix == ".mgf":
        return "mgf"
    if suffix in (".csv", ".tsv"):
        return "csv_tsv"
    if suffix in VENDOR_DOC_EXT:
        return "document"
    if suffix in ARCHIVE_EXT:
        return "archive"
    if suffix in STRUCT_EXT:
        return "structure"
    if suffix in MODEL_EXT:
        return "model"
    if suffix in PARQUET_EXT:
        return "columnar"
    if suffix in NMR_EXT:
        return "nmr_other"
    return "other"


# ---------------------------------------------------------------------------
# Walk (read-only)
# ---------------------------------------------------------------------------


def iter_files(root: Path) -> Iterator[Path]:
    """os.walk with followlinks=False (as specified); no writes."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # never descend into symlinked dirs or our own output area
        dirnames[:] = [
            name for name in dirnames
            if not os.path.islink(os.path.join(dirpath, name))
        ]
        for filename in filenames:
            yield Path(dirpath) / filename


def inventory_file(path: Path, root: Path) -> dict[str, Any]:
    st = path.stat()
    size = st.st_size
    placeholder = is_cloud_placeholder(path, size)
    acqus = find_bruker_context(path)
    fmt = detect_format(path, parent_has_acqus=bool(acqus and acqus.get("has_acqus")))
    record: dict[str, Any] = {
        "path": str(path.relative_to(root)),
        "size_bytes": size,
        "extension": path.suffix.lower() or "(none)",
        "format": fmt,
        "cloud_placeholder": placeholder,
        "modified_utc": datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
    }
    if acqus:
        record["bruker"] = {
            key: acqus.get(key)
            for key in (
                "PULPROG", "NUC1", "SOLVENT", "SFO1", "BF1",
                "has_acqus", "has_fid", "has_pdata",
                "experiment_folder", "sample_folder",
            )
            if key in acqus
        }
    record["experiment_type"] = classify_experiment(acqus, path)

    # Content inspection: ONLY for materialized files the spec asks about.
    suffix = path.suffix.lower()
    is_readable_content = not placeholder and size > 0
    if is_readable_content and suffix in (".csv", ".tsv", ".mgf", ".mzml", ".mzxml", ".txt"):
        try:
            record["line_count"] = stream_line_count(path)
        except OSError as exc:
            record["content_error"] = str(exc)
    elif is_readable_content and suffix == ".gz":
        # only decompress modest .gz text files (never multi-MB archives)
        if size <= 20_000_000:
            try:
                record["line_count"] = stream_gz_line_count(path)
            except (OSError, EOFError) as exc:
                record["content_error"] = str(exc)
    if is_readable_content and suffix in ARCHIVE_EXT | VENDOR_DOC_EXT | {".pt", ".parquet"}:
        try:
            record["magic"] = sniff_magic(path)
        except OSError as exc:
            record["content_error"] = str(exc)
    if placeholder:
        record["content_not_read"] = (
            "cloud placeholder (0 allocated blocks): contents not read to "
            "avoid triggering a network download"
        )
    elif size >= PLACEHOLDER_MIN_BYTES and suffix in SCANNABLE | ARCHIVE_EXT:
        try:
            record["sha256_head"] = sha256_head(path)
        except OSError as exc:
            record["content_error"] = str(exc)
    return record


# ---------------------------------------------------------------------------
# Dataset grouping
# ---------------------------------------------------------------------------


def group_datasets(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group by top-level folder; files at the root go to '_root'."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        parts = Path(record["path"]).parts
        groups[parts[0] if len(parts) > 1 else "_root"].append(record)
    return dict(groups)


def build_summary(root: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    total_size = sum(r["size_bytes"] for r in records)
    local_size = sum(
        r["size_bytes"] for r in records if not r["cloud_placeholder"]
    )
    placeholder_size = total_size - local_size
    formats = Counter(r["format"] for r in records)
    experiment_types = Counter(r["experiment_type"] for r in records)
    groups = group_datasets(records)

    datasets = []
    for name, files in sorted(groups.items()):
        size = sum(f["size_bytes"] for f in files)
        local = sum(f["size_bytes"] for f in files if not f["cloud_placeholder"])
        datasets.append(
            {
                "dataset": name,
                "path": "" if name == "_root" else f"{name}/",
                "file_count": len(files),
                "size_bytes": size,
                "local_size_bytes": local,
                "cloud_placeholder_bytes": size - local,
                "formats": dict(Counter(f["format"] for f in files)),
                "experiment_types": dict(
                    Counter(f["experiment_type"] for f in files)
                ),
                "has_bruker": any("bruker" in f for f in files),
                "has_processed": any(
                    f.get("bruker", {}).get("has_pdata") for f in files
                ),
                "has_raw": any(f.get("bruker", {}).get("has_fid") for f in files),
            }
        )
    return {
        "root": str(root),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "file_count": len(records),
            "total_size_bytes": total_size,
            "total_size_gb": round(total_size / 1e9, 3),
            "materialized_size_bytes": local_size,
            "cloud_placeholder_size_bytes": placeholder_size,
            "dataset_count": len(groups),
            "empty_directory_count": 0,  # filled by the walker below
        },
        "format_breakdown": dict(formats.most_common()),
        "experiment_type_breakdown": dict(experiment_types.most_common()),
        "datasets": sorted(datasets, key=lambda d: -d["size_bytes"]),
        "bruker_processed_count": sum(
            1 for r in records if r.get("bruker", {}).get("has_pdata")
        ),
        "bruker_raw_only_count": sum(
            1
            for r in records
            if r.get("bruker", {}).get("has_fid")
            and not r.get("bruker", {}).get("has_pdata")
        ),
    }


def count_empty_dirs(root: Path) -> int:
    empty = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if not dirnames and not filenames:
            empty += 1
    return empty


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def build_flags(records: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    if not any("bruker" in r for r in records):
        flags.append(
            {
                "level": "high",
                "code": "no_bruker_experiments",
                "message": (
                    "No Bruker acqus/fid/pdata markers anywhere in the tree: "
                    "this folder contains no uncompressed Bruker NMR "
                    "experiments."
                ),
            }
        )
    placeholders = [r for r in records if r["cloud_placeholder"]]
    if placeholders:
        flags.append(
            {
                "level": "high",
                "code": "cloud_placeholders",
                "message": (
                    f"{len(placeholders)} file(s) are cloud placeholders "
                    f"({round(sum(r['size_bytes'] for r in placeholders)/1e9, 2)} GB "
                    "logical). Their contents were NOT read; materialize them "
                    "before any ingestion or validation."
                ),
                "files": [r["path"] for r in placeholders],
            }
        )
    small = [
        r for r in records
        if 0 < r["size_bytes"] < 1024 and r["extension"] not in (".md",)
    ]
    if small:
        flags.append(
            {
                "level": "low",
                "code": "suspiciously_small_files",
                "message": f"{len(small)} file(s) under 1 KB",
                "files": [r["path"] for r in small],
            }
        )
    zero = [r for r in records if r["size_bytes"] == 0]
    if zero:
        flags.append(
            {
                "level": "medium",
                "code": "zero_byte_files",
                "message": f"{len(zero)} file(s) are zero bytes (truncated or failed downloads)",
                "files": [r["path"] for r in zero],
            }
        )
    archives = [r for r in records if r["format"] == "archive"]
    if archives:
        flags.append(
            {
                "level": "medium",
                "code": "unexpanded_archives",
                "message": (
                    f"{len(archives)} archive(s) were recorded but NOT expanded: "
                    + ", ".join(
                        f"{r['path']} ({round(r['size_bytes']/1e6, 1)} MB"
                        + (", cloud placeholder" if r["cloud_placeholder"] else "")
                        + ")"
                        for r in sorted(archives, key=lambda x: -x["size_bytes"])[:6]
                    )
                ),
            }
        )
    mnova = [r for r in records if r["format"] == "mestrenova"]
    if mnova:
        flags.append(
            {
                "level": "low",
                "code": "mestrenova_files",
                "message": f"{len(mnova)} .mnova file(s) recorded (proprietary; not parsed)",
            }
        )
    # PII scan on filenames/folders (constraint 4): emails, phone-like runs
    pii_pattern = re.compile(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|\+?\d[\d\s().-]{8,}\d"
    )
    pii_hits = sorted(
        {
            m.group(0)
            for r in records
            for m in pii_pattern.finditer(r["path"])
        }
    )
    if pii_hits:
        flags.append(
            {
                "level": "high",
                "code": "pii_in_paths",
                "message": (
                    "Personally identifiable information detected in relative "
                    "paths (redact before committing anything): "
                    + ", ".join(pii_hits[:5])
                ),
                "matches_redacted": [
                    re.sub(r"(?<=.).(?=[^@]*@)", "*", h) if "@" in h else "***"
                    for h in pii_hits[:5]
                ],
            }
        )
    top_level_dirs = {
        Path(r["path"]).parts[0] for r in records if len(Path(r["path"]).parts) > 1
    }
    if len(top_level_dirs) > 1:
        flags.append(
            {
                "level": "medium",
                "code": "multi_dataset_assumption",
                "message": (
                    "The 'one dataset per top-level folder' assumption holds "
                    f"only at the top level: {sorted(top_level_dirs)} are "
                    "heterogeneous collections (database dumps, model files, "
                    "benchmark outputs, not per-sample experiments)."
                ),
            }
        )
    return flags


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_summary(summary: dict[str, Any], flags: list[dict[str, Any]]) -> None:
    totals = summary["totals"]
    print("=" * 78)
    print("NMR DATA INVENTORY (read-only)")
    print("=" * 78)
    print(f"Root: {summary['root']}")
    print(f"Files: {totals['file_count']}   Datasets: {totals['dataset_count']}   "
          f"Empty dirs: {totals['empty_directory_count']}")
    print(
        f"Total logical size: {totals['total_size_bytes']:,} bytes "
        f"({totals['total_size_gb']} GB)"
    )
    print(
        f"  materialized locally: {totals['materialized_size_bytes']:,} bytes "
        f"({totals['materialized_size_bytes']/1e6:.1f} MB)"
    )
    print(
        f"  cloud placeholders:   {totals['cloud_placeholder_size_bytes']:,} bytes "
        f"({totals['cloud_placeholder_size_bytes']/1e9:.2f} GB, NOT read)"
    )
    print(f"Bruker processed experiments: {summary['bruker_processed_count']}")
    print(f"Bruker raw-only experiments:  {summary['bruker_raw_only_count']}")
    print()
    print("Format breakdown:")
    for fmt, count in summary["format_breakdown"].items():
        print(f"  {fmt:16s} {count}")
    print()
    print("Experiment-type breakdown:")
    for exp, count in summary["experiment_type_breakdown"].items():
        print(f"  {exp:16s} {count}")
    print()
    print("Top 20 datasets by size:")
    print(f"  {'dataset':28s} {'files':>6s} {'size MB':>10s} {'local MB':>10s}")
    for dataset in summary["datasets"][:20]:
        print(
            f"  {dataset['dataset']:28s} {dataset['file_count']:6d} "
            f"{dataset['size_bytes']/1e6:10.1f} {dataset['local_size_bytes']/1e6:10.1f}"
        )
    print()
    if flags:
        print("Flags:")
        for flag in flags:
            print(f"  [{flag['level'].upper():6s}] {flag['code']}: {flag['message']}")
    else:
        print("Flags: none")
    print("=" * 78)


def integrity_snapshot(root: Path) -> dict[str, Any]:
    """Content-level fingerprint of the source tree.

    Deliberately does NOT use allocated blocks: on cloud-backed file
    systems (Google Drive / FileStream) simply READING a dataless file
    makes the sync agent hydrate it, so block counts drift without any
    content change.  The authoritative signals are file count, total
    logical bytes, and an mtime set -- none of which a read can alter.
    """
    files = list(iter_files(root))
    counts: dict[str, int] = {}
    total = 0
    for path in files:
        st = path.stat()
        total += st.st_size
        counts[path.relative_to(root).as_posix()] = int(st.st_mtime)
    return {
        "file_count": len(files),
        "total_bytes": total,
        "mtime_set_sha256": hashlib.sha256(
            json.dumps(counts, sort_keys=True).encode()
        ).hexdigest(),
        "note": (
            "allocated block counts are excluded: reading a cloud placeholder "
            "hydrates it, so blocks drift with no content change"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="data root (read-only)")
    parser.add_argument(
        "--out",
        default="data/inventory/dataset_inventory.json",
        help="JSON report output path (inside the repo)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 2

    # Baseline integrity snapshot (before)
    before = integrity_snapshot(root)

    records = [inventory_file(path, root) for path in iter_files(root)]
    summary = build_summary(root, records)
    summary["totals"]["empty_directory_count"] = count_empty_dirs(root)
    flags = build_flags(records, root)

    # Integrity snapshot (after) -- content-level signals must be identical
    after = integrity_snapshot(root)
    integrity = {
        "before": before,
        "after": after,
        "unchanged": (
            before["file_count"] == after["file_count"]
            and before["total_bytes"] == after["total_bytes"]
            and before["mtime_set_sha256"] == after["mtime_set_sha256"]
        ),
    }

    report = {
        **summary,
        "integrity_check": integrity,
        "flags": flags,
        "files": sorted(records, key=lambda r: r["path"]),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print_summary(report, flags)
    print(f"\nReport written: {out_path} ({out_path.stat().st_size:,} bytes)")
    print(
        "Source-tree integrity: "
        + ("UNCHANGED" if integrity["unchanged"] else "CHANGED (investigate!)")
    )
    return 0 if integrity["unchanged"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
