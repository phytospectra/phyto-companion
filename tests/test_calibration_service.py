"""Calibration service tests (ADR-0061): Level-1 matching, RT offset,
store isolation, and the API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from phyto_shared.chromatography import LCMethod, ReferenceStandard

from calibration.main import (
    ObservedPeak,
    apply_rt_offset,
    compute_rt_offset,
    get_store,
    match_peak,
    set_store,
    InMemoryHomeLibraryStore,
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


CAFFEINE = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"


def _standard(**kw) -> ReferenceStandard:
    base = dict(
        name="caffeine standard", smiles=CAFFEINE, rt_min=3.10,
        uv_spectrum=[(270.0, 1.0), (280.0, 0.9)],
        ms2=[(195.0, 100.0), (138.0, 40.0)],
    )
    base.update(kw)
    return ReferenceStandard(**base)


# --- Level-1 matching --------------------------------------------------------


def test_rt_only_match_is_level_1() -> None:
    outcome = match_peak(ObservedPeak(rt_min=3.08), [_standard()])
    assert outcome.level == 1
    assert outcome.matched_standard == "caffeine standard"
    assert outcome.property_scores["rt_delta_min"] == 0.02


def test_rt_mismatch_fails_even_without_other_properties() -> None:
    outcome = match_peak(ObservedPeak(rt_min=5.0), [_standard()])
    assert outcome.level == 0
    assert outcome.matched_standard is None
    assert outcome.property_scores["rt_delta_min"] == 1.9


def test_uv_agreement_confirms_and_disagreement_rejects() -> None:
    same = _standard()
    outcome = match_peak(
        ObservedPeak(rt_min=3.1, uv_spectrum=[(270.0, 1.0), (280.0, 0.9)]),
        [same],
    )
    assert outcome.level == 1
    outcome = match_peak(
        ObservedPeak(rt_min=3.1, uv_spectrum=[(430.0, 1.0), (440.0, 0.9)]),
        [same],
    )
    assert outcome.level == 0
    assert "uv_cosine" in outcome.property_scores


def test_peak_with_uv_against_uvless_standard_fails() -> None:
    bare = _standard(uv_spectrum=[], ms2=[])
    outcome = match_peak(
        ObservedPeak(rt_min=3.1, uv_spectrum=[(270.0, 1.0)]), [bare]
    )
    assert outcome.level == 0
    assert "UV" in outcome.notes


def test_ties_resolve_to_smallest_rt_delta() -> None:
    near = _standard(name="near", rt_min=3.10)
    far = _standard(name="far", rt_min=3.18, smiles="O=C(O)c1cc(O)c(O)c(O)c1")
    outcome = match_peak(ObservedPeak(rt_min=3.11), [far, near])
    assert outcome.matched_standard == "near"


def test_no_standards_is_level_zero() -> None:
    outcome = match_peak(ObservedPeak(rt_min=3.1), [])
    assert outcome.level == 0


# --- RT offset ---------------------------------------------------------------


def test_compute_rt_offset_is_observed_minus_predicted() -> None:
    offset = compute_rt_offset(_standard(rt_min=8.9), predicted_rt_min=8.2)
    assert offset == 0.7


def test_apply_rt_offset_clamps_at_zero() -> None:
    assert apply_rt_offset(2.0, -3.0) == 0.0
    assert apply_rt_offset(2.0, 0.5) == 2.5


# --- Store isolation ---------------------------------------------------------


def test_store_isolates_methods_and_users() -> None:
    set_store(InMemoryHomeLibraryStore())
    get_store().save_standards("u1", "m1", [_standard()], _method())
    assert get_store().load_standards("u1", "m2") == []
    assert get_store().load_standards("u2", "m1") == []
    assert len(get_store().load_standards("u1", "m1")) == 1


# --- API ---------------------------------------------------------------------


def _client() -> TestClient:
    from calibration.main import create_app

    set_store(InMemoryHomeLibraryStore())
    return TestClient(create_app())


def test_calibrate_standards_then_match() -> None:
    client = _client()
    resp = client.post(
        "/calibrate/standards",
        json={"method_id": "m1", "standards": [_standard().model_dump()]},
    )
    assert resp.status_code == 200, resp.text
    match = client.post(
        "/calibrate/match",
        json={
            "method_id": "m1",
            "peak": {"rt_min": 3.08},
        },
    )
    assert match.status_code == 200
    assert match.json()["match"]["level"] == 1