"""Chromatography domain-model and math tests (ADR-0063, ADR-0064).

Method sanity, anchor monotonicity, the RT<->RI piecewise map and the
spectral-cosine helpers: the arithmetic the chromatography layers stand
on must be pinned down before any reranker uses it.
"""

from __future__ import annotations

import pytest

from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    ReferenceStandard,
    RetentionPrediction,
    build_anchor_calibration,
    ms2_cosine,
    ri_to_rt,
    rt_to_ri,
    uv_cosine,
)


def _method(**overrides) -> LCMethod:
    base = dict(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    )
    base.update(overrides)
    return LCMethod(**base)


def _anchors() -> list[AnchorStandard]:
    # Consecutive homologues (the RI convention: 100 RI units per CH2).
    return [
        AnchorStandard(name="C10", chain_length=10, observed_rt_min=5.0),
        AnchorStandard(name="C11", chain_length=11, observed_rt_min=7.5),
        AnchorStandard(name="C12", chain_length=12, observed_rt_min=11.0),
    ]


# --- LCMethod --------------------------------------------------------------


def test_lc_method_accepts_a_sane_gradient() -> None:
    method = _method()
    assert method.gradient[-1] == (30.0, 95.0)


def test_lc_method_rejects_non_increasing_gradient_times() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        _method(gradient=[(0.0, 5.0), (10.0, 50.0), (10.0, 60.0)])


def test_lc_method_rejects_percent_b_out_of_range() -> None:
    with pytest.raises(ValueError, match="percent_B"):
        _method(gradient=[(0.0, 5.0), (30.0, 101.0)])


def test_lc_method_rejects_gradient_not_starting_at_zero() -> None:
    with pytest.raises(ValueError, match="time 0.0"):
        _method(gradient=[(1.0, 5.0), (30.0, 95.0)])


# --- RetentionPrediction ---------------------------------------------------


def test_retention_prediction_interval_must_bracket_point() -> None:
    with pytest.raises(ValueError, match="bracket"):
        RetentionPrediction(
            predicted_rt_min=10.0,
            prediction_interval=(10.5, 12.0),
            model_id="m", model_version="1",
        )


def test_retention_prediction_accepts_valid_interval() -> None:
    prediction = RetentionPrediction(
        predicted_rt_min=10.0,
        prediction_interval=(7.5, 12.5),
        model_id="m", model_version="1",
    )
    assert prediction.prediction_interval == (7.5, 12.5)


# --- Anchor calibration ------------------------------------------------------


def test_anchor_calibration_rejects_non_monotonic_rts() -> None:
    bad = [
        AnchorStandard(name="C10", chain_length=10, observed_rt_min=8.0),
        AnchorStandard(name="C12", chain_length=12, observed_rt_min=6.0),
    ]
    with pytest.raises(ValueError, match="strictly increase"):
        build_anchor_calibration("m1", bad)


def test_anchor_calibration_roundtrips_through_ri() -> None:
    anchors = _anchors()
    ri = rt_to_ri(8.0, anchors)
    # Between C11 (7.5 min -> 1100) and C12 (11.0 min -> 1200).
    assert 1100.0 < ri < 1200.0
    rt = ri_to_rt(ri, anchors)
    assert rt == pytest.approx(8.0, abs=1e-6)


def test_rt_to_ri_on_segment_is_linear() -> None:
    anchors = _anchors()
    # Exactly halfway between C11 (1100) and C12 (1200) in time -> 1150.
    halfway = (7.5 + 11.0) / 2
    assert rt_to_ri(halfway, anchors) == pytest.approx(1150.0)


def test_rt_to_ri_requires_two_anchors() -> None:
    with pytest.raises(Exception):
        rt_to_ri(5.0, _anchors()[:1])


def test_rt_to_ri_rejects_duplicate_chain_lengths() -> None:
    dup = [
        AnchorStandard(name="a", chain_length=10, observed_rt_min=5.0),
        AnchorStandard(name="b", chain_length=10, observed_rt_min=7.0),
    ]
    with pytest.raises(Exception, match="unique"):
        rt_to_ri(6.0, dup)


# --- Spectral cosines --------------------------------------------------------


def test_uv_cosine_identical_spectra_is_one() -> None:
    spectrum = [(250.0, 1.0), (260.0, 2.0), (350.0, 3.0)]
    assert uv_cosine(spectrum, spectrum) == pytest.approx(1.0, abs=1e-6)


def test_uv_cosine_handles_uneven_sampling() -> None:
    reference = [(250.0, 1.0), (260.0, 2.0), (350.0, 3.0)]
    observed = [(251.0, 1.0), (259.0, 2.1), (350.0, 3.0)]
    cosine = uv_cosine(reference, observed)
    assert 0.9 < cosine <= 1.0


def test_ms2_cosine_identical_is_one_and_empty_scores_zero() -> None:
    peaks = [(100.0, 10.0), (150.0, 5.0)]
    assert ms2_cosine(peaks, peaks)[0] == pytest.approx(1.0)
    assert ms2_cosine(peaks, []) [0] == 0.0


def test_reference_standard_defaults_are_empty() -> None:
    standard = ReferenceStandard(name="caffeine", smiles="Cn1c(=O)c2c(ncn2C)n(C)c1=O", rt_min=3.1)
    assert standard.uv_spectrum == []
    assert standard.ms2 == []