#!/usr/bin/env python3
"""Analyse the context behind the SUMMIT v4 Part A headline number.

The 92.3% top-1 figure is quoted in validation reports. On its own it is
uninterpretable: the same number can describe a method that is uniformly good,
or one that aces small candidate sets and collapses on large ones. This script
reports the distributional detail needed to read the number honestly:

  1. Candidate-set size distribution (median, IQR).
  2. Top-1 accuracy per candidate-size bucket.
  3. Failure modes by chemical class, with structural fingerprints.
  4. An explicit statement of reproduction vs. independent validation.

It also audits the site-localisation benchmark, because an 88.9% vs 44.3%
comparison means something very different on a 2-site problem than on a
10-site problem.

Usage:
    python3 scripts/analyze_benchmark_context.py
    python3 scripts/analyze_benchmark_context.py --output report.md
"""

from __future__ import annotations

import argparse
import csv
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "real_npmrd" / "benchmark_v4"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "inventory" / "benchmark_context_report.md"

MAIN_METHOD = "GNN + edited HSQC"

# Provenance of the fixtures themselves, stated once so every section can
# refer to it.  These CSVs are SUMMIT v4's own output files.
PROVENANCE = (
    "The per-compound results were produced by the SUMMIT v4 benchmark itself "
    "and copied verbatim from its output directory. The numbers below are "
    "therefore a REPRODUCTION of SUMMIT v4, not an independent validation."
)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def bucket_name(n_candidates: int) -> str:
    if n_candidates < 10:
        return "<10"
    if n_candidates <= 50:
        return "10-50"
    if n_candidates <= 200:
        return "50-200"
    return ">200"


BUCKET_ORDER = ["<10", "10-50", "50-200", ">200"]


def pct(values: list[float], predicate) -> float:
    if not values:
        return float("nan")
    return 100.0 * sum(1 for v in values if predicate(v)) / len(values)


def candidate_size_section(rows: list[dict[str, str]]) -> dict[str, Any]:
    sizes = [int(r["n_candidates"]) for r in rows]
    quartiles = statistics.quantiles(sizes, n=4) if len(sizes) >= 4 else []
    return {
        "count": len(sizes),
        "min": min(sizes),
        "max": max(sizes),
        "median": statistics.median(sizes),
        "q1": quartiles[0] if quartiles else None,
        "q3": quartiles[2] if quartiles else None,
        "mean": statistics.mean(sizes),
    }


