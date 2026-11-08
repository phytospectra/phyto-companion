#!/usr/bin/env python3
"""Offline, locked public-regression model comparison. See PROTOCOL.md."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PILOT = HERE.parent / "blinded_pilot"
CASE_DIR = PILOT / "cases"
KEYS = PILOT / "keys" / "answer_keys.json"
PROMPT = HERE / "prompt.txt"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_packet(case: dict, cid: str) -> None:
    # Strict nested allowlist; only these fields can enter model context.
    require(isinstance(case, dict) and set(case) == {"case_id", "task", "acquisition", "evidence"}, f"{cid}: packet fields")
    require(case["case_id"] == cid, f"{cid}: packet ID mismatch")
    task = case["task"]
    require(isinstance(task, dict) and set(task) == {"type", "candidates"} and task["type"] == "candidate_ranking", f"{cid}: task fields")
    require(isinstance(task["candidates"], list) and task["candidates"], f"{cid}: no candidates")
    labels = []
    for cand in task["candidates"]:
        require(isinstance(cand, dict) and set(cand) == {"label", "smiles", "formula"} and all(isinstance(v, str) and v for v in cand.values()), f"{cid}: candidate fields")
        labels.append(cand["label"])
    require(len(labels) == len(set(labels)), f"{cid}: duplicate candidate labels")
    acq = case["acquisition"]
    require(isinstance(acq, dict) and set(acq) == {"modality", "solvent", "field_strength_MHz", "note"}, f"{cid}: acquisition fields")
    require(all(acq[k] is None or isinstance(acq[k], str) for k in ("modality", "solvent", "note")) and (acq["field_strength_MHz"] is None or number(acq["field_strength_MHz"])), f"{cid}: acquisition values")
    require(isinstance(case["evidence"], dict) and set(case["evidence"]) == {"HSQC"} and isinstance(case["evidence"]["HSQC"], list), f"{cid}: evidence fields")
    ids = []
    for peak in case["evidence"]["HSQC"]:
        require(isinstance(peak, dict) and set(peak) == {"evidence_id", "dH", "dC"} and isinstance(peak["evidence_id"], str) and number(peak["dH"]) and number(peak["dC"]), f"{cid}: peak fields")
        ids.append(peak["evidence_id"])
    require(len(ids) == len(set(ids)), f"{cid}: duplicate evidence IDs")
    blob = json_bytes(case).decode().lower()
    for token in ("quercetin", "kaempferol", "luteolin", "true_compound", "answer_label", "expected_behaviour", "reviewer", "fixture", ".csv", "partition", "decisive"):
        require(token not in blob, f"{cid}: leak token {token}")


def payload(prompt: bytes, case: dict) -> bytes:
    return prompt + b"\nCASE JSON:\n" + json_bytes(case) + b"\n"


def validate_config(config: dict) -> None:
    require(isinstance(config, dict) and set(config) == {"settings", "repeats", "arms"}, "config fields")
    settings = config["settings"]
    require(isinstance(settings, dict) and set(settings) == {"temperature", "top_p", "max_tokens", "seed", "context_tokens", "threads"}, "settings fields")
    require(number(settings["temperature"]) and settings["temperature"] >= 0 and number(settings["top_p"]) and 0 < settings["top_p"] <= 1, "invalid sampling settings")
    require(type(settings["max_tokens"]) is int and settings["max_tokens"] > 0 and type(settings["seed"]) is int and 0 <= settings["seed"] < 2**31, "invalid token limit or seed")
    require(type(settings["context_tokens"]) is int and settings["context_tokens"] > 0 and type(settings["threads"]) is int and settings["threads"] > 0, "invalid context or threads")
    require(type(config["repeats"]) is int and 1 <= config["repeats"] <= 100 and settings["seed"] + config["repeats"] - 1 < 2**31, "invalid repeats")
    require(isinstance(config["arms"], list) and config["arms"], "arms required")
    names = []
    for arm in config["arms"]:
        require(isinstance(arm, dict) and set(arm) == {"id", "provider", "provider_version", "model", "model_version", "adapter", "artifact_path", "artifact_sha256"}, "arm fields")
        require(all(isinstance(arm[k], str) and arm[k].strip() for k in ("id", "provider", "provider_version", "model", "model_version")), "arm identity required")
        require(re.fullmatch(r"[A-Za-z0-9_-]+", arm["id"]) is not None, "invalid arm ID")
        require(arm["adapter"] in ("replay", "llama_cpp"), "unsupported adapter")
        if arm["adapter"] == "replay":
            require(arm["artifact_path"] is None and arm["artifact_sha256"] is None, "replay artifact must be null")
        else:
            require(isinstance(arm["artifact_path"], str) and Path(arm["artifact_path"]).is_absolute() and isinstance(arm["artifact_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", arm["artifact_sha256"]) is not None, "local artifact path/hash required")
        names.append(arm["id"])
    require(len(set(names)) == len(names), "duplicate arm IDs")


def create_lock(config: dict) -> dict:
    validate_config(config)
    prompt = PROMPT.read_bytes()
    cases = {}
    for path in sorted(CASE_DIR.glob("*.json")):
        case = load(path)
        if not case.get("task", {}).get("candidates"):
            continue
        cid = path.stem
        require(re.fullmatch(r"C[0-9]+", cid) is not None, "invalid case filename")
        validate_packet(case, cid)
        cases[cid] = {"packet_sha256": file_digest(path), "input_sha256": digest(payload(prompt, case))}
    require(bool(cases), "no eligible cases")
    keys = load(KEYS)
    require(isinstance(keys, list) and len({k["case_id"] for k in keys}) == len(keys) and set(cases) <= {k["case_id"] for k in keys}, "key coverage/duplicates")
    return {"format": "model-comparison-lock-v1", "classification": "public_regression_not_blinded", "harness_sha256": file_digest(Path(__file__)), "prompt_sha256": digest(prompt), "keys_sha256": file_digest(KEYS), "cases": cases, **config}


def verify_lock(lock: dict) -> None:
    require(isinstance(lock, dict) and set(lock) == {"format", "classification", "harness_sha256", "prompt_sha256", "keys_sha256", "cases", "settings", "repeats", "arms"}, "lock fields")
    require(lock["format"] == "model-comparison-lock-v1" and lock["classification"] == "public_regression_not_blinded", "lock version/classification")
    current = create_lock({k: lock[k] for k in ("settings", "repeats", "arms")})
    require(current == lock, "locked prompt, packets, keys, eligible set or config changed")


def expected_identity(arm: dict) -> dict:
    return {k: arm[k] for k in ("provider", "provider_version", "model", "model_version", "adapter", "artifact_sha256")}


def run_local(lock_path: Path, arm_id: str) -> list[dict]:
    lock = load(lock_path)
    verify_lock(lock)
    arm = next((a for a in lock["arms"] if a["id"] == arm_id), None)
    require(arm is not None and arm["adapter"] == "llama_cpp", "selected arm is not llama_cpp")
    artifact = Path(arm["artifact_path"])
    require(artifact.is_file() and file_digest(artifact) == arm["artifact_sha256"], "local model artifact missing or hash mismatch")
    try:
        import llama_cpp
    except ImportError as exc:
        raise ValueError("llama_cpp is not installed; no local inference performed") from exc
    require(getattr(llama_cpp, "__version__", None) == arm["provider_version"], "llama_cpp version differs from lock")
    # No download or network adapter. Do not pass keys, worksheets, paths or metadata to the model.
    model = llama_cpp.Llama(model_path=str(artifact), n_ctx=lock["settings"]["context_tokens"],
                            n_threads=lock["settings"]["threads"], n_gpu_layers=0, verbose=False)
    records = []
    lock_hash = file_digest(lock_path)
    for cid, info in lock["cases"].items():
        case = load(CASE_DIR / f"{cid}.json")
        text = payload(PROMPT.read_bytes(), case).decode("utf-8")
        for repeat in range(lock["repeats"]):
            settings = {**lock["settings"], "seed": lock["settings"]["seed"] + repeat}
            record = {"arm": arm_id, "case_id": cid, "repeat": repeat, "lock_sha256": lock_hash,
                      "input_sha256": info["input_sha256"], "identity": expected_identity(arm),
                      "settings": settings, "status": "failure", "response_text": None,
                      "input_tokens": None, "output_tokens": None, "cost_usd": 0.0,
                      "cost_basis": "local_no_provider_charge", "elapsed_seconds": None,
                      "error": None, "runtime": {"python": platform.python_version(), "platform": platform.platform(), "llama_cpp": llama_cpp.__version__}}
            started = time.monotonic()
            try:
                generation = {k: settings[k] for k in ("temperature", "top_p", "max_tokens", "seed")}
                output = model.create_completion(prompt=text, **generation)
                record.update(status="success", response_text=output["choices"][0]["text"],
                              input_tokens=output.get("usage", {}).get("prompt_tokens"),
                              output_tokens=output.get("usage", {}).get("completion_tokens"))
            except Exception as exc:  # preserve failed attempts and unknown usage
                record["error"] = f"{type(exc).__name__}: {exc}"
            record["elapsed_seconds"] = time.monotonic() - started
            records.append(record)
    return records


def validate_record(record: dict, lock: dict, lock_hash: str) -> str | None:
    if not isinstance(record, dict):
        return "record is not an object"
    required = {"arm", "case_id", "repeat", "lock_sha256", "input_sha256", "identity", "settings", "status", "response_text", "input_tokens", "output_tokens", "cost_usd", "cost_basis", "elapsed_seconds", "error", "runtime"}
    if set(record) != required:
        return "record fields"
    arm = next((a for a in lock["arms"] if a["id"] == record["arm"]), None)
    cid, repeat = record["case_id"], record["repeat"]
    if arm is None or not isinstance(cid, str) or cid not in lock["cases"] or type(repeat) is not int or not 0 <= repeat < lock["repeats"]:
        return "unknown slot"
    settings = {**lock["settings"], "seed": lock["settings"]["seed"] + repeat}
    if record["lock_sha256"] != lock_hash or record["input_sha256"] != lock["cases"][cid]["input_sha256"] or record["identity"] != expected_identity(arm) or record["settings"] != settings:
        return "lock, input, identity or settings mismatch"
    if record["status"] not in ("success", "failure") or not isinstance(record["runtime"], dict):
        return "invalid status or runtime"
    if record["status"] == "success" and (not isinstance(record["response_text"], str) or record["error"] is not None):
        return "invalid success"
    if record["status"] == "failure" and (record["response_text"] is not None or not isinstance(record["error"], str) or not record["error"].strip()):
        return "invalid failure"
    for key in ("input_tokens", "output_tokens"):
        if record[key] is not None and (type(record[key]) is not int or record[key] < 0):
            return "invalid token count"
    for key in ("cost_usd", "elapsed_seconds"):
        if record[key] is not None and (not number(record[key]) or record[key] < 0):
            return "invalid cost or duration"
    if record["cost_basis"] not in ("actual", "estimated", "local_no_provider_charge", "unknown"):
        return "invalid cost basis"
    if (record["cost_basis"] == "unknown") != (record["cost_usd"] is None):
        return "cost basis/value mismatch"
    if record["cost_basis"] == "local_no_provider_charge" and (record["cost_usd"] != 0 or arm["adapter"] != "llama_cpp"):
        return "invalid local cost"
    return None


def score_response(text: str, case: dict, key: dict) -> dict:
    try:
        response = json.loads(text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        require(isinstance(response, dict) and set(response) == {"case_id", "selected_candidate", "cited_evidence", "abstention_reason"}, "response fields")
        require(response["case_id"] == case["case_id"], "response case ID")
        selected = response["selected_candidate"]
        require(selected is None or (isinstance(selected, str) and selected in {c["label"] for c in case["task"]["candidates"]}), "candidate label")
        require(response["abstention_reason"] is None or isinstance(response["abstention_reason"], str), "abstention reason")
        require(isinstance(response["cited_evidence"], list), "citations must be an array")
        require(all(isinstance(c, dict) for c in response["cited_evidence"]), "citation must be an object")
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": "malformed_response", "error": str(exc)}
    citations = []
    peaks = case["evidence"]["HSQC"]
    for citation in response["cited_evidence"]:
        valid = False
        reason = "invalid citation"
        # Never fall back from an explicit unknown ID or incompatible type.
        if "evidence_id" in citation:
            valid = (set(citation) <= {"evidence_id", "type"} and isinstance(citation["evidence_id"], str)
                     and citation.get("type", "HSQC") == "HSQC" and citation["evidence_id"] in {p["evidence_id"] for p in peaks})
            reason = "packet ID exists" if valid else "unknown ID, incompatible type or unsupported fields"
        elif set(citation) == {"type", "dH", "dC"} and citation["type"] == "HSQC" and number(citation["dH"]) and number(citation["dC"]):
            hits = [p["evidence_id"] for p in peaks if abs(p["dH"] - citation["dH"]) <= 0.05 and abs(p["dC"] - citation["dC"]) <= 0.50]
            valid = len(hits) == 1
            reason = "unique coordinate match" if valid else "no unique coordinate match"
        citations.append({"citation": citation, "exists": valid, "reason": reason,
                          "supports_conclusion": "requires expert review" if valid else "not applicable"})
    abstention_expected = key.get("answer_label") is None and key.get("expected_behaviour", "").startswith("abstain")
    reason_present = isinstance(response["abstention_reason"], str) and bool(response["abstention_reason"].strip())
    return {"status": "scored", "selected": selected, "abstention_expected": abstention_expected,
            "candidate_correct": None if abstention_expected else selected == key.get("answer_label"),
            "abstention_with_reason": (selected is None and reason_present) if abstention_expected else None,
            "missing_evidence_identified": "requires expert review" if abstention_expected and selected is None and reason_present else "not established",
            "citations": citations}


def metric(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def evaluate(lock_path: Path, record_paths: list[Path]) -> dict:
    lock = load(lock_path)
    verify_lock(lock)
    lock_hash = file_digest(lock_path)
    keys = {k["case_id"]: k for k in load(KEYS)}
    cases = {cid: load(CASE_DIR / f"{cid}.json") for cid in lock["cases"]}
    groups = {}
    rejected = []
    for path in record_paths:
        records = load(path)
        require(isinstance(records, list), f"{path}: records must be an array")
        for index, rec in enumerate(records):
            error = validate_record(rec, lock, lock_hash)
            if error:
                rejected.append({"file": str(path), "index": index, "reason": error})
            else:
                groups.setdefault((rec["arm"], rec["case_id"], rec["repeat"]), []).append(rec)
    arms_report = []
    for arm in lock["arms"]:
        rows = []
        all_attempts = []
        for cid in lock["cases"]:
            for repeat in range(lock["repeats"]):
                attempts = groups.get((arm["id"], cid, repeat), [])
                all_attempts.extend(attempts)
                row = {"case_id": cid, "repeat": repeat, "attempts": len(attempts)}
                if not attempts:
                    row["status"] = "missing"
                elif len(attempts) > 1:
                    row["status"] = "duplicate_attempts"
                elif attempts[0]["status"] == "failure":
                    row.update(status="failure", error=attempts[0]["error"])
                else:
                    row.update(score_response(attempts[0]["response_text"], cases[cid], keys[cid]))
                rows.append(row)
        scored = [row for row in rows if row["status"] == "scored"]
        candidates = [r for r in scored if not r["abstention_expected"]]
        abstentions = [r for r in scored if r["abstention_expected"]]
        citations = [c for r in scored for c in r["citations"]]
        usage = {}
        for name in ("input_tokens", "output_tokens", "cost_usd", "elapsed_seconds"):
            known = [r[name] for r in all_attempts if r[name] is not None]
            usage[name] = {"known_sum": sum(known), "known_attempts": len(known), "unknown_attempts": len(all_attempts) - len(known)}
        usage["cost_by_basis"] = {basis: {"attempts": sum(r["cost_basis"] == basis for r in all_attempts), "known_sum_usd": sum(r["cost_usd"] for r in all_attempts if r["cost_basis"] == basis and r["cost_usd"] is not None)} for basis in ("actual", "estimated", "local_no_provider_charge", "unknown")}
        arms_report.append({"arm": arm["id"], "identity": expected_identity(arm), "expected_slots": len(rows),
                            "status_counts": {s: sum(r["status"] == s for r in rows) for s in ("scored", "missing", "duplicate_attempts", "failure", "malformed_response")},
                            "scores": {"response_coverage": metric(len(scored), len(rows)),
                                       "candidate_accuracy_on_scored_sufficient_cases": metric(sum(r["candidate_correct"] for r in candidates), len(candidates)),
                                       "abstention_with_nonempty_reason_on_scored_abstention_cases": metric(sum(r["abstention_with_reason"] for r in abstentions), len(abstentions)),
                                       "invalid_citations": metric(sum(not c["exists"] for c in citations), len(citations))},
                            "usage_all_valid_attempts_including_duplicates_and_failures": usage, "slots": rows})
    paired = []
    for i, left in enumerate(arms_report):
        for right in arms_report[i + 1:]:
            lrows = {(r["case_id"], r["repeat"]): r for r in left["slots"]}
            rrows = {(r["case_id"], r["repeat"]): r for r in right["slots"]}
            matched = [(lrows[slot], rrows[slot]) for slot in lrows
                       if lrows[slot]["status"] == rrows[slot]["status"] == "scored"
                       and lrows[slot]["candidate_correct"] is not None
                       and rrows[slot]["candidate_correct"] is not None]
            left_correct = sum(a["candidate_correct"] for a, _ in matched)
            right_correct = sum(b["candidate_correct"] for _, b in matched)
            paired.append({"left": left["arm"], "right": right["arm"],
                           "expected_slots": len(lrows), "matched_scored_sufficient_slots": len(matched),
                           "left_correct": left_correct, "right_correct": right_correct,
                           "left_minus_right": (left_correct - right_correct) / len(matched) if matched else None,
                           "note": "Repeated slots are not independent cases; unmatched slots are not silently scored."})
    return {"format": "model-comparison-report-v1", "classification": lock["classification"],
            "lock_sha256": lock_hash, "prompt_sha256": lock["prompt_sha256"], "keys_sha256": lock["keys_sha256"],
            "warning": "Public, unreviewed smoke fixtures; no blinded benchmark or fine-tuning evidence. Replay provenance and provider settings are self-reported. Citation support and missing-evidence correctness require expert review.",
            "rejected_records": rejected, "arms": arms_report, "paired_candidate_comparisons": paired}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("lock")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run-local")
    run.add_argument("--lock", type=Path, required=True)
    run.add_argument("--arm", required=True)
    run.add_argument("--output", type=Path, required=True)
    score = sub.add_parser("evaluate")
    score.add_argument("--lock", type=Path, required=True)
    score.add_argument("--records", type=Path, action="append", default=[])
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "lock":
            write_new(args.output, create_lock(load(args.config)))
        elif args.command == "run-local":
            require(not args.output.exists(), "output already exists")
            write_new(args.output, run_local(args.lock, args.arm))
        else:
            write_new(args.output, evaluate(args.lock, args.records))
        print(f"wrote {args.output} sha256={file_digest(args.output)}")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
