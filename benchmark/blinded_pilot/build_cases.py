#!/usr/bin/env python3
"""Build blinded candidate-ranking cases from verified-rights local sources.

BLINDING CONTRACT
-----------------
Files under cases/ are the model-facing packets. They contain ONLY an
explicit allowlist:
  - opaque case ID (C###)
  - task type and candidate labels + validated structures (SMILES/formula)
  - available acquisition conditions (modality, solvent, field strength)
  - observed peaks/correlations with stable evidence IDs (E###)
Everything else — source provenance, answer, expected abstention, expert
annotations, split metadata — lives in keys/ or review/ (evaluator-only).

The model-facing peak tables are UNASSIGNED: positions carry no atom labels
that imply the answer. Expert atom assignments (from the curated fixtures)
stay in the evaluator-only key/worksheet files. Coordinates are measured
values from the fixtures, rounded to 2 dp as curated.

Cases are regenerated deterministically from the recorded SEED; rebuilding
reproduces identical packets, keys and hashes (checked by tests).

CHEMISTRY VALIDATION
--------------------
Every candidate structure is parsed with RDKit; its derived molecular
formula is compared against the displayed formula; duplicates (same
canonical SMILES) are rejected. The structures here were resolved against
PubChem records (see STRUCTURE_REGISTRY docstring) after the original
scaffold drafts were found to be different isomers (C15H10O8/myricetin-like).

Usage:
    .venv/bin/python benchmark/blinded_pilot/build_cases.py [--dry-run]
"""
from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests" / "fixtures" / "real_npmrd"
OUT = Path(__file__).resolve().parent

SEED = 20261102

# ---------------------------------------------------------------------------
# Structure registry: resolved against authoritative records 2026-11-02.
# The draft builder's scaffold SMILES were verified to be DIFFERENT isomers:
#   draft "quercetin" SMILES -> C15H10O8 (myricetin-like, hexahydroxyflavone)
#   draft "kaempferol" SMILES -> C15H10O7 (resokaempferol-like 3'-OH isomer)
#   draft "luteolin" SMILES  -> C15H10O7 (a flavonol = the true quercetin)
# The resolved structures below parse to the displayed formulas and match
# the PubChem records cited per entry.
# ---------------------------------------------------------------------------
STRUCTURE_REGISTRY = {
    "quercetin": {
        "smiles": "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12",
        "formula": "C15H10O7",
        # PubChem CID 5280343, IUPAC 2-(3,4-dihydroxyphenyl)-3,5,7-trihydroxychromen-4-one
        "structure_ref": "PubChem CID 5280343",
    },
    "kaempferol": {
        "smiles": "O=c1c(O)c(-c2ccc(O)cc2)oc2cc(O)cc(O)c12",
        "formula": "C15H10O6",
        # PubChem CID 5280863, IUPAC 3,5,7-trihydroxy-2-(4-hydroxyphenyl)chromen-4-one
        "structure_ref": "PubChem CID 5280863",
    },
    "luteolin": {
        "smiles": "O=c1cc(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12",
        "formula": "C15H10O6",
        # PubChem CID 5280445, IUPAC 2-(3,4-dihydroxyphenyl)-5,7-dihydroxychromen-4-one
        "structure_ref": "PubChem CID 5280445",
    },
}

TRIO = ["quercetin", "kaempferol", "luteolin"]


def validate_structure(name: str, smiles: str, displayed_formula: str, rdkit=None):
    """Parse SMILES, derive the formula, compare with the displayed one."""
    if rdkit is None:
        from rdkit import Chem
        from rdkit.Chem import rdMolDescriptors
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        raise ValueError(f"{name}: SMILES does not parse: {smiles}")
    derived = rdMolDescriptors.CalcMolFormula(m)
    if derived != displayed_formula:
        raise ValueError(
            f"{name}: formula mismatch — structure parses as {derived} "
            f"but display says {displayed_formula} (refuse to build)")
    return derived, Chem.MolToSmiles(m)


def canonical(smiles: str) -> str:
    from rdkit import Chem
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


def read_hsqc_assigned(path: Path):
    """Read the curated (ASSIGNED) fixture table — evaluator-side only."""
    rows = []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or line.startswith("position"):
            continue
        pos, dh, dc = line.split(",")
        rows.append({"position": pos, "dH": float(dh), "dC": float(dc)})
    return rows


def evidence_id(rng, used):
    """Stable sequential evidence IDs E###."""
    while True:
        eid = f"E{len(used)+1:03d}"
        if eid not in used:
            used.add(eid)
            return eid
        used.add(eid)


