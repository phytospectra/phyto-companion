"""All examples are SYNTHETIC software contracts; none are measured results."""
import copy
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Avoid reliance on an installed namespace package named benchmark.
PACKAGE = "paired_contract"
spec = importlib.util.spec_from_file_location(
    PACKAGE, ROOT / "benchmark/paired_hsqc/harness.py",
    submodule_search_locations=[str(ROOT / "benchmark/paired_hsqc")])
package = importlib.util.module_from_spec(spec)
sys.modules[PACKAGE] = package
spec.loader.exec_module(package)
from paired_contract import harness as h  # noqa: E402
from paired_contract import scorer as s  # noqa: E402
from paired_contract.synthetic import make_fixture  # noqa: E402


def overwrite(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def fixture(tmp_path):
    bundle, key = tmp_path / "bundle", tmp_path / "private-key.json"
    make_fixture(bundle, key)
    return bundle, key, tmp_path


def pipeline(fixture):
    bundle, key, directory = fixture
    lock, run, report = [directory / n for n in ("lock.json", "run.json", "report.json")]
    s.freeze(bundle, key, lock)
    h.run_predictions(bundle, lock, run)
    return s.score(bundle, lock, key, run, report), (lock, run, report)


def test_real_executor_and_determinism(fixture, monkeypatch):
    bundle, key, directory = fixture
    data = h.load_bundle(bundle)
    from orchestrator.real_executor import RealToolExecutor
    calls = []
    real = RealToolExecutor.execute

    def spy(self, name, inputs):
        calls.append((name, copy.deepcopy(inputs)))
        assert not {"truth_status", "acceptable_candidate_ids", "keys"} & set(inputs)
        return real(self, name, inputs)

    monkeypatch.setattr(RealToolExecutor, "execute", spy)
    lock = directory / "lock.json"
    s.freeze(bundle, key, lock)
    r1 = h.run_predictions(bundle, lock, directory / "one.json")
    key.unlink()  # Inference must work even when evaluator truth is unavailable.
    r2 = h.run_predictions(bundle, lock, directory / "two.json")
    assert r1["deterministic_sha256"] == r2["deterministic_sha256"]
    assert len(calls) == 2 * len(data["queries"])
    assert r1["records"][0]["arm"] == "A" and r1["records"][2]["arm"] == "B"
    assert "key" not in inspect.signature(h.run_predictions).parameters
    assert "truth" not in inspect.getsource(h.arm_a)


def test_denominators_absence_and_uncertainty(fixture):
    result, _ = pipeline(fixture)
    assert result["classification"] == "synthetic_contract"
    assert result["scientific_validation"] is False
    assert result["planned_test_cases"] == 8
    for arm in ("A", "B"):
        rates = result["rates"][arm]
        assert rates["supported_correct"]["denominator"] == 5
        assert rates["supported_coverage"]["denominator"] == 8
        assert rates["absent_abstention"]["denominator"] == 2
        assert rates["absent_abstention"]["numerator"] == 1
        assert rates["retrieval_recall"]["numerator"] == 4
        assert rates["reference_retrieval_recall"]["numerator"] == 3
        assert rates["supported_wrong"]["numerator"] == 2  # absent and ambiguous unique preference
    assert result["paired"]["present_denominator"] == 5
    assert result["paired"]["cluster_bootstrap"]["groups"] == 2
    assert result["paired"]["exact_mcnemar_p"] is None
    rows = {r["case_id"]: r for r in result["per_case"]}
    assert rows["missing-reference"]["present_reference_missing"]
    assert rows["ambiguous-contract"]["arms"]["A"]["supported_correct"] is False


def test_meaningful_no_assignment_baseline(fixture):
    data = h.load_bundle(fixture[0])
    # Three distinct nearby queries reuse one reference in B; A can match only one.
    case = {"formula": "C2H6O", "peaks": [
        {"h_ppm": 1. + delta, "c_ppm": 20.} for delta in (0., .001, .002)]}
    a, b = h.arm_a(data["executor"], case), h.arm_b(data["executor"], case)
    assert a["disposition"] == "insufficient_evidence"
    assert b["disposition"] == "ranked"
    assert a["hits"][0]["matched_query_count"] == 1
    assert b["hits"][0]["matched_query_count"] == 3
    # Inherited unequal-count normalization: not silently corrected.
    assert a["hits"][0]["distance"] == pytest.approx(2.)
    assert b["hits"][0]["distance"] < .1


def test_ties_missing_and_multiple_references(fixture):
    bundle, _, _ = fixture
    refs = h.read(bundle / "references.json")
    refs[1]["peaks"] = refs[0]["peaks"]
    refs.append({**refs[0], "reference_id": "SYNTHETIC-alpha-second"})
    overwrite(bundle / "references.json", refs)
    data = h.load_bundle(bundle)
    case = next(c for c in data["queries"] if c["case_id"] == "present")
    for arm in (h.arm_a, h.arm_b):
        out = arm(data["executor"], case)
        assert out["disposition"] == "ambiguous"
        assert out["ranking"] == ["alpha", "beta"]
        assert len(out["hits"]) == 3
    refs.pop(1)
    overwrite(bundle / "references.json", refs)
    data = h.load_bundle(bundle)
    for arm in (h.arm_a, h.arm_b):
        assert arm(data["executor"], case)["disposition"] == "insufficient_evidence"


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "nan", "bool", "empty", "formula", "partition"])
def test_malformed_queries(fixture, mutation):
    bundle, _, _ = fixture
    queries = h.read(bundle / "queries.json")
    if mutation == "unknown":
        queries[0]["truth_status"] = "present"
    elif mutation == "duplicate":
        queries.append(queries[0])
    elif mutation == "nan":
        queries[0]["peaks"][0]["h_ppm"] = float("nan")
    elif mutation == "bool":
        queries[0]["peaks"][0]["h_ppm"] = True
    elif mutation == "empty":
        queries[0]["peaks"] = []
    elif mutation == "formula":
        queries[0]["formula"] = "C2H6O+"
    else:
        queries[0]["partition"] = "validation"
    overwrite(bundle / "queries.json", queries)
    with pytest.raises(ValueError):
        h.load_bundle(bundle)


