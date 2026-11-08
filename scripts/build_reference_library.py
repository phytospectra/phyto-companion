#!/usr/bin/env python
"""Build an executor-format HSQC reference library from curated source tables.

Reproducible import/validation pipeline for ``RealToolExecutor`` libraries
(ADR chain: measured-reference dereprecation support). Inputs are a source
directory containing ``manifest.csv`` plus one peak table per compound; the
output is a ``structures.csv`` + ``references.json`` pair loadable via
``PHYTO_STRUCTURE_DB_PATH`` / ``PHYTO_HSQC_REFERENCE_LIBRARY``, plus a
``MANIFEST.json`` recording per-record status and content hashes.

Design rules:
- Deterministic: identical inputs produce byte-identical outputs (no
  timestamps, no environment data). Reference IDs are content-addressed.
- Fail-transparent: a record that fails validation is QUARANTINED with its
  reason and excluded; nothing is silently repaired or dropped. ``--strict``
  turns any quarantine into a nonzero exit.
- Scientific honesty: provenance semantics are the executor's. Structure rows
  are ``structure_only``; reference ``provenance: experimental`` is a curator
  assertion the software cannot verify -- the manifest records the evidence
  nature (e.g. literature_hand_curated) separately.

Usage:
    python scripts/build_reference_library.py \
        --source data/reference_libraries/<name>/source \
        --output data/reference_libraries/<name> [--strict]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlparse

from rdkit import Chem, rdBase
from rdkit.Chem import rdMolDescriptors

H_RANGE = (-5.0, 15.0)
C_RANGE = (-50.0, 300.0)
MAX_PEAKS = 256
MANIFEST_COLUMNS = (
    "name",
    "smiles",
    "solvent",
    "source",
    "license",
    "table",
    "expected_inchikey",
    "declared_formula",
    "evidence_nature",
    "source_accession_url",
    "identity_source_url",
    "rights_evidence_url",
    "redistribution_assertion",
)
BUILDER_VERSION = "2"


def _https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _candidate_id(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _read_table(path: Path) -> tuple[list[dict[str, float]], str | None]:
    """Parse a ``position,dH,dC`` table; return (peaks, declared_formula).

    Comment lines beginning with ``#`` are skipped; a comment containing
    ``formula <FORMULA>`` records the table's declared molecular formula.
    """
    peaks: list[dict[str, float]] = []
    declared: str | None = None
    seen_positions: set[str] = set()
    seen_coordinates: set[tuple[float, float]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        lines = [
            line for line in handle.read().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    for comment in path.read_text(encoding="utf-8").splitlines():
        if comment.lstrip().startswith("#") and "formula" in comment:
            token = comment.split("formula", 1)[1].strip().split()
            if token:
                declared = token[0].strip(",;.:()")
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or not {
        "position", "dH", "dC"
    }.issubset(set(reader.fieldnames)):
        raise ValueError(f"{path.name}: expected columns position,dH,dC")
    for row in reader:
        position = (row.get("position") or "").strip()
        h_text, c_text = (row.get("dH") or "").strip(), (row.get("dC") or "").strip()
        if not position or not h_text or not c_text:
            raise ValueError(f"{path.name}: empty position/dH/dC cell")
        if position in seen_positions:
            raise ValueError(f"{path.name}: duplicate position label {position!r}")
        seen_positions.add(position)
        try:
            h, c = float(h_text), float(c_text)
        except ValueError as exc:
            raise ValueError(f"{path.name}: non-numeric shift for {position}") from exc
        if not (math.isfinite(h) and math.isfinite(c)):
            raise ValueError(f"{path.name}: non-finite shift for {position}")
        if not H_RANGE[0] <= h <= H_RANGE[1]:
            raise ValueError(f"{path.name}: {position} dH {h} outside {H_RANGE}")
        if not C_RANGE[0] <= c <= C_RANGE[1]:
            raise ValueError(f"{path.name}: {position} dC {c} outside {C_RANGE}")
        if (h, c) in seen_coordinates:
            raise ValueError(
                f"{path.name}: duplicate cross-peak coordinates ({h}, {c}) -- "
                "ambiguous assignment, refusing to guess"
            )
        seen_coordinates.add((h, c))
        peaks.append({"h_ppm": h, "c_ppm": c})
    if not 1 <= len(peaks) <= MAX_PEAKS:
        raise ValueError(f"{path.name}: peak count {len(peaks)} outside 1..{MAX_PEAKS}")
    return peaks, declared


def build(source: Path, output: Path, strict: bool = False) -> int:
    manifest_path = source / "manifest.csv"
    if not manifest_path.is_file():
        raise SystemExit(f"missing {manifest_path}")
    output.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    quarantined: list[dict] = []
    inchikeys: dict[str, str] = {}

    with manifest_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(MANIFEST_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"manifest.csv missing columns: {sorted(missing)}")
        rows = list(reader)

    for row in rows:
        name = (row.get("name") or "").strip()
        table_name = (row.get("table") or "").strip()
        entry = {
            "name": name or "(unnamed)",
            "table": table_name,
            "status": "",
            "reason": "",
        }
        table_path = source / table_name if table_name else None

        def quarantine(reason: str) -> None:
            entry["status"] = "quarantined"
            entry["reason"] = reason
            quarantined.append(entry)

        if not name:
            quarantine("missing compound name")
            continue
        candidate_id = _candidate_id(name)
        if any(r["candidate_id"] == candidate_id for r in records):
            quarantine(f"duplicate normalized candidate id {candidate_id!r}")
            continue
        if (row.get("redistribution_assertion") or "").strip() != "cleared":
            quarantine("redistribution not explicitly cleared for this record")
            continue
        urls = {key: (row.get(key) or "").strip() for key in
                ("source_accession_url", "identity_source_url", "rights_evidence_url")}
        if not all(_https_url(value) for value in urls.values()):
            quarantine("stable HTTPS source accession, identity source, and record-specific rights evidence are required")
            continue
        if not all((row.get(key) or "").strip() for key in
                   ("expected_inchikey", "declared_formula", "evidence_nature")):
            quarantine("expected InChIKey, declared formula and evidence nature are required")
            continue
        if table_path is not None and not table_path.resolve().is_relative_to(source.resolve()):
            quarantine("peak table must be within source directory")
            continue
        if table_path is None or not table_path.is_file():
            quarantine(f"peak table {table_name!r} not found")
            continue
        smiles = (row.get("smiles") or "").strip()
        if not smiles:
            quarantine(
                "no verified structure available for this record "
                "(blank SMILES); literature shifts without a resolvable "
                "structure identity cannot be attached to a candidate"
            )
            continue
        if len(smiles) > 4096:
            quarantine("SMILES exceeds 4096 characters")
            continue
        with rdBase.BlockLogs():
            mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            quarantine("SMILES does not parse with RDKit")
            continue
        inchikey = Chem.MolToInchiKey(mol)
        if not inchikey:
            quarantine("RDKit produced no InChIKey")
            continue
        expected = (row.get("expected_inchikey") or "").strip()
        if expected and expected != inchikey:
            quarantine(
                f"expected_inchikey {expected} != RDKit InChIKey {inchikey}"
            )
            continue
        if inchikey in inchikeys:
            quarantine(
                f"duplicate structure: same InChIKey as {inchikeys[inchikey]!r}"
            )
            continue
        try:
            peaks, declared_formula = _read_table(table_path)
        except ValueError as exc:
            quarantine(str(exc))
            continue
        formula = rdMolDescriptors.CalcMolFormula(mol)
        manifest_formula = row["declared_formula"].strip()
        if manifest_formula != formula or (declared_formula and declared_formula != formula):
            quarantine(
                f"declared formula disagrees with structure formula {formula}"
            )
            continue
        solvent = (row.get("solvent") or "").strip()
        source_text = (row.get("source") or "").strip()
        license_text = (row.get("license") or "").strip()
        if not (solvent and source_text and license_text):
            quarantine("solvent, source and license are all required")
            continue
        combined_source = f"{source_text} | accession: {urls['source_accession_url']} | identity: {urls['identity_source_url']}"
        combined_license = f"{license_text} | rights evidence: {urls['rights_evidence_url']}"
        if (len(candidate_id) > 200 or len(solvent) > 200 or len(combined_source) > 2000
                or len(combined_license) > 1000 or len(candidate_id) + 13 > 200):
            quarantine("record metadata exceeds executor schema limits")
            continue
        table_digest = _sha256_bytes(table_path.read_bytes())
        reference_id = f"{candidate_id}-{table_digest[:12]}"
        records.append(
            {
                "candidate_id": candidate_id,
                "name": name,
                "table": table_name,
                "smiles": smiles,
                "inchikey": inchikey,
                "formula": formula,
                "reference_id": reference_id,
                "solvent": solvent,
                "source": source_text,
                "license": license_text,
                "peaks": peaks,
                "evidence_nature": row["evidence_nature"].strip(),
                "source_accession_url": urls["source_accession_url"],
                "identity_source_url": urls["identity_source_url"],
                "rights_evidence_url": urls["rights_evidence_url"],
                "redistribution_assertion": "cleared",
                "table_sha256": table_digest,
            }
        )
        inchikeys[inchikey] = name
        entry["status"] = "imported"
        entry["reason"] = ""

    statuses = [
        {"name": r["name"], "table": r["table"], "status": "imported", "reason": ""}
        for r in records
    ] + [q for q in quarantined if q["status"] == "quarantined"]

    structures_path = output / "structures.csv"
    references_path = output / "references.json"
    with structures_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["name", "smiles", "provenance", "source"]
        )
        writer.writeheader()
        for record in sorted(records, key=lambda r: r["candidate_id"]):
            writer.writerow(
                {
                    "name": record["name"],
                    "smiles": record["smiles"],
                    # Structure rows carry identity only: the measured claim
                    # lives in the reference record, not the structure row.
                    "provenance": "structure_only",
                    "source": record["source"],
                }
            )
    references = [
        {
            "reference_id": record["reference_id"],
            "candidate_id": record["candidate_id"],
            "inchikey": record["inchikey"],
            "source": f"{record['source']} | accession: {record['source_accession_url']} | identity: {record['identity_source_url']}",
            "solvent": record["solvent"],
            "license": f"{record['license']} | rights evidence: {record['rights_evidence_url']}",
            "provenance": "experimental",  # curator assertion; see MANIFEST
            "peaks": record["peaks"],
        }
        for record in sorted(records, key=lambda r: r["reference_id"])
    ]
    references_path.write_text(
        json.dumps(references, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_out = {
        "builder_version": BUILDER_VERSION,
        "records_imported": len(records),
        "records_quarantined": len([q for q in quarantined if q["status"] == "quarantined"]),
        "per_record": statuses,
        "evidence_nature": {
            record["candidate_id"]: record["evidence_nature"] or "unspecified"
            for record in records
        },
        "record_evidence": {
            record["candidate_id"]: {
                key: record[key] for key in (
                    "source_accession_url", "identity_source_url", "rights_evidence_url",
                    "redistribution_assertion", "table_sha256"
                )
            }
            for record in records
        },
        "input_hashes": {record["table"]: record["table_sha256"] for record in records},
        "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        "output_hashes": {
            "structures.csv": _sha256_bytes(structures_path.read_bytes()),
            "references.json": _sha256_bytes(references_path.read_bytes()),
        },
        "notes": (
            "reference provenance 'experimental' is a curator assertion, not "
            "software-verified measurement; rights and identity evidence URLs and "
            "redistribution clearance are curator assertions, not verified by software"
        ),
    }
    (output / "MANIFEST.json").write_text(
        json.dumps(manifest_out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for item in statuses:
        print(f"{item['status']:12s} {item['name']}: {item['reason'] or 'ok'}")
    print(
        f"imported={manifest_out['records_imported']} "
        f"quarantined={manifest_out['records_quarantined']} -> {output}"
    )
    if strict and manifest_out["records_quarantined"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--strict", action="store_true",
        help="exit nonzero if any record is quarantined",
    )
    args = parser.parse_args(argv)
    return build(args.source, args.output, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
