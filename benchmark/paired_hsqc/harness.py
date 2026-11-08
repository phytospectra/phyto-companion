"""Key-free deterministic paired HSQC inference. No network or provider calls."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CONFIG = {"h_tolerance_ppm": .05, "c_tolerance_ppm": .5, "cap": 4.,
          "max_results": 100, "min_distinct": 3, "min_coverage": .5,
          "gap": .25, "timeout_seconds": 60., "top_k": 3,
          "bootstrap_seed": 20261109, "bootstrap_replicates": 10000}
LABEL = "SYNTHETIC SOFTWARE CONTRACT ONLY; NOT MEASURED RESULTS"


def bootstrap():
    if str(ROOT / "shared") not in sys.path:
        sys.path.insert(0, str(ROOT / "shared"))
    for name in ("elucidation", "orchestrator"):
        if name in sys.modules:
            continue
        directory = ROOT / "services" / name / "app"
        spec = importlib.util.spec_from_file_location(
            name, directory / "__init__.py", submodule_search_locations=[str(directory)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, required):
    require(isinstance(value, dict) and set(value) == set(required), "invalid object fields")


def text(value):
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 2000,
            "invalid identifier/text")
    return value


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read(path):
    data = Path(path).read_bytes()
    require(len(data) <= 8 * 1024 * 1024, "JSON size limit")
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, "nonfinite JSON"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    # Exclusive create: no overwriting a lock, predictions, report or private key.
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def external(path):
    path = Path(path).resolve()
    require(not path.is_relative_to(ROOT), "private/scientific artifacts must be outside repository")
    return path


def source_revision():
    supplied = os.environ.get("PHYTO_SOURCE_REVISION")
    if supplied is not None:
        require(re.fullmatch(r"[0-9a-fA-F]{40}", supplied) is not None,
                "PHYTO_SOURCE_REVISION must be a full 40-hex source revision")
        supplied = supplied.lower()
    # Do not accidentally inherit a parent directory's unrelated repository.
    if (ROOT / ".git").exists():
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip().lower()
        require(re.fullmatch(r"[0-9a-f]{40}", git) is not None, "invalid git HEAD revision")
        require(supplied is None or supplied == git, "source revision disagrees with git HEAD")
        return git
    # Source archives carry the exact revision in a sidecar file.
    sidecar = ROOT / ".source-revision"
    if sidecar.is_file():
        recorded = sidecar.read_text().strip().lower()
        require(re.fullmatch(r"[0-9a-f]{40}", recorded) is not None,
                ".source-revision must contain a full 40-hex source revision")
        require(supplied is None or supplied == recorded,
                "source revision disagrees with archive .source-revision")
        return recorded
    require(supplied is not None, "source archive requires PHYTO_SOURCE_REVISION")
    return supplied


def code_state():
    paths = list(HERE.glob("*.py")) + [HERE / "PROTOCOL.md"]
    for relative in ("services/orchestrator/app", "services/elucidation/app", "shared/phyto_shared"):
        paths.extend((ROOT / relative).rglob("*.py"))
    # Source hashes bind dirty work as well as HEAD; runtime/dependency hashes are also checked.
    return {"revision": source_revision(),
            "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}}


def inputs(case):
    return {"formula": case["formula"], "max_results": CONFIG["max_results"],
            "h_tolerance_ppm": CONFIG["h_tolerance_ppm"],
            "c_tolerance_ppm": CONFIG["c_tolerance_ppm"],
            "peaks": [{"peak_type": "2d", **p, "intensity": 1., "experiment_type": "HSQC"}
                      for p in case["peaks"]]}


def load_bundle(bundle):
    bootstrap()
    from orchestrator.real_executor import RealToolExecutor, ReferenceHSQCInput, _formula_counts
    from orchestrator.tool_registry import build_default_registry
    bundle = Path(bundle).resolve()
    manifest = read(bundle / "manifest.json")
    fields(manifest, ("classification", "review_artifact_sha256"))
    require(manifest["classification"] in ("synthetic_contract", "independent_measured"),
            "invalid classification")
    review_hash = manifest["review_artifact_sha256"]
    require(isinstance(review_hash, str) and len(review_hash) == 64
            and all(c in "0123456789abcdef" for c in review_hash), "invalid evidence digest")
    if manifest["classification"] == "independent_measured":
        external(bundle)
    queries = read(bundle / "queries.json")
    require(isinstance(queries, list) and 1 <= len(queries) <= 10000, "invalid queries")
    ids = set()
    for case in queries:
        fields(case, ("case_id", "formula", "peaks", "partition"))
        text(case["case_id"])
        require(case["case_id"] not in ids, "duplicate case")
        ids.add(case["case_id"])
        require(case["partition"] in ("dev", "test"), "invalid partition")
        require(isinstance(case["peaks"], list), "invalid peaks")
        for peak in case["peaks"]:
            fields(peak, ("h_ppm", "c_ppm"))
            for axis, low, high in (("h_ppm", -5, 15), ("c_ppm", -50, 300)):
                v = peak[axis]
                require(type(v) in (int, float) and math.isfinite(v) and low <= v <= high,
                        "invalid peak coordinate")
        ReferenceHSQCInput.model_validate(inputs(case))
    executor = RealToolExecutor(build_default_registry(), bundle / "structures.csv",
                                bundle / "references.json")
    retrieval = {}
    for case in queries:
        candidates = sorted(executor._db.candidates_for_formula(_formula_counts(case["formula"])),
                            key=lambda c: c.id)
        refs = {c.id: sorted(r.reference_id for r in executor._references.get(c.id, []))
                for c in candidates}
        require(sum(map(len, refs.values())) <= 100, "reference truncation forbidden")
        retrieval[case["case_id"]] = refs
    hashes = {name: sha(bundle / name) for name in
              ("manifest.json", "queries.json", "structures.csv", "references.json")}
    return {"manifest": manifest, "queries": sorted(queries, key=lambda c: c["case_id"]),
            "executor": executor, "retrieval": retrieval, "hashes": hashes}


def snapshot(data):
    return {"bundle_sha256": data["hashes"], "retrieval": data["retrieval"],
            "config": CONFIG, "code": code_state(),
            "provider": data["executor"].provenance("reference_hsqc.rank"),
            "environment": {"python": platform.python_version(), "platform": platform.platform(),
                            "machine": platform.machine(), "processor": platform.processor()}}


def validate_lock(data, lock):
    fields(lock, ("schema", "status", "classification", "snapshot", "key_sha256", "lock_id"))
    require(lock["schema"] == 1 and lock["status"] == "review_lock_pending",
            "unsupported lock/review state")
    require(lock["classification"] == data["manifest"]["classification"], "classification mismatch")
    require(lock["lock_id"] == digest({k: v for k, v in lock.items() if k != "lock_id"}),
            "lock digest mismatch")
    require(lock["snapshot"] == snapshot(data), "frozen inputs/code/environment changed")


def nearest_cost(query, reference):
    dh, dc = abs(query[0] - reference[0]), abs(query[1] - reference[1])
    if dh > .05 or dc > .5:
        return 4.
    return 4. * min(math.sqrt((dh / .05) ** 2 + (dc / .5) ** 2) / math.sqrt(2), 1.)


def arm_a(executor, case):
    # The production executor itself performs exact formula retrieval and real matching.
    output = executor.execute("reference_hsqc.rank", inputs(case)).model_dump(mode="json")
    hits = []
    observed = [(p["h_ppm"], p["c_ppm"]) for p in case["peaks"]]
    for hit in output["hits"]:
        result = hit["matcher_result"]
        matched = [a for a in result["assignments"] if a["cost"] < 4]
        hits.append({"candidate_id": hit["candidate"]["id"], "reference_id": hit["reference_id"],
                     "distance": result["score"], "matched_query_count": len(matched),
                     "distinct_matched": len({observed[a["observed_index"]] for a in matched}),
                     "reference_peak_count": result["n_predicted"]})
    return summarize(hits, output["unavailable_references"],
                     output["evidence_assessment"]["disposition"], len(observed))


def summarize(hits, missing, disposition, count):
    ranking = list(dict.fromkeys(h["candidate_id"] for h in hits))
    return {"ranking": ranking, "disposition": disposition,
            "unavailable_references": sorted(missing), "hits": hits, "query_peak_count": count}


def arm_b(executor, case):
    from orchestrator.real_executor import _formula_counts
    observed = [(p["h_ppm"], p["c_ppm"]) for p in case["peaks"]]
    candidates = sorted(executor._db.candidates_for_formula(_formula_counts(case["formula"])),
                        key=lambda c: c.id)
    hits, missing = [], []
    for candidate in candidates:
        refs = executor._references.get(candidate.id, [])
        if not refs:
            missing.append(candidate.id)
        for ref in refs:
            costs = [min(nearest_cost(q, (r.h_ppm, r.c_ppm)) for r in ref.peaks)
                     for q in observed]
            hits.append({"candidate_id": candidate.id, "reference_id": ref.reference_id,
                         "distance": sum(costs) / len(costs),
                         "matched_query_count": sum(c < 4 for c in costs),
                         "distinct_matched": len({q for q, c in zip(observed, costs) if c < 4}),
                         "reference_peak_count": len(ref.peaks)})
    hits.sort(key=lambda h: (h["distance"], h["candidate_id"], h["reference_id"]))
    disposition = "no_hits"
    if hits:
        top = hits[0]
        runner = next((h for h in hits if h["candidate_id"] != top["candidate_id"]), None)
        if missing or top["distinct_matched"] < 3 or round(top["matched_query_count"] / len(observed), 6) < .5:
            disposition = "insufficient_evidence"
        elif runner and round(runner["distance"] - top["distance"], 6) < .25:
            disposition = "ambiguous"
        else:
            disposition = "ranked"
    return summarize(hits, missing, disposition, len(observed))


def run_predictions(bundle, lock_path, output):
    started = time.perf_counter()
    data, lock = load_bundle(bundle), read(lock_path)
    validate_lock(data, lock)
    if lock["classification"] == "independent_measured":
        external(lock_path)
        external(output)
    loading = time.perf_counter() - started
    records = []
    for index, case in enumerate(data["queries"]):
        for arm in (("A", "B") if index % 2 == 0 else ("B", "A")):
            start = time.perf_counter()
            prediction, error_type = None, None
            try:
                prediction = (arm_a if arm == "A" else arm_b)(data["executor"], case)
                status = "ok"
            except Exception as exc:
                status, error_type = "exception", type(exc).__name__
            elapsed = time.perf_counter() - start
            if elapsed > CONFIG["timeout_seconds"]:
                status, prediction = "timeout", None
            records.append({"case_id": case["case_id"], "arm": arm, "status": status,
                            "prediction": prediction, "error_type": error_type,
                            "elapsed_seconds": elapsed, "input_sha256": digest(inputs(case))})
    # Detect drift during the run; do not publish a mixed-snapshot run.
    validate_lock(load_bundle(bundle), lock)
    result = {"schema": 1, "lock_id": lock["lock_id"], "config": CONFIG,
              "classification": lock["classification"], "shared_loading_seconds": loading,
              "order": "sorted case_id; AB/BA alternating; single sequential process",
              "records": records, "deterministic_sha256": digest([
                  {k: v for k, v in r.items() if k != "elapsed_seconds"} for r in records])}
    write(output, result)
    return result
