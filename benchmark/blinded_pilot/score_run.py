#!/usr/bin/env python3
"""Score a blinded-benchmark run.

SCORING CONTRACT
----------------
1. The ELIGIBLE set is frozen from cases/*.json before inference. Every
   eligible case must appear in the report, including missing/malformed
   responses (recorded as missing/invalid, counted in denominators).
2. Duplicate or unknown-case submissions are detected and reported.
3. A cited evidence ID must belong to THAT case's packet. For
   coordinate-based fallback, BOTH coordinates must match the SAME
   observed peak within documented tolerances. A label-only match
   ("H-7") never validates a correlation.
4. Three scores are reported separately, each with numerator + denominator:
     - candidate accuracy (eligible, non-missing, non-malformed responses)
     - invalid citations  (every response)
     - abstention correctness (eligible abstention-expected + others)
5. "Citation exists" is distinguished from "citation supports the
   conclusion": the latter requires the packet to contain the decisive
   correlation the citation claims, which is only fully checkable by the
   reviewer. Automatic scoring marks support_status and leaves
   'supports' claims to expert review unless an adjudicated rubric exists.

Response schema (one file per case: <case_id>.response.json):
{
  "case_id": "C001",
  "selected_candidate": "cand-a" | null,     # null = abstain
  "ranking": ["cand-b", ...],                # optional, must cover candidates
  "cited_evidence": [
    {"evidence_id": "E002"}                  # preferred: packet-scoped ID
  ],
  "abstention_reason": "..." | null
}
Coordinate fallback (only if evidence_id is absent):
  {"type":"HSQC","dH":6.19,"dC":98.5}  -> must match ONE observed peak
  within DH_TOL / DC_TOL on both coordinates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Documented coordinate tolerances (same for all arms; from fixture curation)
DH_TOL = 0.05   # ppm, 1H
DC_TOL = 0.50   # ppm, 13C


def load_case(cid: str) -> dict | None:
    p = HERE / "cases" / f"{cid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def eligible_cases() -> list[str]:
    """Frozen from packets; placeholders (empty candidates) excluded."""
    out = []
    for p in sorted((HERE / "cases").glob("*.json")):
        c = json.loads(p.read_text())
        if not c.get("task", {}).get("candidates"):
            continue  # placeholder case — outside the eligible set
        out.append(c["case_id"])
    return out


def match_coordinate_citation(cited: dict, packet: dict) -> tuple[bool, str]:
    """Coordinate fallback: both coordinates must match the same observed peak."""
    if cited.get("type") != "HSQC":
        return False, "coordinate fallback only supported for HSQC in this packet"
    dh, dc = cited.get("dH"), cited.get("dC")
    if dh is None or dc is None:
        return False, "coordinate citation missing dH or dC"
    hits = []
    for p in packet.get("evidence", {}).get("HSQC", []):
        if abs(p["dH"] - dh) <= DH_TOL and abs(p["dC"] - dc) <= DC_TOL:
            hits.append(p["evidence_id"])
    if not hits:
        return False, f"no observed peak within dH±{DH_TOL}/dC±{DC_TOL}"
    if len(hits) > 1:
        return False, f"ambiguous: matches multiple peaks {hits}"
    return True, f"coordinate match -> {hits[0]}"


def evaluate_citation(cited: dict, cid: str, packet: dict) -> dict:
    """Return verdict on ONE cited item. Never uses bare labels."""
    if not isinstance(cited, dict):
        return {"cited": cited, "valid": False, "reason": "not an object"}
    eid = cited.get("evidence_id")
    if eid:
        packet_ids = {p["evidence_id"] for p in packet.get("evidence", {}).get("HSQC", [])}
        if eid in packet_ids:
            return {"cited": cited, "valid": True, "reason": f"evidence_id {eid} in packet {cid}"}
        # coordinate fallback attempt when the ID is unknown here
        ok, msg = match_coordinate_citation(cited, packet)
        if ok:
            return {"cited": cited, "valid": True, "reason": f"coordinate fallback: {msg}"}
        return {"cited": cited, "valid": False,
                "reason": f"evidence_id {eid} not in packet {cid}; coordinate fallback failed ({msg})"}
    ok, msg = match_coordinate_citation(cited, packet)
    return {"cited": cited, "valid": ok,
            "reason": ("coordinate fallback: " + msg) if ok else msg}


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    run_dir = Path(argv[0])
    keys_path = HERE / "keys" / "answer_keys.json"
    if "--keys" in argv:
        keys_path = Path(argv[argv.index("--keys") + 1])
    keys = {k["case_id"]: k for k in json.loads(keys_path.read_text())}

    eligible = eligible_cases()
    expected_n = len(eligible)

    # Collect submissions; detect duplicates/unknowns.
    # Pattern matches <case_id>.response.json AND <case_id>.<n>.response.json
    # (the latter = a duplicate submission by the same case).
    submissions: dict[str, list[Path]] = {}
    unknown_files: list[str] = []
    for f in sorted(run_dir.glob("*.json")):
        stem = f.name[: -len(".json")]          # e.g. C004.response / C004.response.2
        parts = stem.split(".")
        if len(parts) < 2 or parts[1] != "response":
            continue
        cid = parts[0]
        if cid in eligible:
            submissions.setdefault(cid, []).append(f)
        else:
            unknown_files.append(f.name)

    per_case = []
    # Candidate-accuracy denominators
    acc_num = acc_den = 0
    inv_cit_num = inv_cit_den = 0      # invalid citations / citations evaluated
    abst_num = abst_den = 0
    missing = []
    malformed = []

    for cid in eligible:
        case = load_case(cid)
        files = submissions.get(cid, [])
        entry = {"case_id": cid, "eligible": True, "n_submissions": len(files)}

        if not files:
            entry.update({"status": "missing_response"})
            missing.append(cid)
            per_case.append(entry)
            continue
        if len(files) > 1:
            entry["status"] = "duplicate_submissions"
            entry["files"] = [f.name for f in files]
            per_case.append(entry)
            continue

        try:
            resp = json.loads(files[0].read_text())
            assert isinstance(resp, dict)
            assert resp.get("case_id") == cid
        except Exception as e:
            entry.update({"status": "malformed_response", "error": str(e)})
            malformed.append(cid)
            per_case.append(entry)
            continue

        key = keys.get(cid, {})
        abstain_expected = key.get("answer_label") is None and \
            bool(key.get("expected_behaviour", "").startswith("abstain"))
        selected = resp.get("selected_candidate")
        abstained = selected is None
        cited = resp.get("cited_evidence") or []

        # 1) candidate accuracy
        if abstain_expected:
            cand_ok = abstained and bool(resp.get("abstention_reason"))
        else:
            cand_ok = selected == key.get("answer_label")
        acc_den += 1
        acc_num += int(cand_ok)

        # 2) invalid citations
        verdicts = [evaluate_citation(c, cid, case) for c in cited]
        n_inv = sum(1 for v in verdicts if not v["valid"])
        inv_cit_den += len(verdicts)
        inv_cit_num += n_inv

        # distinguish "exists" from "supports the conclusion"
        support_flags = []
        for v in verdicts:
            support_flags.append({
                "citation": v["cited"],
                "exists": v["valid"],
                "supports_conclusion": "requires expert review"
                if v["valid"] else "n/a (citation does not exist)",
            })

        # 3) abstention
        if abstain_expected:
            abst_ok = abstained and bool(resp.get("abstention_reason"))
        else:
            abst_ok = not abstained
        abst_den += 1
        abst_num += int(abst_ok)

        entry.update({
            "status": "scored",
            "abstain_expected": abstain_expected,
            "selected": selected,
            "candidate_correct": cand_ok,
            "citations_evaluated": len(verdicts),
            "invalid_citations": n_inv,
            "citation_verdicts": verdicts,
            "support_status": support_flags,
            "abstention_correct": abst_ok,
        })
        per_case.append(entry)

    report = {
        "run_dir": str(run_dir),
        "eligible_case_count": expected_n,
        "eligible_cases": eligible,
        "placeholders_excluded": sorted(
            p.stem for p in (HERE / "cases").glob("*.json")
            if p.stem not in eligible),
        "missing_responses": missing,
        "malformed_responses": malformed,
        "unknown_submissions": unknown_files,
        "scores": {
            "candidate_accuracy": {
                "numerator": acc_num, "denominator": acc_den,
                "value": round(acc_num / acc_den, 4) if acc_den else None,
                "note": "missing/malformed responses remain in eligible count "
                        "but are excluded from this numerator",
            },
            "invalid_citations": {
                "numerator": inv_cit_num, "denominator": inv_cit_den,
                "value": round(inv_cit_num / inv_cit_den, 4) if inv_cit_den else None,
                "note": "invalid / total citations evaluated",
            },
            "abstention_correct": {
                "numerator": abst_num, "denominator": abst_den,
                "value": round(abst_num / abst_den, 4) if abst_den else None,
            },
        },
        "per_case": per_case,
    }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