@pytest.mark.parametrize("mutation", ["group", "overlap", "missing", "absent", "foreign", "duplicate"])
def test_invalid_keys(fixture, mutation):
    bundle, key, directory = fixture
    keys = h.read(key)
    if mutation == "group":
        keys["cases"][1]["partition"] = "dev"
    elif mutation == "overlap":
        keys["cases"][0]["query_source_id"] = "SYNTHETIC-reference-alpha"
    elif mutation == "missing":
        keys["cases"].pop()
    elif mutation == "absent":
        keys["cases"][0]["truth_status"] = "absent"
    elif mutation == "foreign":
        keys["cases"][0]["acceptable_candidate_ids"] = ["foreign"]
    else:
        keys["cases"].append(keys["cases"][0])
    overwrite(key, keys)
    with pytest.raises(ValueError):
        s.freeze(bundle, key, directory / "lock.json")


@pytest.mark.parametrize("kind", ["missing", "duplicate", "malformed", "exception", "timeout", "foreign-hit"])
def test_failure_slots_keep_paired_denominator(fixture, kind):
    _, paths = pipeline(fixture)
    lock, run_path, _ = paths
    bundle, key, directory = fixture
    run = h.read(run_path)
    i = next(i for i, r in enumerate(run["records"]) if r["case_id"] == "present" and r["arm"] == "A")
    record = run["records"][i]
    if kind == "missing":
        run["records"].pop(i)
    elif kind == "duplicate":
        run["records"].append(copy.deepcopy(record))
    elif kind == "malformed":
        record.pop("input_sha256")
    elif kind == "foreign-hit":
        record["prediction"]["hits"][0]["candidate_id"] = "foreign"
    else:
        record.update(status=kind, prediction=None)
    overwrite(run_path, run)
    result = s.score(bundle, lock, key, run_path, directory / "failure.json")
    expected = "malformed" if kind == "foreign-hit" else kind
    assert result["statuses"]["A"][expected] == 1
    assert result["rates"]["A"]["failure"]["numerator"] == 1
    assert result["paired"]["present_denominator"] == 5
    assert result["paired"]["matched_success_denominator"] == 4
    assert result["rates"]["A"]["supported_correct"]["denominator"] == 5


