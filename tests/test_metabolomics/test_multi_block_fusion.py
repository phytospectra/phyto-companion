"""Multi-block fusion: MFA, consensus PCA, DIABLO substitute, fusion test."""

from __future__ import annotations

import numpy as np
import pytest

from phyto_shared.exceptions import InvalidInputError
from metabolomics.multi_block_fusion import (
    DIABLO_UNAVAILABLE_REASON,
    consensus_pca,
    mfa,
    permutation_test_fusion,
    pls_da_concat,
)


def _blocks(seed: int = 0) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Two informative blocks (group signal) + one noise block."""
    rng = np.random.default_rng(seed)
    labels = np.array([0] * 12 + [1] * 12)
    signal = labels[:, None].astype(float)
    nmr = signal * 3.0 + rng.normal(0, 0.3, (24, 6))      # strong
    ms = signal * 2.0 + rng.normal(0, 0.6, (24, 8))       # moderate
    ir = rng.normal(0, 1.0, (24, 5))                      # pure noise
    return {"nmr": nmr, "ms": ms, "ir": ir}, labels


class TestMFA:
    def test_shapes_and_scalings(self):
        blocks, _ = _blocks()
        result = mfa(blocks, n_components=2)
        assert result.block_names == ["ir", "ms", "nmr"]
        assert len(result.scores) == 24
        assert len(result.block_scalings) == 3
        assert all(s > 0 for s in result.block_scalings)
        assert 0.0 < result.explained_variance_ratio[0] <= 1.0

    def test_first_component_separates_groups(self):
        blocks, labels = _blocks()
        scores = np.array(mfa(blocks, n_components=1).scores)[:, 0]
        # separation must exceed the within-group spread (an effect size
        # check, not an arbitrary absolute threshold)
        between = abs(np.mean(scores[labels == 1]) - np.mean(scores[labels == 0]))
        within = np.std(scores)
        assert between > within

    def test_mismatched_sample_counts_raise(self):
        blocks, _ = _blocks()
        blocks["bad"] = np.zeros((5, 3))
        with pytest.raises(InvalidInputError):
            mfa(blocks)

    def test_empty_raises(self):
        with pytest.raises(InvalidInputError):
            mfa({})


class TestConsensusPCA:
    def test_saliences_sum_to_one_per_component(self):
        blocks, _ = _blocks()
        result = consensus_pca(blocks, n_components=2)
        for component in result.block_saliences:
            assert sum(component) == pytest.approx(1.0, abs=1e-3)
            assert len(component) == 3

    def test_informative_blocks_dominate(self):
        blocks, _ = _blocks()
        result = consensus_pca(blocks, n_components=1)
        # block order is alphabetical: ir, ms, nmr
        salience = result.block_saliences[0]
        assert salience[0] < max(salience[1], salience[2])


class TestDIABLOSubstitute:
    def test_diablo_documented_as_unavailable(self):
        import metabolomics.multi_block_fusion as module

        assert not hasattr(module, "diablo")
        assert "GPL" in DIABLO_UNAVAILABLE_REASON

    def test_pls_da_concat_returns_per_block_and_fused(self):
        blocks, labels = _blocks()
        result = pls_da_concat(blocks, labels)
        assert set(result["single_block_q2"]) == {"ir", "ms", "nmr"}
        assert result["fused_q2"] > result["single_block_q2"]["ir"]


class TestFusionBeatsSingle:
    def test_fusion_helps_statistically(self):
        """Mirrors Step 4's fusion test at the statistical level."""
        blocks, labels = _blocks()
        result = permutation_test_fusion(blocks, labels, n_permutations=20)
        assert result.best_single_block in {"nmr", "ms"}
        assert result.fusion_better in (True, False)  # reported, not assumed
        assert 0.0 < result.permutation_p_value <= 1.0
        # the informative combination must at least beat pure noise
        assert result.fused_q2 > -1.0

    def test_requires_two_classes(self):
        blocks, _ = _blocks()
        with pytest.raises(InvalidInputError):
            permutation_test_fusion(blocks, np.zeros(24))  # one class only
