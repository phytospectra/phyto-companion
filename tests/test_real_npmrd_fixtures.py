"""Tests validating real NP-MRD & SUMMIT v4 benchmark fixtures.

These tests prove:
1. The ingestion service parses real literature HSQC peak lists (NP-MRD andrographolide).
2. The elucidation matcher computes real Hungarian matching on real assignments.
3. The platform reproduces and cross-checks the published SUMMIT v4 benchmark metrics.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from phyto_shared.models import Peak2D, Spectrum
from ingestion.nmr_parser import parse_hsqc_csv, parse_peak_csv
from elucidation.hungarian_matcher import match_hsqc

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "real_npmrd"
ANDROGRAPHOLIDE_CSV = FIXTURES_DIR / "andrographolide_hsqc_literature.csv"
BENCHMARK_DIR = FIXTURES_DIR / "benchmark_v4"


# ===========================================================================
# 1. Ingestion of real literature HSQC peaks
# ===========================================================================


class TestAndrographolideIngestion:
    def test_fixture_file_integrity(self):
        assert ANDROGRAPHOLIDE_CSV.exists()
        data = ANDROGRAPHOLIDE_CSV.read_bytes()
        expected_sha = "32eacebda81a2f43f03eabaa9664c1efc0a88bed7c6ece9a08b4c8d49a8cbe13"
        assert hashlib.sha256(data).hexdigest() == expected_sha

    def test_parse_hsqc_csv_returns_valid_spectrum(self):
        spectrum = parse_hsqc_csv(ANDROGRAPHOLIDE_CSV, spectrum_id="andrographolide_lit")
        assert isinstance(spectrum, Spectrum)
        assert spectrum.kind == "HSQC"
        assert len(spectrum.data) == 15
        assert all(isinstance(p, Peak2D) for p in spectrum.data)
        assert all(p.experiment_type == "HSQC" for p in spectrum.data)

        # Check shift ranges
        h_shifts = [p.h_ppm for p in spectrum.data]
        c_shifts = [p.c_ppm for p in spectrum.data]
        assert min(h_shifts) == pytest.approx(0.75)
        assert max(h_shifts) == pytest.approx(6.85)
        assert min(c_shifts) == pytest.approx(15.53)
        assert max(c_shifts) == pytest.approx(149.29)

        # Check assigned positions
        positions = spectrum.metadata.get("assigned_positions")
        assert positions is not None
        pos_list = positions.split(",")
        assert len(pos_list) == 15
        assert "C1" in pos_list and "C12" in pos_list and "C20" in pos_list

    def test_auto_detect_peak_csv(self):
        spectrum = parse_peak_csv(ANDROGRAPHOLIDE_CSV)
        assert spectrum.kind == "HSQC"
        assert len(spectrum.data) == 15

    def test_hungarian_matching_on_real_peaks(self):
        spectrum = parse_hsqc_csv(ANDROGRAPHOLIDE_CSV)
        observed = [(p.h_ppm, p.c_ppm) for p in spectrum.data]

        # Match against itself: must be perfect 0.0 distance and 1.0 coverage
        self_match = match_hsqc(observed, observed)
        assert self_match.score == pytest.approx(0.0)
        assert self_match.coverage == pytest.approx(1.0)
        assert self_match.ranking_convention == "lower_is_better"

        # Match against a slightly perturbed spectrum (simulating prediction error)
        perturbed = [(h + 0.01, c + 0.1) for h, c in observed]
        close_match = match_hsqc(perturbed, observed)
        assert 0.0 < close_match.score < 1.0
        assert close_match.coverage == pytest.approx(1.0)


# ===========================================================================
# 2. Reproduction and verification of SUMMIT v4 benchmark metrics
# ===========================================================================


class TestSummitV4BenchmarkReproduction:
    def test_part_a_overall_metrics_match_summary(self):
        per_compound_path = BENCHMARK_DIR / "partA_per_compound.csv"
        assert per_compound_path.exists()

        with per_compound_path.open() as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 207, "SUMMIT v4 benchmark set consists of 207 natural products"

        col = "GNN + edited HSQC"
        ranks = [float(r[col]) for r in rows]

        top1_pct = sum(1 for r in ranks if r == 1.0) / len(ranks) * 100.0
        top5_pct = sum(1 for r in ranks if r <= 5.0) / len(ranks) * 100.0
        mrr = sum(1.0 / r for r in ranks) / len(ranks)

        # Verify against summary_v4.txt reported numbers
        assert round(top1_pct, 1) == 92.3
        assert round(top5_pct, 1) == 100.0
        assert round(mrr, 3) == 0.957

    def test_part_a_methods_comparison(self):
        methods_path = BENCHMARK_DIR / "partA_methods.csv"
        with methods_path.open() as f:
            rows = list(csv.DictReader(f))

        methods_by_name = {r["method"]: r for r in rows}
        assert "GNN + edited HSQC" in methods_by_name
        assert "HOSE + 13C only" in methods_by_name

        main_method = methods_by_name["GNN + edited HSQC"]
        assert float(main_method["top1_%"]) == 92.3
        assert float(main_method["top5_%"]) == 100.0
        assert float(main_method["MRR"]) == 0.957

        # Confirm GNN outperforms classical HOSE codes significantly
        hose_c13 = methods_by_name["HOSE + 13C only"]
        assert float(main_method["top1_%"]) > float(hose_c13["top1_%"])

    def test_part_a_class_breakdown_metrics(self):
        class_path = BENCHMARK_DIR / "partA_by_class.csv"
        with class_path.open() as f:
            rows = list(csv.DictReader(f))

        classes = {r["cls"]: r for r in rows}
        assert len(classes) == 20

        # High-performance classes
        assert float(classes["alkaloid/N-containing"]["top1_pct"]) == 100.0
        assert int(classes["alkaloid/N-containing"]["n"]) == 25

        # Challenging classes (flavonoids have high stereochemical/positional isomers)
        assert float(classes["flavonoid"]["top1_pct"]) == 57.1
        assert int(classes["flavonoid"]["n"]) == 7

    def test_part_b_site_localization_beats_chance(self):
        part_b_path = BENCHMARK_DIR / "partB_localization.csv"
        with part_b_path.open() as f:
            rows = list(csv.DictReader(f))

        subsets = {r["subset"]: r for r in rows}
        assert "all" in subsets
        assert "≥2 sites" in subsets

        all_subset = subsets["all"]
        assert int(all_subset["n"]) == 36
        obs_pct = float(all_subset["observed_pct"])
        chance_pct = float(all_subset["chance_pct"])

        assert obs_pct == pytest.approx(88.9)
        assert chance_pct == pytest.approx(44.3)
        assert obs_pct > chance_pct

        # The headline comparison is inflated by trivial cases: 8 of 36 have a
        # single possible site and 7 have two (ADR-0036). Guard those facts so a
        # future fixture swap cannot silently restore the bare 88.9% claim.
        with (BENCHMARK_DIR / "partB_v4.csv").open() as f:
            site_rows = [
                r for r in csv.DictReader(f) if r.get("n_sites") and r.get("true_site_rank")
            ]
        assert len(site_rows) == 36
        at_most_two = [r for r in site_rows if float(r["n_sites"]) <= 2]
        assert len(at_most_two) == 15, "42% of the localisation set is near-trivial"
        single = [r for r in at_most_two if float(r["n_sites"]) == 1]
        assert len(single) == 8

    def test_part_c_mixture_evaluation(self):
        part_c_path = BENCHMARK_DIR / "partC_v4.csv"
        with part_c_path.open() as f:
            rows = list(csv.DictReader(f))

        assert len(rows) > 0
        first = rows[0]
        assert "mixture" in first and "rank_mixture" in first and "rank_pure" in first
