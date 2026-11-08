"""Synthetic-only acceptance runner; no measured accuracy claims."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_synthetic_evaluation_is_deterministic_and_explicit():
    path = ROOT / "benchmark" / "reference_hsqc_acceptance" / "run_evaluation.py"
    spec = importlib.util.spec_from_file_location("reference_contract_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.run()
    assert first == module.run()
    assert first["case_count"] == first["contract_pass_count"] == 6
    assert first["measured_query_count"] == first["independent_validation_count"] == 0
    assert first["evidence_class"] == "SYNTHETIC SOFTWARE CONTRACT ONLY"
    assert all(case["status"] == "ok" and case["contract_pass"] for case in first["per_case"])
