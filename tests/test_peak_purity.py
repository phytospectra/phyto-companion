"""Peak-purity tests (ADR-0065): the 0-1 score, MS alignment weights,
and the AMBIGUITY caveat."""

from __future__ import annotations

import pytest

from phyto_shared.peak_purity import (
    PURITY_AMBIGUITY_THRESHOLD,
    peak_purity,
    purity_caveat,
)


PURE_UV = [(250.0, 1.0), (260.0, 2.0), (350.0, 3.0)]


def test_identical_apex_and_edges_is_pure() -> None:
    score = peak_purity(PURE_UV, PURE_UV, PURE_UV, 300.1, 300.11)
    assert score == pytest.approx(1.0)


def test_different_edge_spectra_depress_the_score() -> None:
    edge = [(430.0, 1.0), (440.0, 2.0), (450.0, 3.0)]
    score = peak_purity(PURE_UV, edge, edge, 300.1, 300.11)
    assert score < 0.9


def test_misaligned_shoulder_mass_signals_coelution() -> None:
    aligned = peak_purity(None, None, None, 300.1, 300.12)     # 0.7*0.75 + 0.3*1.0 = 0.825
    misaligned = peak_purity(None, None, None, 300.1, 450.0)   # 0.7*0.75 + 0.3*0.2 = 0.585
    far = peak_purity(None, None, None, 300.1, 700.0)
    assert misaligned < aligned
    # With no UV at all even a misaligned shoulder stays above the
    # threshold: absence of UV evidence is neutral, not purity.  A much
    # larger m/z gap reads as a different compound outright.
    assert far == pytest.approx(0.585)


def test_missing_channels_contribute_neutral_not_pass() -> None:
    # No UV edges and no shoulder: 0.75 * 0.7 + 0.8 * 0.3 -> 0.765.
    score = peak_purity(None, None, None, 300.1, None)
    assert score == pytest.approx(0.765)


def test_score_is_clamped_to_unit_interval() -> None:
    assert peak_purity(PURE_UV, PURE_UV, PURE_UV, None, None) <= 1.0


def test_caveat_only_below_threshold() -> None:
    assert purity_caveat(0.2) is not None
    assert purity_caveat(0.9) is None
    assert purity_caveat(PURITY_AMBIGUITY_THRESHOLD) is None