def build():
    from rdkit import Chem  # noqa: F401  (ensures hard dependency)
    from rdkit.Chem import rdMolDescriptors  # noqa: F401

    rng = random.Random(SEED)
    used_evidence: set[str] = set()
    cases: dict[str, dict] = {}
    keys: list[dict] = []
    worksheets: list[dict] = []

    # --- registry-wide chemistry validation first (fail fast) -------------
    for name, rec in STRUCTURE_REGISTRY.items():
        validate_structure(name, rec["smiles"], rec["formula"])
    canon_by_name = {n: canonical(r["smiles"]) for n, r in STRUCTURE_REGISTRY.items()}
    if len(set(canon_by_name.values())) != len(canon_by_name):
        raise ValueError("duplicate canonical structures in registry")

    # --- C001..C003: trio, one per true compound, nmr_only arm ------------
    # Each case's model packet = target compound's HSQC coordinates (unassigned)
    # vs all three candidates. Evidence that distinguishes candidates:
    #   presence/absence of H-3 cross-peak (flavone luteolin) and the B-ring
    #   pattern (kaempferol: one 2H-equivalent pair; quercetin/luteolin: three
    #   separate protons). See worksheets.
    trio_keys = {}
    for i, true_name in enumerate(TRIO):
        case_id = f"C{i+1:03d}"
        assigned = read_hsqc_assigned(FIX / STRUCTURE_REGISTRY.get(true_name, {}).get(
            "fixture", f"{true_name}_hsqc.csv") if "fixture" in STRUCTURE_REGISTRY.get(true_name, {}) else FIX / f"{true_name}_hsqc.csv")

        # Unassigned model packet: coordinates only, stable evidence IDs.
        model_peaks = []
        for row in assigned:
            eid = evidence_id(rng, used_evidence)
            model_peaks.append({"evidence_id": eid, "dH": row["dH"], "dC": row["dC"]})

        # Candidates: shuffled with recorded seed; answer label recorded in key.
        order = list(range(3))
        rng.shuffle(order)
        candidates = []
        answer_label = None
        for pos_idx, ci in enumerate(order):
            c_name = TRIO[ci]
            label = f"cand-{chr(97 + pos_idx)}"
            candidates.append({"label": label,
                               "smiles": STRUCTURE_REGISTRY[c_name]["smiles"],
                               "formula": STRUCTURE_REGISTRY[c_name]["formula"]})
            if c_name == true_name:
                answer_label = label

        case = {
            "case_id": case_id,
            "task": {
                "type": "candidate_ranking",
                "candidates": candidates,
            },
            "acquisition": {
                "modality": "HSQC (1H-detected 1H-13C single-bond correlations)",
                "solvent": "DMSO-d6",
                "field_strength_MHz": None,  # not recorded in fixture
                "note": "peak list is unassigned; coordinates rounded to 2 dp",
            },
            "evidence": {"HSQC": model_peaks},
        }
        cases[case_id] = case

        # Evaluator-only key
        trio_keys[case_id] = {
            "case_id": case_id,
            "true_compound": true_name,
            "answer_label": answer_label,
            "assigned_peaks": assigned,   # expert atom labels — evaluator only
            "decisive_evidence_proposal": {
                "status": "awaiting expert review",
                "proposal": DECISIVE_PROPOSAL,
            },
        }

        # Reviewer worksheet (evaluator-only)
        worksheets.append({
            "case_id": case_id,
            "true_compound": true_name,
            "candidate_structures": candidates,
            "evidence_with_labels": [
                {"evidence_id": p["evidence_id"], **{k: v for k, v in a.items() if k != "evidence_id"}}
                for p, a in zip(model_peaks, assigned)
            ],
            "decisive_observations_proposed": DECISIVE_PROPOSAL,
            "plausible_alternatives": (
                "kaempferol vs luteolin share C15H10O6 and differ only in the "
                "C-3 oxygen; separation relies on the presence (luteolin) vs "
                "absence (kaempferol) of an H-3 cross-peak — an ABSENCE "
                "argument whose strength the reviewer must weigh against "
                "possible weak/overlapped H-3."),
            "insufficient_evidence_field": None,
            "reviewer": None,
        })

    for cid, k in trio_keys.items():
        keys.append(k)

    # --- C004: designed abstention case -----------------------------------
    # Same trio candidates; the packet contains ONLY the A-ring peaks
    # (H-6/H-8), which are identical across all three candidates. Correct
    # behaviour = abstain and name the missing evidence. This is a DESIGNED
    # ablation, recorded as such; it is not a claim about real experiments.
    ka_assigned = read_hsqc_assigned(FIX / "kaempferol_hsqc.csv")
    aring = [r for r in ka_assigned if "'" not in r["position"]]
    model_peaks = []
    for row in aring:
        eid = evidence_id(rng, used_evidence)
        model_peaks.append({"evidence_id": eid, "dH": row["dH"], "dC": row["dC"]})
    order = list(range(3))
    rng.shuffle(order)
    candidates = []
    answer_label = None
    for pos_idx, ci in enumerate(order):
        c_name = TRIO[ci]
        label = f"cand-{chr(97 + pos_idx)}"
        candidates.append({"label": label,
                           "smiles": STRUCTURE_REGISTRY[c_name]["smiles"],
                           "formula": STRUCTURE_REGISTRY[c_name]["formula"]})
        if c_name == "kaempferol":
            answer_label = label
    cases["C004"] = {
        "case_id": "C004",
        "task": {"type": "candidate_ranking", "candidates": candidates},
        "acquisition": {
            "modality": "HSQC (1H-detected 1H-13C single-bond correlations)",
            "solvent": "DMSO-d6",
            "field_strength_MHz": None,
            "note": "peak list is unassigned; coordinates rounded to 2 dp; "
                    "packet may be incomplete — decide whether the evidence "
                    "suffices to rank",
        },
        "evidence": {"HSQC": model_peaks},
    }
    keys.append({
        "case_id": "C004",
        "true_compound": "kaempferol",
        "answer_label": None,           # deliberate: correct behaviour is to abstain
        "expected_behaviour": "abstain; name the missing decisive evidence "
                              "(B-ring 1H-13C pattern and/or H-3 presence)",
        "assigned_peaks": ka_assigned,
        "designed_ablation": "B-ring peaks removed by design; A-ring peaks "
                             "identical across all three candidates",
        "decisive_evidence_proposal": {
            "status": "awaiting expert review",
            "proposal": DECISIVE_PROPOSAL,
        },
    })
    worksheets.append({
        "case_id": "C004",
        "true_compound": "kaempferol",
        "candidate_structures": candidates,
        "evidence_with_labels": [
            {"evidence_id": p["evidence_id"], **{k: v for k, v in a.items() if k != "evidence_id"}}
            for p, a in zip(model_peaks, aring)
        ],
        "decisive_observations_proposed": (
            "None decisive in-packet: the two A-ring HSQC cross-peaks "
            "(H-6, H-8) are common to all three candidates. Designed "
            "abstention case."),
        "plausible_alternatives": "all three candidates remain plausible on "
                                  "the packet's evidence alone",
        "insufficient_evidence_field": "CONFIRM: is abstention the required "
                                       "behaviour, or would you rank?",
        "reviewer": None,
    })

    return cases, keys, worksheets


