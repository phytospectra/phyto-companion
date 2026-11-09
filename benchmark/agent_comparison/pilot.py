#!/usr/bin/env python3
"""Bounded synthetic OpenRouter smoke pilot. Plan first; no implicit inference.

The $1 local reservation estimate is NOT an independent provider-enforced cap.
A nonresetting API key capped at <=$50 INCLUDING BYOK is required. Unknown cost,
HTTP/transport errors, or identity mismatches stop the run, never auto-retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

import httpx

try:
    from .cases import CASES, PROMPT
except ImportError:
    from cases import CASES, PROMPT

BASE = "https://openrouter.ai/api/v1"
MODELS = ["google/gemini-3.8-flash", "qwen/qwen3.8-27b", "google/gemma-4-31b-it"]
MAX_OUTPUT = 1024
LOCAL_RESERVATION_CAP = 1.0
KEY_CAP = 50.0


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def write_new(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def plan(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    inputs = [{"case_id": case["case_id"], "packet": case["packet"]} for case in CASES]
    keys = {case["case_id"]: case["expected"] for case in CASES}
    value = {
        "schema_version": 1, "status": "synthetic_software_smoke_only", "created_at": now(),
        "models": MODELS, "repeats": 1, "max_calls": 18,
        "settings": {"temperature": 0, "max_tokens": MAX_OUTPUT, "reasoning": {"enabled": False}},
        "prompt": PROMPT, "inputs": inputs, "inputs_sha256": digest(inputs),
        "keys_sha256": digest(keys), "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "max_local_reservation_usd": LOCAL_RESERVATION_CAP, "required_key_cap_usd": KEY_CAP,
        "antigravity_science_plugin": "not_run_not_available",
    }
    write_new(directory / "plan.json", value)
    write_new(directory / "keys.json", keys)
    write_new(directory / "plan-digest.json", {"sha256": digest(value)})
    return value


def verify(directory):
    value = load(directory / "plan.json")
    require(digest(value) == load(directory / "plan-digest.json")["sha256"], "Plan digest mismatch")
    require(digest(value["inputs"]) == value["inputs_sha256"], "Input digest mismatch")
    require(digest(load(directory / "keys.json")) == value["keys_sha256"], "Key digest mismatch")
    require(value["runner_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "Runner changed since plan")
    require(value["models"] == MODELS and value["max_calls"] == 18, "Unsupported plan")
    require(value["inputs"] == [{"case_id": c["case_id"], "packet": c["packet"]} for c in CASES], "Only original synthetic cases allowed")
    require(value["prompt"] == PROMPT, "Prompt changed")
    return value


def validate_key(data):
    require(isinstance(data, dict), "Malformed key response")
    cap, remaining, usage = data.get("limit"), data.get("limit_remaining"), data.get("usage")
    require(finite(cap) and 0 < cap <= KEY_CAP, "A dedicated nonresetting key cap <= US$50 is required")
    require(data.get("limit_reset") is None, "Resetting key budgets are prohibited")
    require(data.get("include_byok_in_limit") is True, "Enable inclusion of BYOK usage in the API key cap")
    require(finite(remaining) and remaining > 0 and finite(usage) and usage >= 0, "Key budget unavailable")
    require(remaining <= cap and usage <= cap, "Inconsistent/exhausted key budget")
    return {"limit": cap, "remaining": remaining, "usage": usage, "include_byok_in_limit": True}


def prices(catalog):
    result = {}
    for model in MODELS:
        matches = [entry for entry in catalog if entry.get("id") == model]
        require(len(matches) == 1, f"Model unavailable: {model}")
        pricing = matches[0].get("pricing", {})
        rates = {}
        for key in ("prompt", "completion"):
            try:
                rates[key] = float(pricing[key])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Unknown price: {model}") from None
            require(finite(rates[key]) and rates[key] >= 0, "Invalid model price")
        for key in ("request", "web_search", "image", "audio"):
            # Text-only request, no plugins/tools; request-level fixed fees must be known.
            if key == "request" and key in pricing:
                require(float(pricing[key]) == 0, "Fixed request fee unsupported")
        result[model] = rates
    return result


def body_for(value, item, model, rates):
    return {
        "model": model, "messages": [
            {"role": "system", "content": value["prompt"]},
            {"role": "user", "content": canonical(item).decode()},
        ],
        "temperature": 0, "max_tokens": MAX_OUTPUT, "reasoning": {"enabled": False},
        "provider": {"allow_fallbacks": False, "require_parameters": True,
                     "data_collection": "deny",
                     "max_price": {key: rate * 1_000_000 for key, rate in rates.items()}},
        "stream": False,
    }


def reservation(body, rates):
    # Byte-based bound plus chat overhead is conservative for these tiny ASCII
    # prompts, not a universal tokenizer guarantee. Key cap is the hard guard.
    input_bound = sum(len(message["content"].encode()) for message in body["messages"]) + 1024
    return input_bound * rates["prompt"] + MAX_OUTPUT * rates["completion"]


def score(text, expected):
    try:
        response = json.loads(text)
    except (TypeError, ValueError):
        return {"passed": False, "reason": "not_exact_json"}
    valid = isinstance(response, dict) and set(response) == set(expected)
    if not valid or not isinstance(response.get("evidence_ids"), list):
        return {"passed": False, "reason": "schema_mismatch"}
    if len(response["evidence_ids"]) != len(set(str(x) for x in response["evidence_ids"])):
        return {"passed": False, "reason": "duplicate_evidence"}
    match = (response["selected_candidate"] == expected["selected_candidate"] and
             response["disposition"] == expected["disposition"] and
             sorted(response["evidence_ids"], key=str) == sorted(expected["evidence_ids"]))
    return {"passed": match, "reason": "contract_match" if match else "contract_mismatch"}


def run(directory: Path, api_key: str, client=None):
    require(bool(api_key), "OPENROUTER_API_KEY is missing")
    value = verify(directory)
    # An exclusive persistent run marker prevents concurrent or restart replays,
    # including crashes between dispatch and output capture. No automatic resume.
    write_new(directory / "RUN-STARTED.json", {"started_at": now(), "plan_sha256": digest(value)})
    owned = client is None
    if owned:
        client = httpx.Client(base_url=BASE, timeout=60, follow_redirects=False)
    headers = {"Authorization": "Bearer " + api_key}
    results = []
    spent = 0.0
    reserved = 0.0
    stop_reason = None
    try:
        key_response = client.get(BASE + "/key", headers=headers)
        require(key_response.status_code == 200, "Key preflight failed")
        budget = validate_key(key_response.json().get("data"))
        catalog = client.get(BASE + "/models")
        require(catalog.status_code == 200, "Model catalog unavailable")
        model_prices = prices(catalog.json().get("data", []))
        write_new(directory / "preflight.json", {"budget": budget, "prices": model_prices, "at": now()})
        planned = [(model, item, body_for(value, item, model, model_prices[model]))
                   for item in value["inputs"] for model in value["models"]]
        estimate = sum(reservation(body, model_prices[model]) for model, _, body in planned)
        require(estimate <= LOCAL_RESERVATION_CAP and estimate < budget["remaining"], "Plan exceeds local reservation or key budget")
        keys = load(directory / "keys.json")
        for index, (model, item, body) in enumerate(planned):
            bound = reservation(body, model_prices[model])
            require(reserved + bound <= LOCAL_RESERVATION_CAP, "Local reservation exhausted")
            # Recheck lifetime budget serially before each call. Never silently top up.
            check = client.get(BASE + "/key", headers=headers)
            require(check.status_code == 200, "Budget recheck unavailable")
            current = validate_key(check.json().get("data"))
            require(bound < current["remaining"], "Remaining key budget too small")
            reserved += bound
            entry = {"slot": index, "case_id": item["case_id"], "requested_model": model,
                     "input_sha256": digest(body), "reservation_usd": bound, "at": now()}
            write_new(directory / f"{index:02d}-pending.json", entry)
            started = time.monotonic()
            try:
                response = client.post(BASE + "/chat/completions", headers=headers, json=body)
            except httpx.HTTPError:
                stop_reason = "ambiguous_transport_failure_no_retry"
                write_new(directory / f"{index:02d}-failure.json", {**entry, "status": stop_reason})
                break
            entry["elapsed_seconds"] = time.monotonic() - started
            entry["http_status"] = response.status_code
            try:
                raw = response.json()
            except ValueError:
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            # Persist only allowlisted response fields; never remote error text or headers.
            entry.update({key: raw.get(key) for key in ("id", "model", "provider", "usage", "choices")})
            entry["status"] = "received"
            write_new(directory / f"{index:02d}-response.json", entry)
            results.append(entry)
            if response.status_code != 200 or raw.get("error"):
                stop_reason = "provider_failure_no_retry"
                break
            usage = raw.get("usage")
            cost = usage.get("cost") if isinstance(usage, dict) else None
            if not finite(cost) or cost < 0:
                stop_reason = "unknown_cost_reconcile_before_continuing"
                break
            spent += cost
            if cost > bound or spent > LOCAL_RESERVATION_CAP:
                stop_reason = "reservation_estimate_exceeded_no_more_calls"
                break
            if raw.get("model") != model:
                stop_reason = "served_model_mismatch_no_more_calls"
                break
            if not raw.get("id") or not raw.get("provider"):
                stop_reason = "provider_identity_missing"
                break
            try:
                choice = raw["choices"][0]
                text = choice["message"]["content"]
                result = score(text, keys[item["case_id"]])
                if choice.get("finish_reason") != "stop":
                    result = {"passed": False, "reason": "incomplete_response"}
            except (KeyError, IndexError, TypeError):
                result = {"passed": False, "reason": "malformed_completion"}
            entry["score"] = result
            write_new(directory / f"{index:02d}-score.json", result)
    except (ValueError, httpx.HTTPError):
        # Do not log exception text: remote bodies/URLs can contain sensitive data.
        stop_reason = "preflight_or_budget_guard_failed"
        raise
    finally:
        report = {"purpose": "synthetic_software_smoke_not_scientific_validation",
                  "antigravity_skills": "not_run", "planned_slots": 18, "received_slots": len(results),
                  "known_cost_usd": spent, "reserved_estimate_usd": reserved,
                  "stop_reason": stop_reason, "results": results,
                  "limits": ["Six public synthetic instruction-following contracts; no scientific accuracy claim.",
                             "Hosted model aliases do not provide immutable weight identity.",
                             "No Antigravity Science plugin or local fine-tuned model was exercised.",
                             "Local reservation is a conservative estimate, not a provider spend guarantee."]}
        write_new(directory / "report.json", report)
        if owned:
            client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "run"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.action == "plan":
            plan(args.output)
            print("Synthetic plan written; no API called.")
        else:
            report = run(args.output, os.getenv("OPENROUTER_API_KEY", ""))
            print(json.dumps({k: report[k] for k in ("purpose", "received_slots", "known_cost_usd", "stop_reason")}))
            return 0 if report["received_slots"] == 18 and report["stop_reason"] is None else 1
    except (ValueError, FileExistsError, httpx.HTTPError):
        print("Pilot stopped by preflight/budget/replay guard. Inspect local records; no automatic retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