def test_drift_overwrite_and_foreign_run(fixture):
    bundle, key, directory = fixture
    _, (lock, run, _) = pipeline(fixture)
    with pytest.raises(FileExistsError):
        s.freeze(bundle, key, lock)
    records = h.read(run)
    records["lock_id"] = "foreign"
    overwrite(run, records)
    with pytest.raises(ValueError):
        s.score(bundle, lock, key, run, directory / "foreign.json")
    (bundle / "structures.csv").write_text((bundle / "structures.csv").read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        h.run_predictions(bundle, lock, directory / "drift.json")


def test_strict_json_and_limits(fixture):
    bundle, _, directory = fixture
    path = directory / "bad.json"
    path.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="duplicate"):
        h.read(path)
    refs = h.read(bundle / "references.json")
    overwrite(bundle / "references.json", [{**refs[0], "reference_id": str(i)} for i in range(101)])
    with pytest.raises(ValueError, match="truncation"):
        h.load_bundle(bundle)


def test_no_private_key_paths_and_cli_no_key_option(fixture):
    bundle, key, directory = fixture
    _, (lock, run, _) = pipeline(fixture)
    for path in (lock, run):
        content = path.read_text()
        assert str(key) not in content
        assert "acceptable_candidate_ids" not in content
        assert "truth_status" not in content
    with pytest.raises(ValueError, match="outside"):
        h.external(ROOT / "private-key.json")
    command = subprocess.run([sys.executable, str(ROOT / "benchmark/paired_hsqc/evaluate.py"),
                              "run", "--bundle", str(bundle), "--lock", str(lock),
                              "--output", str(directory / "never.json"), "--keys", str(key)],
                             capture_output=True, text=True)
    assert command.returncode == 2
    assert not (directory / "never.json").exists()


def test_scientific_fails_closed(fixture):
    bundle, key, directory = fixture
    manifest, keys = h.read(bundle / "manifest.json"), h.read(key)
    manifest["classification"] = "independent_measured"
    keys["evidence"]["classification"] = "independent_measured"
    manifest["review_artifact_sha256"] = h.digest(keys["evidence"])
    overwrite(bundle / "manifest.json", manifest)
    overwrite(key, keys)
    with pytest.raises(ValueError, match="evidence coverage"):
        s.freeze(bundle, key, directory / "not-approved.json")
    assert not (directory / "not-approved.json").exists()


def evidence_contract(fixture):
    # Invented attestations exercise schema logic ONLY; never scientific evidence.
    bundle, key, _ = fixture
    keys = h.read(key)
    keys["evidence"]["classification"] = "independent_measured"
    sources = {}
    for row in keys["cases"]:
        row["independence_group"] = "SYNTHETIC-family" if row["acceptable_candidate_ids"] else row["independence_group"]
        row["provenance_review"] = dict.fromkeys(row["provenance_review"], True)
        sources[row["query_source_id"]] = "query"
        sources.update({sid: "reference" for sid in row["reference_source_ids"]})
    records = []
    for sid, role in sorted(sources.items()):
        records.append({"source_id": sid, "role": role, "acquisition_id": "SYNTHETIC-" + sid,
                        "raw_sha256": h.digest(sid), "rights_verified": True,
                        "independently_acquired": True, "external_adjudication": "SYNTHETIC-NOT-REAL",
                        "metadata": dict.fromkeys(("solvent", "temperature", "pH", "field", "referencing",
                                                   "processing", "peak_picking", "assignment_provenance"),
                                                  "SYNTHETIC software contract, not evidence")})
    keys["evidence"]["records"] = records
    data = h.load_bundle(bundle)
    data["manifest"] = {"classification": "independent_measured",
                        "review_artifact_sha256": h.digest(keys["evidence"])}
    return data, keys


@pytest.mark.parametrize("mutation", ["rights", "acquisition", "raw", "metadata", "adjudication",
                                      "source", "split", "compound", "flags", "digest"])