# Draft decisive-evidence proposal (expert review REQUIRED before scoring).
# An omitted peak in a curated table is NOT proof of experimental absence —
# the reviewer must weigh absences.
DECISIVE_PROPOSAL = (
    "PROPOSED (awaiting expert review): "
    "(1) Luteolin shows an H-3 cross-peak (~6.68/~102.9 ppm in the curated "
    "table); quercetin and kaempferol (flavonols) lack H-3. "
    "(2) B-ring pattern separates quercetin (catechol: three distinct "
    "B-ring protons) from kaempferol (single 2H-equivalent H-2'/H-6' pair "
    "at ~8.04/~130.4 and H-3'/H-5' at ~6.92/~115.1). "
    "(3) Caution: absence of H-3 in a curated table is weak evidence — "
    "overlapping or weak peaks must be considered; reviewer decides whether "
    "absence alone can decide or the case must be marked ambiguous."
)


def main() -> int:
    dry = "--dry-run" in sys.argv
    cases, keys, worksheets = build()

    # Final leak self-check on the model packets before writing
    LEAK_TOKENS = ["quercetin", "kaempferol", "luteolin", "answer", "abstention",
                   "reviewer", "fixture", ".csv", "partition", "smoke_test",
                   "true_compound", "answer_label", "expected_behaviour"]
    for cid, case in cases.items():
        blob = json.dumps(case).lower()
        for tok in LEAK_TOKENS:
            if tok in blob:
                raise ValueError(f"LEAK in {cid}: model packet contains '{tok}'")

    if not dry:
        (OUT / "cases").mkdir(exist_ok=True)
        (OUT / "keys").mkdir(exist_ok=True)
        (OUT / "review").mkdir(exist_ok=True)
        # clear stale outputs so the directory reflects this build exactly
        for p in (OUT / "cases").glob("*.json"):
            p.unlink()
        for cid, c in sorted(cases.items()):
            (OUT / "cases" / f"{cid}.json").write_text(json.dumps(c, indent=1, sort_keys=True) + "\n")
        (OUT / "keys" / "answer_keys.json").write_text(json.dumps(keys, indent=1, sort_keys=True) + "\n")
        (OUT / "review" / "worksheets.json").write_text(json.dumps(worksheets, indent=1, sort_keys=True) + "\n")
        # packaging hash over packets + keys in deterministic order
        h = hashlib.sha256()
        for p in sorted((OUT / "cases").glob("*.json")):
            h.update(p.read_bytes())
        h.update((OUT / "keys" / "answer_keys.json").read_bytes())
        (OUT / "PACKAGING.sha256").write_text(
            f"{h.hexdigest()}\nseed={SEED}\n")

    eligible = sorted(cases.keys())
    print(f"built {len(cases)} cases ({'dry-run' if dry else 'written'})")
    for cid in eligible:
        print(f"  {cid}: candidates={[c['label'] for c in cases[cid]['task']['candidates']]}"
              f" peaks={len(cases[cid]['evidence']['HSQC'])}")
    print(f"eligible set: {eligible}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
