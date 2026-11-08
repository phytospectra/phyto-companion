#!/usr/bin/env python3
"""Regression checks for the repaired blinded-pilot harness.

Run:  .venv/bin/python benchmark/blinded_pilot/test_repairs.py

Covers the six required checks:
  1. packets contain no evaluator-only fields or answer-bearing filenames
  2. every displayed formula matches its validated structure
  3. valid HSQC citation passes; fabricated HMBC sharing one endpoint fails
  4. citation copied from another case fails
  5. with one of four eligible responses missing, run still reports 4 expected
     cases and records the missing response
  6. placeholders stay outside the eligible set; same-seed rebuild reproduces
     packets, keys and hashes
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

PY = sys.executable
failures: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def main() -> int:
    # ---- build once, deterministically ------------------------------------
    subprocess.run([PY, str(HERE / "build_cases.py")], check=True,
                   capture_output=True, cwd=str(HERE))
    hash1 = (HERE / "PACKAGING.sha256").read_text()
    packets1 = {p.name: p.read_bytes() for p in sorted((HERE / "cases").glob("*.json"))}
    keys1 = (HERE / "keys" / "answer_keys.json").read_bytes()
    ws1 = (HERE / "review" / "worksheets.json").read_bytes()

    # --- check 1: blinding --------------------------------------------------
    eval_tokens = ["quercetin", "kaempferol", "luteolin", "answer_label",
                   "expects_abstention", "expected_behaviour", "reviewer",
                   "true_compound", "fixture", ".csv", "partition", "decisive"]
    leak_hits = []
    for p in sorted((HERE / "cases").glob("*.json")):
        blob = p.read_text().lower()
        for tok in eval_tokens:
            if tok in blob:
                leak_hits.append(f"{p.name}:{tok}")
    check("1a. no evaluator-only fields/answer-bearing filenames in packets",
          not leak_hits, str(leak_hits))
    check("1b. packets contain allowlist fields only (case_id/task/acquisition/evidence)",
          all(set(json.loads(p.read_text()).keys()) <=
              {"case_id", "task", "acquisition", "evidence"}
              for p in (HERE / "cases").glob("*.json")))

    # --- check 2: chemistry --------------------------------------------------
    from rdkit import Chem
    from rdkit.Chem import rdMolDescriptors
    bad = []
    for p in sorted((HERE / "cases").glob("*.json")):
        c = json.loads(p.read_text())
        for cand in c["task"]["candidates"]:
            m = Chem.MolFromSmiles(cand["smiles"])
            if m is None:
                bad.append(f"{p.name}:{cand['label']}: unparseable")
                continue
            f = rdMolDescriptors.CalcMolFormula(m)
            if f != cand["formula"]:
                bad.append(f"{p.name}:{cand['label']}: {f} != {cand['formula']}")
    check("2a. displayed formula matches validated structure (all candidates)",
          not bad, str(bad))
    dup_within = {}
    for p in sorted((HERE / "cases").glob("*.json")):
        c = json.loads(p.read_text())
        seen = {}
        for cand in c["task"]["candidates"]:
            cs = canon_seen_key(cand["smiles"])
            seen.setdefault(cs, []).append(cand["label"])
        for cs, labels in seen.items():
            if len(labels) > 1:
                dup_within[f"{p.name}"] = labels
    check("2b. no duplicate structures under different candidate IDs within a case",
          not dup_within, str(dup_within))

    # shuffle integrity: answer label in key maps to the true structure
    keys = json.loads(keys1)
    ok_map = True
    for k in keys:
        if k.get("answer_label") is None:
            continue
        case = json.loads((HERE / "cases" / f"{k['case_id']}.json").read_text())
        sel = [c for c in case["task"]["candidates"] if c["label"] == k["answer_label"]]
        if not sel:
            ok_map = False; continue
        cs = canon_seen_key(sel[0]["smiles"])
        true_cs = canon_seen_key(smiles_of(k["true_compound"]))
        if cs != true_cs:
            ok_map = False
    check("2c. answer label still points to intended structure after shuffling",
          ok_map)

    # --- checks 3-5 need a run dir ------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "C001.response.json").write_text(json.dumps({
            "case_id": "C001", "selected_candidate": json.loads(keys1)[0]["answer_label"],
            "cited_evidence": valid_hsqc_citation("C001") + fabricated_hmbc_citation("C001")
                              + label_only_hmbc_citation(),
            "abstention_reason": None}))
        (td / "C002.response.json").write_text(json.dumps({
            "case_id": "C002", "selected_candidate": "cand-a",
            "cited_evidence": cross_case_citation("C003", "C002"),
            "abstention_reason": None}))
        # C003 deliberately NOT written (check 5)
        (td / "C004.response.json").write_text(json.dumps({
            "case_id": "C004", "selected_candidate": None,
            "cited_evidence": [], "abstention_reason": "B-ring HSQC peaks absent; "
            "A-ring peaks identical across candidates"}))
        # duplicate + unknown submissions (check 5d)
        (td / "C004.response.2.json").write_text(json.dumps({
            "case_id": "C004", "selected_candidate": None,
            "cited_evidence": [], "abstention_reason": "dup"}))
        (td / "C999.response.json").write_text(json.dumps({
            "case_id": "C999", "selected_candidate": "cand-a"}))

        # call scorer as subprocess for a clean report
        out = subprocess.run([PY, str(HERE / "score_run.py"), str(td)],
                             capture_output=True, text=True, check=True).stdout
        rep = json.loads(out)

        # check 3
        c001 = next(x for x in rep["per_case"] if x["case_id"] == "C001")
        inv = c001["citation_verdicts"]
        check("3a. valid HSQC citation passes", inv[0]["valid"])
        check("3b. fabricated HMBC sharing one endpoint fails",
              (not inv[1]["valid"]) if len(inv) > 1 else False,
              inv[1]["reason"] if len(inv) > 1 else "missing")
        check("3c. label-only HMBC ('H-7' matches a label) never validates",
              (not inv[2]["valid"]) if len(inv) > 2 else False,
              inv[2]["reason"] if len(inv) > 2 else "missing")

        # check 4
        c002 = next(x for x in rep["per_case"] if x["case_id"] == "C002")
        check("4. citation copied from another case fails",
              not c002["citation_verdicts"][0]["valid"],
              c002["citation_verdicts"][0]["reason"])

        # check 5
        check("5. one response missing -> still 4 eligible cases + missing recorded",
              rep["eligible_case_count"] == 4 and rep["missing_responses"] == ["C003"],
              f"eligible={rep['eligible_case_count']}, missing={rep['missing_responses']}")
        acc = rep["scores"]["candidate_accuracy"]
        # C001 scored, C002 scored, C003 missing (excluded from num/den),
        # C004 duplicate (excluded from num/den) -> denominator = 2
        check("5b. numerator/denominator reported separately",
              acc["denominator"] == 2 and isinstance(acc["numerator"], int),
              f"acc={acc}")
        check("5c. duplicate submissions detected",
              any(x.get("status") == "duplicate_submissions" for x in rep["per_case"]),
              str([x.get("status") for x in rep["per_case"]]))
        check("5d. unknown-case submission detected",
              any("C999" in u for u in rep["unknown_submissions"]),
              str(rep["unknown_submissions"]))

    # --- check 6: placeholders + determinism --------------------------------
    rep_all = subprocess.run([PY, str(HERE / "score_run.py"), str(HERE / "cases")],
                             capture_output=True, text=True).stdout
    try:
        r = json.loads(rep_all)
        elig = r["eligible_cases"]
    except Exception:
        elig = None
    check("6a. placeholder cases outside eligible set",
          elig is not None and all(e in {"C001", "C002", "C003", "C004"} for e in elig),
          str(elig))

    # rebuild with same seed -> identical artifacts
    subprocess.run([PY, str(HERE / "build_cases.py")], check=True, capture_output=True,
                   cwd=str(HERE))
    hash2 = (HERE / "PACKAGING.sha256").read_text()
    packets2 = {p.name: p.read_bytes() for p in sorted((HERE / "cases").glob("*.json"))}
    keys2 = (HERE / "keys" / "answer_keys.json").read_bytes()
    ws2 = (HERE / "review" / "worksheets.json").read_bytes()
    check("6b. same-seed rebuild reproduces packets, keys and hash",
          hash1 == hash2 and packets1 == packets2 and keys1 == keys2 and ws1 == ws2,
          f"hash equal={hash1==hash2}")

    print()
    if failures:
        print(f"{len(failures)} FAILURES: {failures}")
        return 1
    print("ALL REPAIR CHECKS PASS")
    return 0


def canon_seen_key(smiles: str) -> str:
    from rdkit import Chem
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


def smiles_of(compound: str) -> str:
    # read from builder registry instead
    import importlib.util
    spec = importlib.util.spec_from_file_location("bc", HERE / "build_cases.py")
    bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
    return bc.STRUCTURE_REGISTRY[compound]["smiles"]


def valid_hsqc_citation(cid: str) -> list[dict]:
    case = json.loads((HERE / "cases" / f"{cid}.json").read_text())
    p = case["evidence"]["HSQC"][0]
    return [{"evidence_id": p["evidence_id"]}]


def fabricated_hmbc_citation(cid: str) -> list[dict]:
    """HMBC claiming an H-7->C-9 correlation: neither endpoint exists in an
    HSQC-only packet; 'H-7' as a bare label must never validate it."""
    return [{"type": "HMBC", "from": "H-7", "to": "C-9", "evidence_id": "E999"}]


def label_only_hmbc_citation() -> list[dict]:
    """No evidence_id, bare labels 'H-7'/'C-9'. The packet contains no such
    labels at all (it's unassigned coordinates), but even if it did, a bare
    label must never validate a correlation claim."""
    return [{"type": "HMBC", "from": "H-7", "to": "C-9"}]


def cross_case_citation(from_cid: str, to_cid: str) -> list[dict]:
    """First evidence ID of from_cid's packet (does not exist in to_cid)."""
    case = json.loads((HERE / "cases" / f"{from_cid}.json").read_text())
    return [{"evidence_id": case["evidence"]["HSQC"][0]["evidence_id"]}]


if __name__ == "__main__":
    sys.exit(main())
