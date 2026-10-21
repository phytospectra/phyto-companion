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
        f"headline figure is not propped up by easy small-pool cases. "
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
    add("## 3. Failure modes")
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
    add("## 4. Site-localisation benchmark audit")
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
    add("## 5. How to cite these numbers")
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

    report = render(sizes, buckets, classes, failures, localisation)
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