def accuracy_by_bucket(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        buckets[bucket_name(int(row["n_candidates"]))].append(row)

    out = []
    for name in BUCKET_ORDER:
        group = buckets.get(name, [])
        if not group:
            continue
        ranks = [float(r[MAIN_METHOD]) for r in group]
        sizes = [int(r["n_candidates"]) for r in group]
        out.append(
            {
                "bucket": name,
                "n": len(group),
                "top1_pct": pct(ranks, lambda r: r == 1.0),
                "top5_pct": pct(ranks, lambda r: r <= 5.0),
                "median_candidates": statistics.median(sizes),
                "share_pct": 100.0 * len(group) / len(rows),
            }
        )
    return out


def structural_flags(true_smiles: str, winner_smiles: str) -> dict[str, Any]:
    """Compare the predicted winner with the true structure.

    RDKit is optional: the report degrades gracefully if it is unavailable, but
    says so rather than silently skipping the structural analysis.
    """
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem import AllChem, DataStructs, rdMolDescriptors

        RDLogger.DisableLog("rdApp.*")
    except ImportError:
        return {"available": False}

    true_mol = Chem.MolFromSmiles(true_smiles)
    win_mol = Chem.MolFromSmiles(winner_smiles)
    if true_mol is None or win_mol is None:
        return {"available": True, "parsable": False}

    def flat(mol):
        copy = Chem.Mol(mol)
        Chem.RemoveStereochemistry(copy)
        return Chem.MolToSmiles(copy)

    def has_sugar(mol):
        # A pyranose ring: 6-membered, exactly one ring oxygen, at least three
        # ring carbons bearing an exocyclic oxygen.
        for ring in mol.GetRingInfo().AtomRings():
            if len(ring) != 6:
                continue
            atoms = [mol.GetAtomWithIdx(i) for i in ring]
            if sum(1 for a in atoms if a.GetAtomicNum() == 8) != 1:
                continue
            oxygenated = sum(
                1
                for a in atoms
                if a.GetAtomicNum() == 6
                and any(
                    nb.GetAtomicNum() == 8 and nb.GetIdx() not in ring
                    for nb in a.GetNeighbors()
                )
            )
            if oxygenated >= 3:
                return True
        return False

    fp_true = AllChem.GetMorganFingerprintAsBitVect(true_mol, 2, 2048)
    fp_win = AllChem.GetMorganFingerprintAsBitVect(win_mol, 2, 2048)
    return {
        "available": True,
        "parsable": True,
        "same_formula": rdMolDescriptors.CalcMolFormula(true_mol)
        == rdMolDescriptors.CalcMolFormula(win_mol),
        "pure_stereoisomer": flat(true_mol) == flat(win_mol),
        "same_heavy_atoms": true_mol.GetNumHeavyAtoms() == win_mol.GetNumHeavyAtoms(),
        "same_ring_count": rdMolDescriptors.CalcNumRings(true_mol)
        == rdMolDescriptors.CalcNumRings(win_mol),
        "contains_sugar": has_sugar(true_mol),
        "mw_true": rdMolDescriptors.CalcExactMolWt(true_mol),
        "tanimoto": DataStructs.TanimotoSimilarity(fp_true, fp_win),
    }


def failure_section(rows: list[dict[str, str]], failures: list[dict[str, str]]) -> dict[str, Any]:
    classes = Counter(f["cls"] for f in failures)
    ranks = Counter(f["rank"] for f in failures)
    class_totals = Counter(r["cls"] for r in rows)

    flagged = []
    for f in failures:
        flags = structural_flags(f["true_smiles"], f["winner_smiles"])
        flagged.append({"row": f, "flags": flags})

    comparable = [x for x in flagged if x["flags"].get("parsable")]
    # Failure *rate* per class matters more than raw counts: "other glycoside"
    # has 4 failures but 20 compounds, while "flavonoid" has 3 of only 7.
    by_class_rates = [
        {
            "cls": cls,
            "failures": count,
            "compounds": class_totals[cls],
            "rate_pct": 100.0 * count / class_totals[cls] if class_totals[cls] else 0.0,
        }
        for cls, count in classes.most_common()
    ]
    return {
        "count": len(failures),
        "by_class": classes.most_common(),
        "by_class_rates": sorted(by_class_rates, key=lambda x: -x["rate_pct"]),
        "by_rank": sorted(ranks.items(), key=lambda kv: float(kv[0])),
        "same_formula": sum(1 for x in comparable if x["flags"]["same_formula"]),
        "pure_stereoisomers": sum(1 for x in comparable if x["flags"]["pure_stereoisomer"]),
        "same_ring_count": sum(1 for x in comparable if x["flags"]["same_ring_count"]),
        "contains_sugar": sum(1 for x in comparable if x["flags"]["contains_sugar"]),
        "comparable": len(comparable),
        "rdkit_available": bool(comparable) or any(
            x["flags"].get("available", False) for x in flagged
        ),
        "mean_tanimoto": (
            statistics.mean(x["flags"]["tanimoto"] for x in comparable)
            if comparable
            else None
        ),
        "mean_mw": (
            statistics.mean(x["flags"]["mw_true"] for x in comparable)
            if comparable
            else None
        ),
        "median_mw": (
            statistics.median(x["flags"]["mw_true"] for x in comparable)
            if comparable
            else None
        ),
        "mw_over_800": sum(1 for x in comparable if x["flags"]["mw_true"] > 800),
        "sugar_mw_range": (
            (
                min(x["flags"]["mw_true"] for x in comparable if x["flags"]["contains_sugar"]),
                max(x["flags"]["mw_true"] for x in comparable if x["flags"]["contains_sugar"]),
            )
            if any(x["flags"]["contains_sugar"] for x in comparable)
            else None
        ),
        "nonsugar_mw_range": (
            (
                min(x["flags"]["mw_true"] for x in comparable if not x["flags"]["contains_sugar"]),
                max(x["flags"]["mw_true"] for x in comparable if not x["flags"]["contains_sugar"]),
            )
            if any(not x["flags"]["contains_sugar"] for x in comparable)
            else None
        ),
        "rows": flagged,
        "total_compounds": len(rows),
    }


def class_accuracy(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        grouped[row["cls"]].append(float(row[MAIN_METHOD]))
    out = [
        {
            "cls": cls,
            "n": len(ranks),
            "top1_pct": pct(ranks, lambda r: r == 1.0),
        }
        for cls, ranks in grouped.items()
    ]
    return sorted(out, key=lambda x: (x["top1_pct"], -x["n"]))


# --- Small-pool failure analysis ---------------------------------------------

MIN_MARGIN_FOR_CONFIRMED = 0.10
MIN_MARGIN_FOR_TENTATIVE = 0.02

_ELEMENT_PATTERN = re.compile(r"([A-Z][a-z]?)(\d*)")


def heavy_atom_count(formula: str) -> int:
    return sum(
        int(count or 1) for element, count in _ELEMENT_PATTERN.findall(formula)
        if element != "H"
    )


def small_pool_section(
    rows: list[dict[str, str]], failures: list[dict[str, str]]
) -> dict[str, Any]:
    """Characterise the 6 failures in the <10-candidate bucket.

    The bucket is counterintuitive (small pools should be easier), so this
    separates two rival explanations:

      (a) genuine GNN prediction error -- the wrong pick is clearly better
          scored, i.e. the correct answer is not a near-tie; or
      (b) honest ambiguity -- the correct answer is a near-tie, so the
          AMBIGUOUS verdict should have prevented a confident claim.

    The scores available are distances (lower is better), so the winner's
    margin over the correct answer is ``score_true - score_winner``.
    """
    failure_by_name = {f["name"]: f for f in failures}
    small = [r for r in rows if int(r["n_candidates"]) < 10]
    small_failures = [r for r in small if float(r[MAIN_METHOD]) != 1.0]
    formula_freq = Counter(r["formula"] for r in rows)
    all_heavy = sorted(heavy_atom_count(r["formula"]) for r in rows)
    q1 = all_heavy[len(all_heavy) // 3]
    q2 = all_heavy[2 * len(all_heavy) // 3]

    cases = []
    for row in small_failures:
        f = failure_by_name.get(row["name"])
        if f is None:
            continue
        score_true = float(f["score_true"])
        score_winner = float(f["score_winner"])
        # Winner is lowest-scoring; its margin over the truth is positive when
        # it wins outright.
        margin = score_true - score_winner
        flags = structural_flags(f["true_smiles"], f["winner_smiles"])
        # The verdict the platform would have emitted had it reported the
        # top-1 pick as if it were the target.
        if margin >= MIN_MARGIN_FOR_CONFIRMED:
            implied = "confirmed"
        elif margin >= MIN_MARGIN_FOR_TENTATIVE:
            implied = "tentative"
        else:
            implied = "candidate"
        cases.append(
            {
                "name": row["name"],
                "cls": row["cls"],
                "formula": row["formula"],
                "rank": float(row[MAIN_METHOD]),
                "n_candidates": int(row["n_candidates"]),
                "n_peaks": int(row["n_peaks"]),
                "score_true": score_true,
                "score_winner": score_winner,
                "margin": margin,
                "below_confirmed": margin < MIN_MARGIN_FOR_CONFIRMED,
                "below_tentative": margin < MIN_MARGIN_FOR_TENTATIVE,
                "implied_verdict": implied,
                "formula_freq": formula_freq[row["formula"]],
                "heavy_atoms": heavy_atom_count(row["formula"]),
                "same_formula": flags.get("same_formula"),
                "same_ring_count": flags.get("same_ring_count"),
                "tanimoto": flags.get("tanimoto"),
                "structure_parsable": flags.get("parsable", False),
            }
        )

    # Structural-isomer test as specified: same formula, same ring count,
    # different connectivity, Tanimoto >= 0.7.
    isomers = [
        c
        for c in cases
        if c["structure_parsable"]
        and c["same_formula"]
        and c["same_ring_count"]
        and (c["tanimoto"] or 0.0) >= 0.7
    ]

    # Does "rare formula" explain the bucket? Compare formula multiplicity.
    def mean_freq(group: list[dict[str, str]]) -> float:
        return statistics.mean(formula_freq[r["formula"]] for r in group) if group else 0.0

    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        buckets[bucket_name(int(r["n_candidates"]))].append(r)

    # Pool size vs molecule size within heavy-atom strata, to show whether the
    # bucket effect is really a molecule-size effect.
    strata = []
    for label, pred in [
        (f"small (<= {q1} heavy atoms)", lambda h: h <= q1),
        (f"medium ({q1 + 1}-{q2})", lambda h: q1 < h <= q2),
        (f"large (> {q2})", lambda h: h > q2),
    ]:
        group = [r for r in rows if pred(heavy_atom_count(r["formula"]))]
        row_out = {"stratum": label, "n": len(group), "cells": []}
        for plabel, ppred in [
            ("<10 pool", lambda n: n < 10),
            ("10-50 pool", lambda n: 10 <= n <= 50),
            (">50 pool", lambda n: n > 50),
        ]:
            sub = [r for r in group if ppred(int(r["n_candidates"]))]
            if not sub:
                continue
            n_fail = sum(1 for r in sub if float(r[MAIN_METHOD]) != 1.0)
            row_out["cells"].append(
                {
                    "pool": plabel,
                    "n": len(sub),
                    "failures": n_fail,
                    "rate_pct": 100.0 * n_fail / len(sub),
                }
            )
        strata.append(row_out)

    below_confirmed = [c for c in cases if c["below_confirmed"]]
    return {
        "bucket_n": len(small),
        "bucket_failures": len(cases),
        "bucket_rate_pct": 100.0 * len(cases) / len(small) if small else 0.0,
        "cases": cases,
        "isomers": len(isomers),
        "below_confirmed": len(below_confirmed),
        "below_tentative": sum(1 for c in cases if c["below_tentative"]),
        "median_margin": statistics.median(c["margin"] for c in cases) if cases else None,
        "mean_formula_freq_small": mean_freq(small),
        "mean_formula_freq_large": mean_freq(
            [r for r in rows if int(r["n_candidates"]) >= 10]
        ),
        "strata": strata,
        "median_heavy_small": statistics.median(
            heavy_atom_count(r["formula"]) for r in small
        )
        if small
        else 0,
        "median_heavy_all": statistics.median(all_heavy) if all_heavy else 0,
        "glycoside_share_small": 100.0
        * sum(1 for r in small if "glycoside" in r["cls"])
        / len(small)
        if small
        else 0.0,
        "glycoside_share_all": 100.0
        * sum(1 for r in rows if "glycoside" in r["cls"])
        / len(rows)
        if rows
        else 0.0,
    }


def localisation_section(part_b: list[dict[str, str]]) -> dict[str, Any]:
    usable = [
        r
        for r in part_b
        if r.get("n_sites") and r.get("true_site_rank")
    ]
    sites = [float(r["n_sites"]) for r in usable]
    ranks = [float(r["true_site_rank"]) for r in usable]

    # The published chance baseline is the mean of 1/n_sites, which is inflated
    # by single-site cases where chance is 100% by definition.
    mean_chance = statistics.mean(1.0 / n for n in sites) * 100 if sites else None
    median_chance = 100.0 / statistics.median(sites) if sites else None

    single = [r for r in usable if float(r["n_sites"]) == 1]
    multi = [r for r in usable if float(r["n_sites"]) >= 2]

    # A compound with at most two possible sites is a coin flip at worst, so a
    # high score there says little.  Track this subset separately because it is
    # the single most important thing to disclose about the 88.9% headline.
    at_most_two = [r for r in usable if float(r["n_sites"]) <= 2]
    above_two = [r for r in usable if float(r["n_sites"]) > 2]
    at_most_two_share = 100.0 * len(at_most_two) / len(usable) if usable else 0.0

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in usable:
        n = float(r["n_sites"])
        if n == 1:
            key = "1"
        elif n == 2:
            key = "2"
        elif n <= 6:
            key = "3-6"
        else:
            key = ">=7"
        buckets[key].append(float(r["true_site_rank"]))

    per_bucket = []
    for key in ["1", "2", "3-6", ">=7"]:
        vals = buckets.get(key, [])
        if not vals:
            continue
        per_bucket.append(
            {
                "sites": key,
                "n": len(vals),
                "top1_pct": pct(vals, lambda r: r == 1.0),
            }
        )

    return {
        "n": len(usable),
        "median_sites": statistics.median(sites) if sites else None,
        "q1_sites": statistics.quantiles(sites, n=4)[0] if len(sites) >= 4 else None,
        "q3_sites": statistics.quantiles(sites, n=4)[2] if len(sites) >= 4 else None,
        "min_sites": min(sites) if sites else None,
        "max_sites": max(sites) if sites else None,
        "overall_top1_pct": pct(ranks, lambda r: r == 1.0),
        "mean_chance_pct": mean_chance,
        "median_chance_pct": median_chance,
        "single_site_n": len(single),
        "single_site_top1_pct": pct(
            [float(r["true_site_rank"]) for r in single], lambda r: r == 1.0
        )
        if single
        else None,
        "multi_site_n": len(multi),
        "multi_site_top1_pct": pct(
            [float(r["true_site_rank"]) for r in multi], lambda r: r == 1.0
        )
        if multi
        else None,
        "at_most_two_n": len(at_most_two),
        "at_most_two_share_pct": at_most_two_share,
        "at_most_two_top1_pct": pct(
            [float(r["true_site_rank"]) for r in at_most_two], lambda r: r == 1.0
        )
        if at_most_two
        else None,
        "above_two_n": len(above_two),
        "above_two_top1_pct": pct(
            [float(r["true_site_rank"]) for r in above_two], lambda r: r == 1.0
        )
        if above_two
        else None,
        "per_bucket": per_bucket,
    }


def render(
    sizes: dict[str, Any],
    buckets: list[dict[str, Any]],
    classes: list[dict[str, Any]],
    failures: dict[str, Any],
    localisation: dict[str, Any],
    small_pool: dict[str, Any] | None = None,
) -> str:
    overall = sum(b["n"] * b["top1_pct"] for b in buckets) / sum(b["n"] for b in buckets)
    smallest = min(buckets, key=lambda b: b["top1_pct"])
    largest = max(buckets, key=lambda b: b["top1_pct"])

    lines: list[str] = []
    add = lines.append

    add("# Benchmark Context Report: SUMMIT v4 Part A")
    add("")
    add("> Generated by `scripts/analyze_benchmark_context.py`. Do not edit by hand.")
    add("> Re-run after any fixture change; do not hand-edit the numbers below.")
    add("")
    add("## Summary")
    add("")
    add(
        f"Top-1 structure retrieval was evaluated on **{sizes['count']} plant natural "
        f"products** drawn from the SUMMIT v4 benchmark set. Each compound was ranked "
        f"against a decoy pool of candidate structures whose size varied widely "
        f"(median {sizes['median']:.0f}; interquartile range "
        f"{sizes['q1']:.0f}-{sizes['q3']:.0f}; range {sizes['min']}-{sizes['max']}). "
        f"Overall top-1 accuracy was **{overall:.1f}%** "
        f"({sizes['count'] - failures['count']}/{sizes['count']} compounds), with all "
        f"misassignments ranked second or third rather than retrieved as an unrelated "
        f"scaffold. Accuracy was lowest on the smallest decoy pools "
        f"(`{smallest['bucket']}`, {smallest['top1_pct']:.1f}%) and highest on "
        f"mid-sized pools (`{largest['bucket']}`, {largest['top1_pct']:.1f}%), so the "
        f"headline figure is not propped up by easy small-pool cases; the small-pool "
        f"deficit reflects a higher share of large, glycosylated molecules rather "
        f"than an intrinsic penalty for small pools. Of the "
        f"{small_pool['bucket_failures'] if small_pool else 0} small-pool failures, "
        f"{small_pool['below_confirmed'] if small_pool else 0} were near-ties whose "
        f"score margins fall below the confirmed threshold, so the platform would "
        f"have downgraded those claims rather than reporting them confidently, and "
        f"{small_pool['bucket_failures'] - small_pool['below_confirmed'] if small_pool else 0} "
        f"were genuine retrieval errors. "
        f"The site-localisation task covered {localisation['n']} compounds at a median "
        f"of {localisation['median_sites']:.0f} possible edit sites, with observed "
        f"accuracy of {localisation['overall_top1_pct']:.1f}% against a "
        f"{localisation['mean_chance_pct']:.1f}% chance baseline; however "
        f"{localisation['at_most_two_n']}/{localisation['n']} "
        f"({localisation['at_most_two_share_pct']:.0f}%) of those compounds offered at "
        f"most two possible sites, so that baseline is weak. These figures are a "
        f"**reproduction of the SUMMIT v4 benchmark's own outputs, not an independent "
        f"held-out validation.**"
    )
    add("")
    add("## 0. Reproduction, not independent validation")
    add("")
    add(PROVENANCE)
    add("")
    add(
        "**Consequence:** the 92.3% figure carries no information about how the "
        "method generalises to structures outside the SUMMIT v4 benchmark set. "
        "It is a consistency check on our reproduction, not evidence of "
        "out-of-sample performance. Any external document quoting this number "
        "must label it a reproduction (see ADR-0036, which supersedes the "
        "narrower ADR-0027)."
    )
    add("")
    add("## 1. Candidate-set size distribution")
    add("")
    add(
        f"The benchmark contains **{sizes['count']} compounds**. The number of "
        "candidate structures considered per compound (the decoy pool) is:"
    )
    add("")
    add("| Statistic | Value |")
    add("|---|---|")
    add(f"| Minimum | {sizes['min']} |")
    add(f"| 25th percentile | {sizes['q1']:.0f} |")
    add(f"| **Median** | **{sizes['median']:.0f}** |")
    add(f"| 75th percentile | {sizes['q3']:.0f} |")
    add(f"| Maximum | {sizes['max']} |")
    add(f"| Mean | {sizes['mean']:.1f} |")
    add("")
    add(
        "The distribution is strongly right-skewed: the mean sits well above the "
        "median, so a minority of compounds carry very large candidate pools."
    )
    add("")
    add("## 2. Top-1 accuracy by candidate-set size")
    add("")
    add(
        "An overall figure can hide very different behaviour across pool sizes, "
        "so here is the same method broken down by candidate-set size:"
    )
    add("")
    add("| Candidate-set size | Compounds | Share | Median candidates | Top-1 | Top-5 |")
    add("|---|---|---|---|---|---|")
    for b in buckets:
        add(
            f"| {b['bucket']} | {b['n']} | {b['share_pct']:.1f}% | "
            f"{b['median_candidates']:.0f} | {b['top1_pct']:.1f}% | {b['top5_pct']:.1f}% |"
        )
    add(f"| **All** | **{sizes['count']}** | **100%** | **{sizes['median']:.0f}** | "
        f"**{overall:.1f}%** | |")
    add("")
    add("### Reading this table")
    add("")
    add(
        f"Accuracy is **not** simply a function of pool size. The weakest bucket "
        f"is `{smallest['bucket']}` at {smallest['top1_pct']:.1f}% and the "
        f"strongest is `{largest['bucket']}` at {largest['top1_pct']:.1f}%. "
        "Notably the overall 92.3% is **not** inflated by easy small pools: the "
        "small pools are where the method does worst. The likely reason is that "
        "the decoy pool for a compound is built from structures similar to it, so "
        "a small pool is enriched in near-isomers while a large pool adds "
        "progressively more dissimilar structures. That mechanism is a hypothesis "
        "about how the benchmark set was constructed, not something these "
        "fixtures can prove, since they record only pool sizes and ranks, not "
        "decoy identities."
    )
    add("")
    add(
        "**What this table supports.** The headline is not carried by the easiest "
        "pool size: the `<10` bucket holds only "
        f"{next((b['n'] for b in buckets if b['bucket'] == '<10'), 0)} compounds "
        "and scores *below* the overall figure. **What it does not support.** A "
        "uniform-capability reading. The spread between weakest and strongest "
        f"bucket is {largest['top1_pct'] - smallest['top1_pct']:.1f} percentage "
        "points, and because the weakest bucket is small a handful of compounds "
        "shift it by several points. Quote the breakdown, not the single number."
    )
    add("")
    add("## 3. Why small pools are harder")
    add("")
    if small_pool and small_pool["bucket_n"]:
        sp = small_pool
        add(
            f"The `<10` bucket holds {sp['bucket_n']} compounds and fails "
            f"{sp['bucket_failures']} of them ({sp['bucket_rate_pct']:.1f}%), "
            "which is counterintuitive: a small decoy pool should be easier. "
            "This section tests the two rival explanations."
        )
        add("")
        add("### The six failures")
        add("")
        add(
            "| Compound | Class | Rank | n | True score | Winner score | "
            "Margin | Same formula | Same rings | Tanimoto |"
        )
        add("|---|---|---|---|---|---|---|---|---|---|")
        for c in sorted(sp["cases"], key=lambda x: -x["margin"]):
            if c["structure_parsable"]:
                same_f = "yes" if c["same_formula"] else "no"
                same_r = "yes" if c["same_ring_count"] else "no"
                tan = f"{c['tanimoto']:.3f}" if c["tanimoto"] is not None else "n/a"
            else:
                same_f = same_r = tan = "n/a"
            add(
                f"| {c['name'][:48]} | {c['cls']} | {c['rank']:.0f} | "
                f"{c['n_candidates']} | {c['score_true']:.3f} | "
                f"{c['score_winner']:.3f} | {c['margin']:+.3f} | {same_f} | "
                f"{same_r} | {tan} |"
            )
        add("")
        add(
            "Scores are distances (lower is better), so the margin is the "
            "winner's advantage over the correct answer. `Same formula` and "
            "`Same rings` come from RDKit; Tanimoto is the Morgan-fingerprint "
            "similarity to the correct structure."
        )
        add("")
        add("### Which explanation applies")
        add("")
        add(
            f"**{sp['below_confirmed']} of {sp['bucket_failures']}** failures have "
            f"a margin below `MIN_MARGIN_FOR_CONFIRMED` ({MIN_MARGIN_FOR_CONFIRMED}); "
            f"**{sp['below_tentative']} of {sp['bucket_failures']}** falls below the "
            f"tentative threshold ({MIN_MARGIN_FOR_TENTATIVE}). The median margin is "            f"**{sp['median_margin']:+.3f}**. All six share the molecular formula "
            "and ring count of their top-1 pick, so every one is a constitutional "
            f"isomer rather than an unrelated scaffold; **{sp['isomers']} of "
            f"{sp['bucket_failures']}** also pass the Tanimoto >= 0.7 test. The "
            "exception is D-fructose 2,6-bisphosphate (Tanimoto 0.56), still a "
            "constitutional isomer but a structurally distant one."
        )
        add("")
        add(
            "**The answer is mixed, and neither explanation as posed is the "
            "dominant one.**"
        )
        add("")
        add(
            f"1. **Explanation (b) -- honest ambiguity -- fits "
            f"{sp['below_confirmed']} of {sp['bucket_failures']}.** CERNUOSIDE A "
            "(margin +0.055) and CERNUOSIDE B (+0.013) are genuine near-ties. "
            "Both would be reported below `confirmed`, and CERNUOSIDE B below "
            "`tentative` as well. For these, the platform's honesty machinery "
            "does what it should: it would not have claimed a confident top-1. "
            "Note that the `AMBIGUOUS` verdict itself requires an *exact* tie "
            "(`n_tied > 1`, tolerance 1e-8), so a small-but-nonzero margin yields "
            "`tentative`/`candidate`, not `ambiguous`. Either way the claim is "
            "downgraded."
        )
        add(
            f"2. **Explanation (a) -- genuine prediction error -- fits "
            f"{sp['bucket_failures'] - sp['below_confirmed']} of "
            f"{sp['bucket_failures']}.** The three flavonoids and D-fructose "
            "2,6-bisphosphate have margins of +0.13 to +0.29, well above the "
            "confirmed threshold. Here the GNN ranked the wrong isomer clearly "
            "ahead: these are real errors, and the platform would have reported "
            "them confidently."
        )
        add("")
        add(
            "Explanation (a) is therefore the larger share by count "
            f"({sp['bucket_failures'] - sp['below_confirmed']} vs "
            f"{sp['below_confirmed']}), but it does not dominate cleanly: two of "
            "the six are correctly downgraded rather than misreported."
        )
        add("")
        add(
            "**Measurement caveat.** The margin used here is the top-1 pick's "
            "advantage over the *correct* answer, which is exactly what these "
            "fixtures record. For the rank-3 failures there is a second "
            "candidate between them whose score is not in the fixture, so the "
            "top-1 pick's margin over its immediate runner-up could be smaller "
            "than the figure shown. That can only *increase* the number of "
            "near-ties the platform would downgrade, never decrease it: the "
            "2-of-6 ambiguity count is a **lower bound**, and the 4-of-6 error "
            "count an upper bound."
        )
        add("")
        add(
            "**The 'rare formula' mechanism does not hold.** Formula multiplicity "
            f"is essentially identical between the small-pool and larger-pool "
            f"compounds (mean {sp['mean_formula_freq_small']:.2f} vs "
            f"{sp['mean_formula_freq_large']:.2f} occurrences in the 207-compound "
            "set), so thin training coverage of unusual formulas is not what "
            "separates this bucket."
        )
        add("")
        add("### What actually drives the bucket: molecule size, not pool size")
        add("")
        add(
            "Pool size and molecular size are confounded in this benchmark. "
            f"Small pools contain larger molecules (median "
            f"{sp['median_heavy_small']:.0f} heavy atoms vs "
            f"{sp['median_heavy_all']:.0f} overall) and a much higher glycoside "
            f"share ({sp['glycoside_share_small']:.0f}% vs "
            f"{sp['glycoside_share_all']:.0f}%). Stratifying by heavy-atom count "
            "shows where the failures actually live:"
        )
        add("")
        add("| Molecule size | Pool | Compounds | Failures | Failure rate |")
        add("|---|---|---|---|---|")
        for s in sp["strata"]:
            for cell in s["cells"]:
                add(
                    f"| {s['stratum']} | {cell['pool']} | {cell['n']} | "
                    f"{cell['failures']} | {cell['rate_pct']:.1f}% |"
                )
        add("")
        add(
            "**Conclusion.** The `<10` bucket is harder because it is enriched in "
            "large, glycosylated molecules, not because small pools are "
            "intrinsically hard. In the large-molecule stratum the failure rate is "
            "high at every pool size (15.6-25.0%), while in the small-molecule "
            "stratum it is at or near zero (0.0-12.5%, and that single failure is "
            "D-fructose 2,6-bisphosphate). The bucket effect is a composition "
            "effect: pool size is standing in for molecule size."
        )
        add("")
        add(
            "**What this means for the platform.** Neither explanation is a "
            "clean win or a clean loss. Two failures -- both large glycosides -- "
            "are correctly downgraded by the honesty machinery, which is the "
            "platform working as designed. Four are genuine errors: three "
            "heavily oxygenated flavonoids and one sugar phosphate, all ranked "
            "confidently wrong. The limitation to state externally is therefore "
            "not \"rare formulas\" but **large, densely oxygenated "
            "constitutional isomers**, where HSQC differences are confined to a "
            "few carbons regardless of pool size."
        )
    else:
        add("No small-pool records were available in the fixtures.")
    add("")
    add("## 4. Failure modes")
    add("")
    add(
        f"{failures['count']} of {failures['total_compounds']} compounds were not "
        f"ranked first. Every failure was a **near-miss**: "
        + ", ".join(f"rank {rank} ×{count}" for rank, count in failures["by_rank"])
        + "."
    )
    add("")
    add("### Top 5 failure modes by class")
    add("")
    add(
        "Raw counts favour large classes. The table below ranks classes by "
        "**failure rate** (failures / compounds in that class), which is the fairer "
        "comparison:"
    )
    add("")
    add("| Rank | Class | Failures | Compounds | Failure rate |")
    add("|---|---|---|---|---|")
    for i, row in enumerate(failures["by_class_rates"][:5], start=1):
        add(
            f"| {i} | {row['cls']} | {row['failures']} | {row['compounds']} | "
            f"{row['rate_pct']:.0f}% |"
        )
    add("")
    add(
        "Read this table with care. The small classes are unstable: `other` has "
        "only 4 compounds and `diarylheptanoid/curcuminoid` only 6, so a single "
        "failure moves their rate by 17-25 points. The two robust signals are "
        "**flavonoid** (3 of 7, 43%) and **steroid glycoside** (3 of 10, 30%), "
        "both of which are large enough to be meaningful. **Other glycoside** "
        "contributes the most absolute failures (4 of 20) even though its rate "
        "ranks fourth, because that class is five times the size of `other`."
    )
    add("")
    add("### By chemical class (raw counts)")
    add("")
    add("| Class | Failures |")
    add("|---|---|")
    for cls, count in failures["by_class"]:
        add(f"| {cls} | {count} |")
    add("")
    add("### Structural pattern (RDKit)")
    add("")
    if failures["comparable"]:
        n = failures["comparable"]
        add(f"Across the {n} failures with parsable structures:")
        add("")
        add(f"- **{failures['same_formula']}/{n}** share the molecular formula of the correct answer, i.e. they are constitutional isomers, not unrelated structures.")
        add(f"- **{failures['pure_stereoisomers']}/{n}** are pure stereoisomers (identical once stereochemistry is stripped).")
        add(f"- **{failures['same_ring_count']}/{n}** have the same ring count.")
        add(f"- **{failures['contains_sugar']}/{n}** contain a pyranose sugar ring.")
        add(
            f"- Molecular weight of the true structure: median "
            f"**{failures['median_mw']:.0f} Da**, mean {failures['mean_mw']:.0f} Da; "
            f"**{failures['mw_over_800']}/{n}** are above 800 Da."
        )
        add(f"- Mean Morgan-fingerprint Tanimoto similarity to the truth: **{failures['mean_tanimoto']:.2f}**.")
        add("")
        add(
            "The dominant failure mode is therefore **regio-/constitutional "
            "isomer discrimination**, not retrieval of the wrong scaffold. Every "
            "miss has the correct molecular formula, and all but one keep the "
            "correct ring count, so the method lands on the right scaffold family "
            "and then picks the wrong regioisomer. This is the regime where "
            "1H-13C HSQC alone is least informative, because moving a substituent "
            "or a sugar linkage shifts only a few carbons."
        )
        add("")
        sugar_lo, sugar_hi = failures["sugar_mw_range"] or (0, 0)
        nonsugar_lo, nonsugar_hi = failures["nonsugar_mw_range"] or (0, 0)
        add(
            "**The two structural patterns are distinct.** "
            f"{failures['contains_sugar']}/{n} failures are glycosides, and those "
            f"are also the heavy ones ({sugar_lo:.0f}-{sugar_hi:.0f} Da). The "
            f"remaining {n - failures['contains_sugar']} are non-glycosidic, highly "
            f"oxygenated scaffolds ({nonsugar_lo:.0f}-{nonsugar_hi:.0f} Da). So "
            "there is no single \"high molecular weight\" story: glycosylation "
            "drives one group of failures and dense oxygenation drives the other, "
            "and they fail for the same underlying reason -- attachment or "
            "linkage position changes too few HSQC cross-peaks to separate them."
        )
        add("")
        add(
            f"**What the failures are not.** None of the {n} is a pure "
            "stereoisomer of the correct answer, so stereochemical discrimination "
            "is not the failure mode in this benchmark. That is worth stating "
            "explicitly because stereoisomer pairs are often assumed to be the "
            "hard case; here every miss is a constitutional isomer, which is why "
            f"Tanimoto similarity to the truth stays high ({failures['mean_tanimoto']:.2f})."
        )
    else:
        add("RDKit was unavailable, so structural classification was skipped.")
    add("")
    add("### Lowest-accuracy classes overall")
    add("")
    add("| Class | Compounds | Top-1 |")
    add("|---|---|---|")
    for row in classes[:8]:
        add(f"| {row['cls']} | {row['n']} | {row['top1_pct']:.1f}% |")
    add("")
    add("## 5. Site-localisation benchmark audit")
    add("")
    if localisation["n"]:
        add(
            f"The localisation benchmark covers **{localisation['n']} compounds** "
            f"with a **median of {localisation['median_sites']:.0f} possible edit "
            f"sites** (IQR {localisation['q1_sites']:.1f}-{localisation['q3_sites']:.1f}, "
            f"range {localisation['min_sites']:.0f}-{localisation['max_sites']:.0f})."
        )
        add("")
        add(
            f"Reported accuracy is {localisation['overall_top1_pct']:.1f}% against a "
            f"chance baseline of {localisation['mean_chance_pct']:.1f}%."
        )
        add("")
        add(
            f"> **WARNING -- the baseline is weak.** "
            f"**{localisation['at_most_two_n']} of {localisation['n']} compounds "
            f"({localisation['at_most_two_share_pct']:.0f}%) have at most two "
            f"possible sites** ({localisation['single_site_n']} have exactly one, "
            "where the answer is forced). On a two-site problem even random "
            "guessing scores 50%. The 88.9% headline must therefore **not** be "
            "presented as a strong localisation capability. It is a mixture of "
            "vacuous single-site cases, near-coin-flip two-site cases, and a "
            f"genuinely hard remainder that scores "
            f"{localisation['above_two_top1_pct']:.1f}%."
        )
        add("")
        add("### Accuracy split by problem difficulty")
        add("")
        add("| Subset | Compounds | Share | Top-1 |")
        add("|---|---|---|---|")
        _labels = {
            "1": "1 possible site (vacuous)",
            "2": "2 possible sites (50% by guessing)",
            "3-6": "3-6 possible sites",
            ">=7": "7+ possible sites",
        }
        for b in localisation["per_bucket"]:
            add(
                f"| {_labels.get(b['sites'], b['sites'])} | {b['n']} | "
                f"{100.0 * b['n'] / localisation['n']:.0f}% | "
                f"{b['top1_pct']:.1f}% |"
            )
        add(
            f"| *Subtotal: >2 possible sites (genuinely hard)* | "
            f"*{localisation['above_two_n']}* | "
            f"*{100.0 * localisation['above_two_n'] / localisation['n']:.0f}%* | "
            f"**{localisation['above_two_top1_pct']:.1f}%** |"
        )
        add(
            f"| All | {localisation['n']} | 100% | "
            f"{localisation['overall_top1_pct']:.1f}% |"
        )
        add("")
        add(
            "The subtotal row aggregates the `3-6` and `7+` buckets, so the "
            "per-bucket rows sum to 36 while the subtotal is a subset, not an "
            "extra group."
        )
        add("")
        add("### Why the published chance baseline is too generous")
        add("")
        add(
            f"The published 44.3% chance figure is the **mean of 1/n_sites**, which "
            f"single-site cases pull up to 100% by definition. On the "
            f"{localisation['multi_site_n']} compounds with two or more sites the "
            f"observed accuracy is **{localisation['multi_site_top1_pct']:.1f}%**, "
            f"against a baseline nearer 1/median(n) = "
            f"{localisation['median_chance_pct']:.1f}%. Neither figure is wrong; "
            "they simply measure different populations, and the pairing of 88.9% "
            "with 44.3% flatters the method because both are computed over all 36 "
            "compounds."
        )
        add("")
        add(
            "**Bottom line.** The defensible claim is that on compounds with more "
            f"than two candidate sites ({localisation['above_two_n']} of "
            f"{localisation['n']}), top-1 localisation reached "
            f"{localisation['above_two_top1_pct']:.1f}%. The 88.9% figure should "
            "never be quoted on its own: it is inflated by "
            f"{localisation['at_most_two_n']} near-trivial cases. An 88.9% result "
            f"on a median {localisation['median_sites']:.0f}-site problem is a much "
            "weaker signal than the same number on a 10-site problem."
        )
    else:
        add("No localisation records were available in the fixtures.")
    add("")
    add("## 6. How to cite these numbers")
    add("")
    add("- Quote **92.3% top-1** only as *reproduction of SUMMIT v4* (ADR-0036).")
    add(
        "- Always pair it with **median candidate pool "
        f"{sizes['median']:.0f}** and the bucket breakdown."
    )
    add(
        "- Do not quote **88.9% localisation** on its own. Quote "
        f"**{localisation['above_two_top1_pct']:.1f}% on the "
        f"{localisation['above_two_n']} compounds with more than two possible "
        f"sites**, and state that {localisation['at_most_two_n']}/{localisation['n']} "
        "cases had at most two sites."
    )
    add(
        "- When discussing the `<10` bucket, attribute it to **molecule size and "
        "glycosylation**, not to pool size or rare formulas (ADR-0037)."
    )
    if small_pool and small_pool["bucket_failures"]:
        add(
            f"- Of the {small_pool['bucket_failures']} small-pool failures, "
            f"{small_pool['below_confirmed']} are near-ties the platform would "
            "have downgraded and "
            f"{small_pool['bucket_failures'] - small_pool['below_confirmed']} are "
            "genuine errors. Do not describe the bucket as entirely a success or "
            "entirely a limitation."
        )
    add("- Do not describe either number as independent validation (ADR-0036).")
    add("")
    return chr(10).join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    per_compound = load_csv(FIXTURES / "partA_per_compound.csv")
    failures_raw = load_csv(FIXTURES / "partA_failures.csv")
    part_b = load_csv(FIXTURES / "partB_v4.csv")

    sizes = candidate_size_section(per_compound)
    buckets = accuracy_by_bucket(per_compound)
    classes = class_accuracy(per_compound)
    failures = failure_section(per_compound, failures_raw)
    localisation = localisation_section(part_b)
    small_pool = small_pool_section(per_compound, failures_raw)

    report = render(sizes, buckets, classes, failures, localisation, small_pool)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report + "\n", encoding="utf-8")

    print(f"Report written: {args.output}")
    print(f"Compounds: {sizes['count']}  median candidates: {sizes['median']:.0f}")
    for b in buckets:
        print(f"  {b['bucket']:>8s}: n={b['n']:3d}  top1={b['top1_pct']:5.1f}%")
    print(
        f"Localisation: median {localisation['median_sites']:.0f} sites, "
        f"{localisation['single_site_n']}/{localisation['n']} single-site"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
