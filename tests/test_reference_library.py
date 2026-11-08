"""Synthetic-only builder contract tests; no measured or rights-validated data."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from rdkit import Chem

REPO = Path(__file__).resolve().parents[1]
LIBRARY = REPO / "data/reference_libraries/literature_flavonoids_v1"
COLUMNS = (LIBRARY / "source/manifest.template.csv").read_text().strip().split(",")


def _input(tmp_path, **overrides):
    source = tmp_path / "source"
    source.mkdir()
    (source / "invented.csv").write_text("position,dH,dC\nA,1.2,18\nB,3.6,58\n")
    (source / "raw.txt").write_text("invented raw data")
    row = dict(name="Synthetic ethanol", smiles="CCO", solvent="invented",
               source="Invented fixture; not measured", license="synthetic test data",
               table="invented.csv", expected_inchikey=Chem.MolToInchiKey(Chem.MolFromSmiles("CCO")),
               declared_formula="C2H6O", evidence_nature="synthetic_contract_test",
               source_accession_url="https://example.org/synthetic-record",
               identity_source_url="https://example.org/synthetic-identity",
               rights_evidence_url="https://example.org/synthetic-rights",
               redistribution_assertion="cleared", source_acquisition_id="synthetic-acquisition-1",
               measurement_status="measured", measurement_evidence_url="https://example.org/measurement",
               assignment_evidence_url="https://example.org/assignment", acquisition_conditions="invented",
               rights_scope="synthetic only", original_file="raw.txt",
               original_sha256=hashlib.sha256((source / "raw.txt").read_bytes()).hexdigest(),
               transformation="invented table") | overrides
    with (source / "manifest.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(row)
    return source


def _run(source, output, *args):
    return subprocess.run([sys.executable, str(REPO / "scripts/build_reference_library.py"),
                           "--source", str(source), "--output", str(output), *args],
                          capture_output=True, text=True)


def test_no_bundled_spectra_or_usable_library():
    assert json.loads((LIBRARY / "QUARANTINE.json").read_text())["status"] == "quarantined_not_distributable"
    assert not (LIBRARY / "references.json").exists()
    assert not list((LIBRARY / "source").glob("*_hsqc*.csv"))


def test_synthetic_build_is_deterministic_and_records_assertions(tmp_path):
    source = _input(tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    assert _run(source, one, "--strict").returncode == 0
    assert _run(source, two, "--strict").returncode == 0
    for name in ("structures.csv", "references.json", "MANIFEST.json"):
        assert (one / name).read_bytes() == (two / name).read_bytes()
    manifest = json.loads((one / "MANIFEST.json").read_text())
    assert manifest["records_imported"] == 1
    assert manifest["record_evidence"]["synthetic-ethanol"]["redistribution_assertion"] == "cleared"
    assert "not verified" in manifest["notes"]


@pytest.mark.parametrize("changes,reason", [
    ({"redistribution_assertion": ""}, "redistribution not explicitly cleared"),
    ({"redistribution_assertion": "yes"}, "redistribution not explicitly cleared"),
    ({"source_accession_url": ""}, "stable HTTPS"),
    ({"identity_source_url": "file:///tmp/identity"}, "stable HTTPS"),
    ({"rights_evidence_url": ""}, "stable HTTPS"),
    ({"measurement_evidence_url": ""}, "stable HTTPS"),
    ({"assignment_evidence_url": ""}, "stable HTTPS"),
    ({"measurement_status": "predicted"}, "measurement_status"),
    ({"source_acquisition_id": ""}, "acquisition ID"),
    ({"acquisition_conditions": ""}, "acquisition ID"),
    ({"rights_scope": ""}, "acquisition ID"),
    ({"transformation": ""}, "acquisition ID"),
    ({"original_file": "../outside.csv"}, "original file must be within"),
    ({"original_sha256": "0" * 64}, "original file SHA-256"),
    ({"expected_inchikey": ""}, "expected InChIKey"),
    ({"expected_inchikey": "REFJWTPEDVJJIY-UHFFFAOYSA-N"}, "expected_inchikey"),
    ({"declared_formula": "C15H10O7"}, "declared formula"),
    ({"table": "../outside.csv"}, "within source directory"),
])
def test_missing_or_conflicting_evidence_quarantines(tmp_path, changes, reason):
    source = _input(tmp_path, **changes)
    out = tmp_path / "out"
    result = _run(source, out, "--strict")
    assert result.returncode == 1
    assert reason in result.stdout
    assert json.loads((out / "references.json").read_text()) == []


def test_quercetin_identity_mismatch_quarantined(tmp_path):
    source = _input(tmp_path, name="Quercetin",
                    smiles="O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12",
                    expected_inchikey="REFJWTPEDVJJIY-UHFFFAOYSA-N", declared_formula="C15H10O7")
    out = tmp_path / "out"
    assert _run(source, out, "--strict").returncode == 1
    assert "ISJMMWDSNQVANM-UHFFFAOYSA-N" in json.loads((out / "MANIFEST.json").read_text())["per_record"][0]["reason"]
    assert json.loads((out / "references.json").read_text()) == []


def test_legacy_blanket_license_schema_fails_closed(tmp_path):
    source = _input(tmp_path)
    (source / "manifest.csv").write_text("name,smiles,solvent,source,license,table\nA,CCO,x,x,CC-BY-NC,invented.csv\n")
    result = _run(source, tmp_path / "out")
    assert result.returncode != 0
    assert "missing columns" in result.stderr


def test_damaged_peak_table_quarantined(tmp_path):
    source = _input(tmp_path)
    (source / "invented.csv").write_text("position,dH,dC\nA,1.2,18\nB,1.2,18\n")
    result = _run(source, tmp_path / "out", "--strict")
    assert result.returncode == 1
    assert "duplicate cross-peak coordinates" in result.stdout