def test_evidence_gate_contracts(fixture, mutation):
    data, keys = evidence_contract(fixture)
    s.validate_keys(data, keys)  # fabricated schema contract, not a scientific run
    records = keys["evidence"]["records"]
    q = next(r for r in records if r["role"] == "query")
    r = next(r for r in records if r["role"] == "reference")
    if mutation == "rights":
        q["rights_verified"] = False
    elif mutation == "acquisition":
        q["acquisition_id"] = r["acquisition_id"]
    elif mutation == "raw":
        q["raw_sha256"] = r["raw_sha256"]
    elif mutation == "metadata":
        q["metadata"].pop("field")
    elif mutation == "adjudication":
        q["external_adjudication"] = ""
    elif mutation == "source":
        records.remove(q)
    elif mutation == "split":
        keys["cases"][0]["partition"] = "dev"
        keys["cases"][0]["independence_group"] = "different"
        next(c for c in data["queries"] if c["case_id"] == keys["cases"][0]["case_id"])["partition"] = "dev"
    elif mutation == "compound":
        keys["cases"][0]["independence_group"] = "other-compound"
    elif mutation == "flags":
        keys["cases"][0]["provenance_review"]["identity_verified"] = False
    else:
        data["manifest"]["review_artifact_sha256"] = "0" * 64
    if mutation != "digest":
        data["manifest"]["review_artifact_sha256"] = h.digest(keys["evidence"])
    with pytest.raises(ValueError):
        s.validate_keys(data, keys)


def test_execution_exception_and_timeout(fixture, monkeypatch):
    bundle, key, directory = fixture
    lock = directory / "lock.json"
    s.freeze(bundle, key, lock)

    def broken(*args):
        raise RuntimeError("SYNTHETIC private detail must not leak")

    monkeypatch.setattr(h, "arm_a", broken)
    clock = iter(range(0, 100000, 100))
    monkeypatch.setattr(h.time, "perf_counter", lambda: next(clock))
    run = h.run_predictions(bundle, lock, directory / "timeout.json")
    assert all(r["status"] == "timeout" for r in run["records"])
    assert all(r["prediction"] is None for r in run["records"])
    assert "private detail" not in json.dumps(run)


def test_archive_revision_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "ROOT", tmp_path)
    monkeypatch.delenv("PHYTO_SOURCE_REVISION", raising=False)
    with pytest.raises(ValueError, match="archive requires"):
        h.source_revision()
    monkeypatch.setenv("PHYTO_SOURCE_REVISION", "not-a-revision")
    with pytest.raises(ValueError, match="40-hex"):
        h.source_revision()
    monkeypatch.setenv("PHYTO_SOURCE_REVISION", "A" * 40)
    assert h.source_revision() == "a" * 40
    sidecar = tmp_path / ".source-revision"
    sidecar.write_text("B" * 40)
    monkeypatch.delenv("PHYTO_SOURCE_REVISION")
    assert h.source_revision() == "b" * 40
    monkeypatch.setenv("PHYTO_SOURCE_REVISION", "C" * 40)
    with pytest.raises(ValueError, match="disagrees"):
        h.source_revision()
    sidecar.write_text("not-a-revision")
    monkeypatch.delenv("PHYTO_SOURCE_REVISION")
    with pytest.raises(ValueError, match="40-hex"):
        h.source_revision()
    monkeypatch.setattr(h, "ROOT", ROOT)
    real = h.source_revision()
    monkeypatch.setenv("PHYTO_SOURCE_REVISION", real)
    assert h.source_revision() == real
    monkeypatch.setenv("PHYTO_SOURCE_REVISION", "0" * 40)
    with pytest.raises(ValueError, match="disagrees"):
        h.source_revision()


def test_cluster_and_empty_rates():
    rows = [{"group": "one", "A": True, "B": False},
            {"group": "one", "A": False, "B": False},
            {"group": "two", "A": False, "B": True}]
    a = s.cluster_interval(rows)
    assert a == s.cluster_interval(rows)
    assert a["groups"] == 2 and a["difference"] == 0
    assert s.rate([])["value"] is None
    assert s.cluster_interval([])["ci95"] is None
    assert s.rate([False] * 50)["wilson95"][1] > .06
