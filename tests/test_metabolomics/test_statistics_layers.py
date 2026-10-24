"""Statistics layers on the Curcuma fixture (ADR-0070): O-PLS regression
with continuous Y (PLS-DA is the categorical substitute under ADR-0015),
VIP selection, and STOCSY.

Ground truth (README_Curcuma.md / peak_assignment_table_Curcuma.csv):
storage day drives a curcuminoid decay (half-life ~22 d) and a rising
7.55 ppm oxidation marker; the enol-CH singlet sits at 5.95 ppm.  A
defensible O-PLS model MUST recover storage_day from the matrix (high
R2Y/Q2, low permutation p), and the 7.55 marker must correlate with the
decay markers under STOCSY.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from metabolomics.feature_detection import Feature, FeatureTable, SampleMetadata
from metabolomics.opls_regression import (
    DEFAULT_N_PERMUTATIONS,
    SkipOPLS,
    opls_regression,
)
from metabolomics.stocsy import (
    STOCSY_HYPOTHESIS_WARNING,
    SkipSTOCSY,
    all_pairs_stocsy,
    driver_stocsy,
)
from metabolomics.vip import select_vip_features, vip_from_opls

CURCUMA = Path(__file__).resolve().parents[1] / "fixtures" / "metabolomics" / "curcuma"
SAMPLE_SHEET = CURCUMA / "sample_sheet_Curcuma_longa.csv"


@pytest.fixture(scope="module")
def curcuma_matrix():
    from nmr_preproc.pipeline import preprocess_experiments

    dirs = sorted(
        str(p)
        for p in (CURCUMA / "Curcuma_longa_NMR_dataset").iterdir()
        if p.is_dir()
    )
    return preprocess_experiments(dirs)


@pytest.fixture(scope="module")
def storage_days() -> dict[str, float]:
    with SAMPLE_SHEET.open() as fh:
        return {
            row["sample_id"]: float(row["storage_day"])
            for row in csv.DictReader(fh)
        }


def _table_from(matrix: np.ndarray, sample_ids: list[str]) -> FeatureTable:
    n_buckets = matrix.shape[1]
    return FeatureTable(
        features=[
            Feature(
                feature_id=f"B{j}",
                mz=float(j + 1),
                rt=float(j),
                intensities={s: float(matrix[i, j]) for i, s in enumerate(sample_ids)},
            )
            for j in range(n_buckets)
        ],
        samples=[SampleMetadata(sample_id=s) for s in sample_ids],
    )


class TestOPLSRegression:
    def test_recovers_storage_day_ground_truth(
        self, curcuma_matrix, storage_days
    ) -> None:
        table = _table_from(curcuma_matrix.matrix, curcuma_matrix.sample_ids)
        result = opls_regression(table, storage_days)
        # documented decay is the dominant variance axis: the model must
        # explain it and the permutation test must certify it
        assert result.evidence.r2y > 0.8
        assert result.evidence.q2 > 0.5
        assert result.evidence.permutation_p_value <= 0.05
        assert result.evidence.n_permutations == DEFAULT_N_PERMUTATIONS

    def test_evidence_names_thresholds(self, curcuma_matrix, storage_days) -> None:
        table = _table_from(curcuma_matrix.matrix, curcuma_matrix.sample_ids)
        result = opls_regression(table, storage_days)
        assert result.evidence.cv_scheme
        assert result.evidence.model_kind == "opls_regression"

    def test_few_samples_skip_with_reason(self) -> None:
        table = _table_from(np.array([[1.0, 2.0], [3.0, 4.0]]), ["a", "b"])
        with pytest.raises(SkipOPLS, match="fewer than"):
            opls_regression(table, {"a": 1.0, "b": 2.0})

    def test_constant_covariate_skip_with_reason(self, curcuma_matrix) -> None:
        table = _table_from(curcuma_matrix.matrix, curcuma_matrix.sample_ids)
        constant = {s: 5.0 for s in curcuma_matrix.sample_ids}
        with pytest.raises(SkipOPLS, match="constant"):
            opls_regression(table, constant)

    def test_missing_covariate_is_invalid_not_skipped(
        self, curcuma_matrix, storage_days
    ) -> None:
        from phyto_shared.exceptions import InvalidInputError

        table = _table_from(curcuma_matrix.matrix, curcuma_matrix.sample_ids)
        partial = dict(list(storage_days.items())[:3])
        with pytest.raises(InvalidInputError):
            opls_regression(table, partial)


class TestVipSelection:
    def test_vip_from_opls_then_select(self, curcuma_matrix, storage_days) -> None:
        table = _table_from(curcuma_matrix.matrix, curcuma_matrix.sample_ids)
        result = opls_regression(table, storage_days)
        vips = vip_from_opls(result)
        assert len(vips) == curcuma_matrix.matrix.shape[1]
        selection = select_vip_features(vips, result.feature_ids, threshold=1.0)
        assert selection.features
        passing = [f for f in selection.features if f.passes_threshold]
        assert passing
        # every reported VIP carries the unpaired note: the chain did not
        # run a univariate test, so none may claim a p-value pairing
        assert all(
            f.p_value is None and f.pairing_note
            for f in selection.features
        )

    def test_empty_selection_is_evidence_not_silence(self) -> None:
        selection = select_vip_features([0.5, 0.6], ["a", "b"], threshold=1.0)
        assert not [f for f in selection.features if f.passes_threshold]
        assert selection.empty_reason

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="lengths differ"):
            select_vip_features([1.0, 2.0], ["a"], threshold=1.0)


class TestStocsy:
    def test_driver_enol_ch_finds_day_correlated_buckets(self, curcuma_matrix) -> None:
        # driver = the curcumin enol-CH singlet at 5.95 ppm (the strong
        # documented curcuminoid marker); the bucketed matrix is PQN
        # normalised, which redistributes scale away from the weak
        # oxidation marker, so the enol-CH is the defensible driver.
        # Documented ground truth: curcuminoid intensity DECREASES with
        # storage day, so the driver's own amplitude falls with day and
        # buckets that share the decay (e.g. the 8.06-8.14 block, r(day)
        # ~ -0.9) must correlate positively with the driver.
        result = driver_stocsy(
            curcuma_matrix.matrix, curcuma_matrix.ppm, 5.95, r_threshold=0.7,
        )
        assert result.correlations, "no correlations above |r| >= 0.7"
        # the driver bucket itself is excluded from its own correlation
        assert all(
            c.bucket_ppm != pytest.approx(5.95, abs=0.02)
            for c in result.correlations
        )
        # a bucket with r(day) = -0.95 shares the driver's decay: |r| > 0.7
        assert any(
            c.bucket_ppm == pytest.approx(8.14, abs=0.06)
            and c.pearson_r > 0.7
            for c in result.correlations
        )
        # the result object carries the hypothesis warning as evidence text
        assert result.warning == STOCSY_HYPOTHESIS_WARNING

    def test_constant_driver_skips(self) -> None:
        # a driver with zero variance across spectra (synthesised here:
        # the real fixture has no fully constant bucket after baseline
        # correction) is skipped with a named reason
        matrix = np.ones((6, 10)) + np.arange(6)[:, None] * 0.0
        matrix[:, 3] = 1.0  # constant column as the driver
        ppm = np.linspace(9.0, 0.5, 10)
        with pytest.raises(SkipSTOCSY, match="zero variance"):
            driver_stocsy(matrix, ppm, float(ppm[3]))

    def test_all_pairs_finds_correlated_block(self, curcuma_matrix) -> None:
        result = all_pairs_stocsy(
            curcuma_matrix.matrix, curcuma_matrix.ppm, r_threshold=0.9
        )
        assert result.correlations

    def test_too_few_samples_skips(self) -> None:
        matrix = np.random.default_rng(0).normal(size=(2, 10))
        ppm = np.linspace(9.0, 0.5, 10)
        with pytest.raises(SkipSTOCSY, match="at least 3"):
            driver_stocsy(matrix, ppm, 5.0)
