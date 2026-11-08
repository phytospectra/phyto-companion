#!/usr/bin/env python
"""Deterministic SYNTHETIC contract evaluation, never measured validation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def bootstrap() -> None:
    sys.path.insert(0, str(ROOT / "shared"))
    for name in ("elucidation", "orchestrator"):
        directory = ROOT / "services" / name / "app"
        spec = importlib.util.spec_from_file_location(
            name, directory / "__init__.py", submodule_search_locations=[str(directory)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def run() -> dict:
    bootstrap()
    from rdkit import Chem
    from orchestrator.real_executor import RealToolExecutor
    from orchestrator.tool_registry import build_default_registry

    base = [(1.0, 20.0), (2.0, 40.0), (3.0, 60.0), (4.0, 80.0)]
    reference_data = []
    for name, smiles, coordinates in (
        ("ethanol", "CCO", base),
        ("dimethyl-ether", "COC", [(h + 0.4, c + 4) for h, c in base]),
    ):
        reference_data.append({
            "reference_id": "SYNTHETIC-" + name, "candidate_id": name,
            "inchikey": Chem.MolToInchiKey(Chem.MolFromSmiles(smiles)),
            "source": "Invented numeric algorithm fixture; not measured chemistry",
            "solvent": "SYNTHETIC/no acquisition",
            "license": "Test-generated numeric values; no source dataset",
            "provenance": "experimental", "peaks": [
                {"h_ppm": h, "c_ppm": c} for h, c in coordinates
            ],
        })
    cases = [
        ("clean", base, "C2H6O", "ranked"),
        ("small_shift", [(h + .01, c + .1) for h, c in base], "C2H6O", "ranked"),
        ("sparse", base[:1], "C2H6O", "insufficient_evidence"),
        ("duplicates", [base[0]] * 4, "C2H6O", "insufficient_evidence"),
        ("absent_formula", base, "C99H100", "no_hits"),
        ("no_overlap", [(10., 200.), (11., 210.), (12., 220.)], "C2H6O", "insufficient_evidence"),
    ]
    rows = []
    with tempfile.TemporaryDirectory(prefix="phyto-contract-") as temporary:
        directory = Path(temporary)
        structures = directory / "structures.csv"
        references = directory / "references.json"
        structures.write_text(
            "name,smiles,provenance,source\n"
            "ethanol,CCO,synthetic,SYNTHETIC fixture\n"
            "dimethyl ether,COC,synthetic,SYNTHETIC fixture\n"
        )
        references.write_text(json.dumps(reference_data))
        executor = RealToolExecutor(build_default_registry(), structures, references)
        for case_id, coordinates, formula, expected in cases:
            inputs = {"formula": formula, "peaks": [
                {"peak_type": "2d", "h_ppm": h, "c_ppm": c,
                 "intensity": 1., "experiment_type": "HSQC"}
                for h, c in coordinates
            ]}
            try:
                output = executor.execute("reference_hsqc.rank", inputs).model_dump(mode="json")
                disposition = output["evidence_assessment"]["disposition"]
                rows.append({"case_id": case_id, "status": "ok",
                             "input_sha256": digest(inputs), "output_sha256": digest(output),
                             "expected": expected, "actual": disposition,
                             "contract_pass": disposition == expected,
                             "output": output})
            except Exception as exc:
                rows.append({"case_id": case_id, "status": "error",
                             "input_sha256": digest(inputs), "contract_pass": False,
                             "error_type": type(exc).__name__})
    supplied = os.environ.get("PHYTO_SOURCE_REVISION", "").lower()
    if supplied and not re.fullmatch(r"[0-9a-f]{40}", supplied):
        raise ValueError("PHYTO_SOURCE_REVISION must be a full 40-character commit SHA")
    detected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True)
    revision = detected.stdout.strip() if detected.returncode == 0 else supplied
    # Source archives created by the reproducibility procedure carry the
    # exact revision in a sidecar file instead of a Git object store.
    sidecar = ROOT / ".source-revision"
    if not revision and sidecar.is_file():
        revision = sidecar.read_text().strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(".source-revision must contain a full 40-character commit SHA")
    if not revision:
        raise ValueError("Source archives require explicit PHYTO_SOURCE_REVISION provenance")
    if supplied and supplied != revision:
        raise ValueError("PHYTO_SOURCE_REVISION does not match checkout HEAD")
    return {"evidence_class": "SYNTHETIC SOFTWARE CONTRACT ONLY",
            "code_revision": revision,
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "protocol_sha256": hashlib.sha256(Path(__file__).with_name("PROTOCOL.md").read_bytes()).hexdigest(),
            "case_count": len(rows), "contract_pass_count": sum(row["contract_pass"] for row in rows),
            "measured_query_count": 0, "independent_validation_count": 0,
            "limitations": ["Invented coordinates, not plausible ethanol/ether spectra.",
                            "experimental runtime literal is schema-required curator assertion, not a measurement claim.",
                            "No accuracy, sensitivity or scientific decision-rule validation follows."],
            "per_case": rows}


if __name__ == "__main__":
    result = run()
    Path(__file__).with_name("RESULTS.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Synthetic contracts: {result['contract_pass_count']}/{result['case_count']}; measured validation: 0")
    sys.exit(0 if result["contract_pass_count"] == result["case_count"] else 1)
