"""Statistics tests, including the OPLS-DA non-existence regression."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from phyto_shared.exceptions import InvalidInputError
import metabolomics.statistics as stats_module
from metabolomics.feature_detection import DetectParams, MockDetector, FeatureTable
from metabolomics.statistics import (
    OPLS_DA_NOT_IMPLEMENTED,
    biomarker_candidates,
    fdr_correction,
    pca,
    pls_da,
    random_forest_classifier,
    univariate_test,
    vip_scores,
)


def _table() -> FeatureTable:
    return MockDetector().detect(
        [Path(f"/d/{n}.mzML") for n in
         ("control_1", "control_2", "control_3", "treated_1", "treated_2", "treated_3")],
        DetectParams(),
    )


def _labels() -> dict[str, str]:
    return {
        "control_1": "control", "control_2": "control", "control_3": "control",
        "treated_1": "treated", "treated_2": "treated", "treated_3": "treated",
    }


class TestOPLSRegression:
    def test_opls_da_not_in_public_api(self):
        # ADR-0015: patented algorithm, deliberately absent. Only the
        # explanatory CONSTANT may mention OPLS; no callable may.
        assert not hasattr(stats_module, "opls_da")
        assert not hasattr(stats_module, "OPLSDA")
        public = {name for name in dir(stats_module) if not name.startswith("_")}
        assert {
            name for name in public if "opls" in name.lower()
        } == {"OPLS_DA_NOT_IMPLEMENTED"}
        assert "patented" in OPLS_DA_NOT_IMPLEMENTED


class TestPCA:
    def test_shapes_and_variance(self):
        result = pca(_table(), n_components=3)
        assert len(result.scores) == 6
        assert len(result.scores[0]) == 3
        assert len(result.loadings) == 7
        assert 0.0 < result.cumulative_explained_variance <= 1.0 + 1e-9
        assert result.explained_variance_ratio == sorted(
            result.explained_variance_ratio, reverse=True
        )

    def test_component_cap(self):
        result = pca(_table(), n_components=50)
        assert result.n_components <= 6


class TestPLSDa:
    def test_fit_and_permutation(self):
        result = pls_da(_table(), _labels(), n_components=2, n_permutations=10)
        assert -1.0 <= result.q2 <= 1.0
        assert 0.0 <= result.r2y <= 1.0
        assert 0.0 < result.permutation_p_value <= 1.0
        assert len(result.vip) == 7

    def test_vip_scores_non_negative_and_informative(self):
        from sklearn.cross_decomposition import PLSRegression
        from metabolomics.feature_detection import table_to_matrix

        _, _, matrix = table_to_matrix(_table())
        y = np.array([0, 0, 0, 1, 1, 1], dtype=float)
        pls = PLSRegression(n_components=2).fit(matrix, y)
        vips = vip_scores(pls)
        assert len(vips) == 7
        assert all(v >= 0 for v in vips), "VIP is a non-negative magnitude"
        # the informative feature (FT5) must stand out; the constant
        # internal standard (FT1) legitimately scores exactly zero
        assert vips[4] > 1.0
        assert vips[0] == pytest.approx(0.0)

    def test_three_classes_rejected(self):
        labels = dict(_labels())
        labels["control_1"] = "third"
        with pytest.raises(InvalidInputError):
            pls_da(_table(), labels)

    def test_missing_labels_rejected(self):
        with pytest.raises(InvalidInputError):
            pls_da(_table(), {"control_1": "control"})


class TestRandomForest:
    def test_metrics(self):
        result = random_forest_classifier(_table(), _labels(), n_estimators=50)
        assert 0.0 <= result.oob_error <= 1.0
        assert 0.0 <= result.roc_auc <= 1.0
        assert len(result.feature_importances) == 7
        assert sum(result.feature_importances) == pytest.approx(1.0, abs=1e-6)


class TestUnivariate:
    @pytest.mark.parametrize("method", ["welch", "student", "mann_whitney"])
    def test_methods(self, method):
        result = univariate_test(_table(), _labels(), method=method)
        assert result.method == method
        assert len(result.feature_ids) == 7
        assert len(result.p_value) == 7

    def test_elevated_feature_is_significant(self):
        result = univariate_test(_table(), _labels())
        index = result.feature_ids.index("FT5")  # 2x in treated
        assert result.p_value[index] < 0.05
        assert result.log2_fold_change[index] < 0  # treated is group_a
        assert abs(result.cohens_d[index]) > 0.8

    def test_fold_change_definition(self):
        result = univariate_test(_table(), _labels())
        index = result.feature_ids.index("FT5")
        # group_a = control (alphabetical), group_b = treated
        assert result.fold_change[index] == pytest.approx(0.5, rel=0.05)


class TestFDR:
    def test_known_bh_values(self):
        adjusted = fdr_correction([0.01, 0.04, 0.03, 0.20])
        assert adjusted == pytest.approx([0.04, 0.0533333, 0.0533333, 0.20], abs=1e-5)

    def test_by_is_more_conservative(self):
        ps = [0.01, 0.02, 0.03, 0.04, 0.05]
        bh = fdr_correction(ps, "bh")
        by = fdr_correction(ps, "by")
        assert all(b >= a for a, b in zip(bh, by))

    def test_empty(self):
        assert len(fdr_correction([])) == 0


class TestBiomarkers:
    def test_intersection_and_union_reported_separately(self):
        table = _table()
        labels = _labels()
        univariate = univariate_test(table, labels)
        pls = pls_da(table, labels, n_components=2, n_permutations=5)
        result = biomarker_candidates(univariate, pls)
        assert set(result) == {"intersection", "union"}
        names = {rank.feature_id for rank in result["intersection"]}
        assert names <= {rank.feature_id for rank in result["union"]}
        assert all(r.criteria == "intersection" for r in result["intersection"])
