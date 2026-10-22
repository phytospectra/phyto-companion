"""Retention service tests (ADR-0063, ADR-0064): method-dependent RT
prediction, the wide-interval honesty rule, and RI conversion endpoints."""

from __future__ import annotations

import pytest

from phyto_shared.chromatography import AnchorStandard, LCMethod
from phyto_shared.exceptions import InvalidInputError

from retention.main import (
    MockRetentionClient,
    predict_rt,
)


def _method(**kw) -> LCMethod:
    base = dict(
        column_type="C18",
        mobile_phase_a="water + 0.1% formic acid",
        mobile_phase_b="acetonitrile + 0.1% formic acid",
        gradient=[(0.0, 5.0), (30.0, 95.0)],
        flow_rate_ml_min=0.3,
        column_temperature_c=40.0,
    )
    base.update(kw)
    return LCMethod(**base)


REFERENCE_METHOD = _method()          # matches the reference gradient window
SHORT_METHOD = _method(gradient=[(0.0, 5.0), (10.0, 95.0)])

QUERCETIN = "Oc1cc(O)c(c(O)c1)-c1oc2cc(O)cc(O)c2c(=O)c1O"
UNKNOWN = "CCCCCCCCCCCCCCCCCC(=O)O"   # not in the lookup table


def test_lookup_hit_on_reference_method_carries_tight_interval() -> None:
    prediction = predict_rt(QUERCETIN, REFERENCE_METHOD)
    assert prediction.predicted_rt_min == pytest.approx(9.8)
    low, high = prediction.prediction_interval
    assert high - low <= 1.3
    assert "lookup" in prediction.model_id or prediction.model_id


def test_lookup_hit_on_other_method_widens_the_interval() -> None:
    on_ref = predict_rt(QUERCETIN, REFERENCE_METHOD)
    on_short = predict_rt(QUERCETIN, SHORT_METHOD)
    # Same point estimate (the table value) but the interval must widen:
    # an RT from a 10-min gradient is not an RT from a 30-min one.
    assert on_short.model_id != on_ref.model_id
    ref_width = on_ref.prediction_interval[1] - on_ref.prediction_interval[0]
    short_width = on_short.prediction_interval[1] - on_short.prediction_interval[0]
    assert short_width > ref_width


def test_unknown_compound_gets_descriptor_baseline() -> None:
    prediction = predict_rt(UNKNOWN, REFERENCE_METHOD)
    assert prediction.model_id != predict_rt(QUERCETIN, REFERENCE_METHOD).model_id
    low, high = prediction.prediction_interval
    assert low <= prediction.predicted_rt_min <= high
    assert high - low >= 5.0  # the baseline interval is wide, honestly


def test_unparseable_smiles_raises_invalid_input() -> None:
    with pytest.raises(InvalidInputError):
        predict_rt("not-a-smiles", REFERENCE_METHOD)


def test_mock_client_returns_none_for_unknown() -> None:
    assert MockRetentionClient().lookup("not-a-smiles") is None
    assert MockRetentionClient.known_compounds() >= 20


def test_anchors_define_a_roundtrip_ri_axis() -> None:
    anchors = [
        AnchorStandard(name="C10", chain_length=10, observed_rt_min=5.0),
        AnchorStandard(name="C14", chain_length=14, observed_rt_min=11.0),
    ]
    from phyto_shared.chromatography import rt_to_ri, ri_to_rt

    ri = rt_to_ri(8.0, anchors)
    # 1000 + (8-5)/(11-5) * (1400-1000) = 1200 -- linear in RT across the
    # segment, invertible against ri_to_rt regardless of homologue gap.
    assert ri == pytest.approx(1200.0)
    assert ri_to_rt(ri, anchors) == pytest.approx(8.0)