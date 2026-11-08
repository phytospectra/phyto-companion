"""Offline regression tests for the public model-comparison harness."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "benchmark/model_comparison/compare.py"
spec = importlib.util.spec_from_file_location("model_comparison", HARNESS)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)


@pytest.fixture
def config():
    return json.loads((HARNESS.parent / "example-config.json").read_text())


@pytest.fixture
def locked(tmp_path, config):
    lock = mc.create_lock(config)
    path = tmp_path / "lock.json"
    mc.write_new(path, lock)
    return path, lock


def response(cid="C001", candidate="cand-b", citations=None, reason=None):
    return json.dumps({"case_id": cid, "selected_candidate": candidate,
                       "cited_evidence": citations or [], "abstention_reason": reason})


def record(path, lock, arm="offline_replay_a", cid="C001", repeat=0, **overrides):
    a = next(a for a in lock["arms"] if a["id"] == arm)
    rec = {"arm": arm, "case_id": cid, "repeat": repeat,
           "lock_sha256": mc.file_digest(path), "input_sha256": lock["cases"][cid]["input_sha256"],
           "identity": mc.expected_identity(a),
           "settings": {**lock["settings"], "seed": lock["settings"]["seed"] + repeat},
           "status": "success", "response_text": response(cid), "input_tokens": 100,
           "output_tokens": 20, "cost_usd": 0.02, "cost_basis": "actual",
           "elapsed_seconds": 1.5, "error": None, "runtime": {"test": "synthetic"}}
    rec.update(overrides)
    return rec


def test_lock_freezes_payload_and_classifies_public(locked):
    path, lock = locked
    mc.verify_lock(lock)
    assert lock["classification"] == "public_regression_not_blinded"
    assert list(lock["cases"]) == ["C001", "C002", "C003", "C004"]
    assert lock["cases"]["C001"]["input_sha256"] != lock["cases"]["C002"]["input_sha256"]
    bad = copy.deepcopy(lock)
    bad["cases"]["C001"]["input_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        mc.verify_lock(bad)
    assert path.exists()


def test_packet_leakage_is_rejected_nested():
    packet = mc.load(mc.CASE_DIR / "C001.json")
    packet["task"]["candidates"][0]["answer_label"] = "cand-a"
    with pytest.raises(ValueError, match="candidate fields"):
        mc.validate_packet(packet, "C001")
    packet = mc.load(mc.CASE_DIR / "C001.json")
    packet["acquisition"]["note"] = "quercetin"
    with pytest.raises(ValueError, match="leak token"):
        mc.validate_packet(packet, "C001")


def test_scoring_citations_and_abstention_are_conservative():
    case = mc.load(mc.CASE_DIR / "C001.json")
    key = mc.load(mc.KEYS)[0]
    refs = [{"evidence_id": "E001"}, {"type": "HSQC", "dH": 6.19, "dC": 98.3},
            {"evidence_id": "E999", "type": "HSQC", "dH": 6.19, "dC": 98.3},
            {"evidence_id": "E001", "type": "HMBC"}, {"type": "HSQC", "dH": "bad", "dC": 98.3},
            {"evidence_id": "E006"}]
    scored = mc.score_response(response(citations=refs), case, key)
    assert [c["exists"] for c in scored["citations"]] == [True, True, False, False, False, False]
    assert scored["citations"][0]["supports_conclusion"] == "requires expert review"
    c4 = mc.load(mc.CASE_DIR / "C004.json")
    k4 = mc.load(mc.KEYS)[3]
    result = mc.score_response(response("C004", None, reason="anything"), c4, k4)
    assert result["abstention_with_reason"] is True
    assert result["missing_evidence_identified"] == "requires expert review"
    assert result["candidate_correct"] is None
    assert mc.score_response(response("C004", None, reason="  "), c4, k4)["abstention_with_reason"] is False


def test_invalid_responses_not_scored():
    case = mc.load(mc.CASE_DIR / "C001.json")
    key = mc.load(mc.KEYS)[0]
    for value in ('{}', response(candidate="invented"), response(citations=["E001"]),
                  response().replace('"C001"', '"C002"')):
        assert mc.score_response(value, case, key)["status"] == "malformed_response"


def test_missing_failure_duplicate_rejected_usage_and_pairing(tmp_path, locked):
    path, lock = locked
    first = record(path, lock)
    duplicate = record(path, lock, input_tokens=None, cost_usd=None, cost_basis="unknown")
    failed = record(path, lock, cid="C002", status="failure", response_text=None, error="failed",
                    input_tokens=None, output_tokens=None, cost_usd=None, cost_basis="unknown")
    bad = record(path, lock, cid="C003", input_sha256="0" * 64)
    malformed = record(path, lock, cid="C003", repeat=1, response_text="not json")
    other = record(path, lock, arm="offline_replay_b")
    records = tmp_path / "records.json"
    mc.write_new(records, [first, duplicate, failed, bad, malformed, other])
    report = mc.evaluate(path, [records])
    a = report["arms"][0]
    assert a["expected_slots"] == 8
    assert a["status_counts"] == {"scored": 0, "missing": 5, "duplicate_attempts": 1,
                                   "failure": 1, "malformed_response": 1}
    assert len(report["rejected_records"]) == 1
    usage = a["usage_all_valid_attempts_including_duplicates_and_failures"]
    assert usage["input_tokens"] == {"known_sum": 200, "known_attempts": 2, "unknown_attempts": 2}
    assert usage["cost_by_basis"]["unknown"]["attempts"] == 2
    assert report["paired_candidate_comparisons"][0]["matched_scored_sufficient_slots"] == 0
    assert report["paired_candidate_comparisons"][0]["left_minus_right"] is None


def test_pairing_repeats_and_identity_rejection(tmp_path, locked):
    path, lock = locked
    rows = [record(path, lock), record(path, lock, arm="offline_replay_b", response_text=response(candidate="cand-a")),
            record(path, lock, repeat=1), record(path, lock, arm="offline_replay_b", repeat=1),
            record(path, lock, cid="C002", identity={})]
    f = tmp_path / "records.json"
    mc.write_new(f, rows)
    report = mc.evaluate(path, [f])
    pair = report["paired_candidate_comparisons"][0]
    assert pair["matched_scored_sufficient_slots"] == 2
    assert pair["left_minus_right"] == .5
    assert len(report["rejected_records"]) == 1


def test_no_local_provider_and_hash_mismatch_without_network(tmp_path, config):
    artifact = tmp_path / "model.gguf"
    artifact.write_bytes(b"not a model")
    config["arms"][0].update(adapter="llama_cpp", artifact_path=str(artifact), artifact_sha256="0" * 64)
    lock = tmp_path / "lock.json"
    mc.write_new(lock, mc.create_lock(config))
    with pytest.raises(ValueError, match="artifact missing or hash mismatch"):
        mc.run_local(lock, "offline_replay_a")
    config["arms"][0]["artifact_sha256"] = mc.file_digest(artifact)
    lock2 = tmp_path / "lock2.json"
    mc.write_new(lock2, mc.create_lock(config))
    # Force unavailable package without opening any model or network connection.
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, "llama_cpp", None)
        with pytest.raises(ValueError, match="not installed"):
            mc.run_local(lock2, "offline_replay_a")


def test_cli_empty_runs_and_no_overwrite(tmp_path):
    config = HARNESS.parent / "example-config.json"
    lock = tmp_path / "lock.json"
    report = tmp_path / "report.json"
    for args in (["lock", "--config", str(config), "--output", str(lock)],
                 ["evaluate", "--lock", str(lock), "--output", str(report)]):
        result = subprocess.run([sys.executable, str(HARNESS), *args], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    result = mc.load(report)
    assert all(a["status_counts"]["missing"] == 8 for a in result["arms"])
    assert all(a["scores"]["candidate_accuracy_on_scored_sufficient_cases"]["value"] is None for a in result["arms"])
    repeat = subprocess.run([sys.executable, str(HARNESS), "evaluate", "--lock", str(lock),
                             "--output", str(report)], capture_output=True, text=True)
    assert repeat.returncode == 2
