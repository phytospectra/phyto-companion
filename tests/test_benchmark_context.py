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
            "Failure modes",
            "Site-localisation benchmark audit",
        ]:
            assert heading in report

    def test_does_not_present_889_as_standalone_claim(self, report):
        cite = report[report.index("## 5.") :]
        assert "Do not quote **88.9% localisation** on its own" in cite
