"""Tests for ``scripts/analyze_benchmark_context.py`` and its report.

These lock in the facts that ADR-0036 requires external communication to carry:
the candidate-pool distribution, the per-bucket accuracy spread, and the
near-trivial share of the site-localisation benchmark. If a future fixture swap
changes any of them the report must be regenerated and the ADR revisited.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "analyze_benchmark_context.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("analyze_benchmark_context", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ctx():
    return _load_module()


@pytest.fixture(scope="module")
def per_compound(ctx):
    return ctx.load_csv(ctx.FIXTURES / "partA_per_compound.csv")


@pytest.fixture(scope="module")
def failures(ctx):
    return ctx.load_csv(ctx.FIXTURES / "partA_failures.csv")


@pytest.fixture(scope="module")
def part_b(ctx):
    return ctx.load_csv(ctx.FIXTURES / "partB_v4.csv")


# ---------------------------------------------------------------------------
# Part A: candidate-pool context
# ---------------------------------------------------------------------------


class TestCandidatePoolContext:
    def test_candidate_distribution(self, ctx, per_compound):
        sizes = ctx.candidate_size_section(per_compound)
        assert sizes["count"] == 207
        assert sizes["median"] == 48
        assert sizes["q1"] == 16
        assert sizes["q3"] == 122
        assert sizes["min"] == 5
        assert sizes["max"] == 642
        # Right-skewed: the mean sits well above the median.
        assert sizes["mean"] > 1.5 * sizes["median"]

    def test_bucket_counts_and_accuracy(self, ctx, per_compound):
        buckets = {b["bucket"]: b for b in ctx.accuracy_by_bucket(per_compound)}
        assert set(buckets) == {"<10", "10-50", "50-200", ">200"}
        assert buckets["<10"]["n"] == 29
        assert buckets["10-50"]["n"] == 76
        assert buckets["50-200"]["n"] == 84
        assert buckets[">200"]["n"] == 18
        assert sum(b["n"] for b in buckets.values()) == 207

        assert buckets["<10"]["top1_pct"] == pytest.approx(79.3, abs=0.1)
        assert buckets["10-50"]["top1_pct"] == pytest.approx(93.4, abs=0.1)
        assert buckets["50-200"]["top1_pct"] == pytest.approx(95.2, abs=0.1)
        assert buckets[">200"]["top1_pct"] == pytest.approx(94.4, abs=0.1)

    def test_smallest_pool_is_the_weakest(self, ctx, per_compound):
        """The headline is not propped up by easy small pools (ADR-0036)."""
        buckets = ctx.accuracy_by_bucket(per_compound)
        weakest = min(buckets, key=lambda b: b["top1_pct"])
        assert weakest["bucket"] == "<10"
        assert weakest["top1_pct"] < 92.3

    def test_accuracy_is_not_uniform(self, ctx, per_compound):
        buckets = ctx.accuracy_by_bucket(per_compound)
        spread = max(b["top1_pct"] for b in buckets) - min(
            b["top1_pct"] for b in buckets
        )
        assert spread > 10.0, "a spread this large means the single number is misleading"


# ---------------------------------------------------------------------------
# Part A: failure modes
# ---------------------------------------------------------------------------


class TestFailureModes:
    def test_failure_count_and_ranks_are_near_misses(self, ctx, per_compound, failures):
        section = ctx.failure_section(per_compound, failures)
        assert section["count"] == 16
        assert section["total_compounds"] == 207
        assert all(float(rank) <= 3.0 for rank, _ in section["by_rank"])

    def test_failures_are_constitutional_isomers(self, ctx, per_compound, failures):
        section = ctx.failure_section(per_compound, failures)
        pytest.importorskip("rdkit")
        assert section["comparable"] == 16
        assert section["same_formula"] == 16
        assert section["pure_stereoisomers"] == 0
        assert section["contains_sugar"] == 7
        assert section["mean_tanimoto"] > 0.7

    def test_top5_classes_by_failure_rate(self, ctx, per_compound, failures):
        section = ctx.failure_section(per_compound, failures)
        top5 = [r["cls"] for r in section["by_class_rates"][:5]]
        assert "flavonoid" in top5
        assert "steroid glycoside" in top5
        # Rates are consistent with raw counts for each listed class.
        for row in section["by_class_rates"][:5]:
            assert row["rate_pct"] == pytest.approx(
                100.0 * row["failures"] / row["compounds"], abs=0.1
            )


# ---------------------------------------------------------------------------
# Small-pool failure analysis (ADR-0037)
# ---------------------------------------------------------------------------


class TestSmallPoolAnalysis:
    def test_identifies_the_six_small_pool_failures(self, ctx, per_compound, failures):
        sp = ctx.small_pool_section(per_compound, failures)
        assert sp["bucket_n"] == 29
        assert sp["bucket_failures"] == 6
        assert sp["bucket_rate_pct"] == pytest.approx(20.7, abs=0.1)
        assert len(sp["cases"]) == 6

    def test_margins_and_verdict_split(self, ctx, per_compound, failures):
        sp = ctx.small_pool_section(per_compound, failures)
        margins = sorted(c["margin"] for c in sp["cases"])
        assert margins == pytest.approx(
            [0.013, 0.055, 0.127, 0.146, 0.168, 0.290], abs=0.001
        )
        assert sp["below_confirmed"] == 2
        assert sp["below_tentative"] == 1

    def test_every_failure_is_a_constitutional_isomer(self, ctx, per_compound, failures):
        pytest.importorskip("rdkit")
        sp = ctx.small_pool_section(per_compound, failures)
        for case in sp["cases"]:
            assert case["same_formula"] is True
            assert case["same_ring_count"] is True
        # Five of six also pass the close-isomer Tanimoto threshold.
        assert sp["isomers"] == 5

    def test_rare_formula_hypothesis_is_refuted(self, ctx, per_compound, failures):
        sp = ctx.small_pool_section(per_compound, failures)
        # Formula multiplicity is the same in both groups.
        assert sp["mean_formula_freq_small"] == pytest.approx(
            sp["mean_formula_freq_large"], abs=0.15
        )

    def test_molecule_size_is_the_driver(self, ctx, per_compound, failures):
        sp = ctx.small_pool_section(per_compound, failures)
        strata = {s["stratum"]: s for s in sp["strata"]}
        large = next(s for k, s in strata.items() if k.startswith("large"))
        small = next(s for k, s in strata.items() if k.startswith("small"))
        large_rates = {c["pool"]: c["rate_pct"] for c in large["cells"]}
        small_rates = {c["pool"]: c["rate_pct"] for c in small["cells"]}
        # Large molecules fail at every pool size; small ones barely at all.
        assert min(large_rates.values()) > 10.0
        assert max(small_rates.values()) <= 12.5
        # The bucket is enriched in large molecules and glycosides.
        assert sp["median_heavy_small"] > sp["median_heavy_all"]
        assert sp["glycoside_share_small"] > sp["glycoside_share_all"]

    def test_heavy_atom_count_parses_formulas(self, ctx):
        assert ctx.heavy_atom_count("C6H14O12P2") == 20
        assert ctx.heavy_atom_count("C71H116O35") == 106
        assert ctx.heavy_atom_count("H2O") == 1


# ---------------------------------------------------------------------------
# Part B: localisation audit -- the <=2-site flag
# ---------------------------------------------------------------------------


class TestLocalisationAudit:
    def test_median_site_count(self, ctx, part_b):
        section = ctx.localisation_section(part_b)
        assert section["n"] == 36
        assert section["median_sites"] == 3
        assert section["min_sites"] == 1
        assert section["max_sites"] == 29

    def test_near_trivial_share_is_flagged(self, ctx, part_b):
        section = ctx.localisation_section(part_b)
        assert section["at_most_two_n"] == 15
        assert section["at_most_two_share_pct"] == pytest.approx(41.7, abs=0.1)
        assert section["single_site_n"] == 8

    def test_hard_subset_accuracy_is_lower_than_headline(self, ctx, part_b):
        section = ctx.localisation_section(part_b)
        assert section["above_two_n"] == 21
        assert section["above_two_top1_pct"] == pytest.approx(81.0, abs=0.1)
        assert section["above_two_top1_pct"] < section["overall_top1_pct"]
        assert section["overall_top1_pct"] == pytest.approx(88.9, abs=0.1)


# ---------------------------------------------------------------------------
# Report content: the caveat must be present and prominent
# ---------------------------------------------------------------------------


class TestReportContent:
    @pytest.fixture(scope="class")
    @classmethod
    def report(cls, tmp_path_factory):
        module = _load_module()
        out = tmp_path_factory.mktemp("inventory") / "benchmark_context_report.md"
        assert module.main(["--output", str(out)]) == 0
        return out.read_text()

    def test_has_quotable_summary_first(self, report):
        head = report.index("## Summary")
        assert head < report.index("## 0.")
        summary = report[head : report.index("## 0.")]
        assert "207" in summary
        assert "92.3%" in summary
        assert "reproduction" in summary
        assert "median 48" in summary

    def test_states_reproduction_not_validation(self, report):
        assert "REPRODUCTION" in report or "reproduction" in report
        assert "not an independent validation" in report

    def test_warns_on_weak_localisation_baseline(self, report):
        assert "WARNING" in report
        assert "15 of 36" in report
        assert "42%" in report
        assert "81.0%" in report

    def test_reports_all_required_sections(self, report):
        for heading in [
            "Summary",
            "Candidate-set size distribution",
            "Top-1 accuracy by candidate-set size",
            "Why small pools are harder",
            "Failure modes",
            "Site-localisation benchmark audit",
        ]:
            assert heading in report

    def test_small_pool_section_states_mixed_conclusion(self, report):
        assert "neither explanation as posed is the dominant one" in report
        assert "rare formula" in report and "does not hold" in report
        assert "molecule size" in report

    def test_summary_carries_small_pool_finding(self, report):
        summary = report[report.index("## Summary") : report.index("## 0.")]
        assert "2 were near-ties" in summary
        assert "4 were genuine retrieval errors" in summary

    def test_does_not_present_889_as_standalone_claim(self, report):
        cite = report[report.index("## 5.") :]
        assert "Do not quote **88.9% localisation** on its own" in cite
