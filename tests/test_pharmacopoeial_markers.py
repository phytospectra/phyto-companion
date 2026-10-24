"""Pharmacopoeial marker prior tests (ADR-0066): data loading and
validation, RT/UV window checks, per-species evaluation."""

from __future__ import annotations

import json


from taxon_prior.markers import (
    PharmacopoeialMarker,
    check_rt_range,
    check_uv_range,
    evaluate_markers,
    load_markers,
    markers_for_species,
)


def test_marker_file_loads_with_at_least_20_species() -> None:
    table = load_markers()
    assert len(table) >= 20


def test_every_entry_carries_a_reference() -> None:
    for species, entries in load_markers().items():
        for marker in entries:
            assert marker.reference, f"{species}/{marker.marker} lacks a reference"


def test_curie_species_finds_its_markers() -> None:
    markers = markers_for_species("Curcuma longa")
    assert markers, "Curcuma longa must carry curated markers"
    names = {m.marker.lower() for m in markers}
    assert any("curcumin" in n for n in names)


def test_unknown_species_returns_empty_not_error() -> None:
    assert markers_for_species("Arabidopsis thaliana") == []


def test_case_insensitive_species_lookup() -> None:
    assert markers_for_species("curcuma LONGA") == markers_for_species("Curcuma longa")


def test_rt_range_check_uses_grace_band() -> None:
    marker = PharmacopoeialMarker(
        marker="x", smiles="C", expected_rt_range_min=(10.0, 12.0),
        reference="Ph.Int.",
    )
    assert check_rt_range(11.0, marker) is True
    assert check_rt_range(9.7, marker) is True    # inside 0.5 min grace
    assert check_rt_range(9.0, marker) is False   # outside grace
    assert check_rt_range(13.0, marker) is False


def test_uv_range_any_secondary_maximum_matches() -> None:
    marker = PharmacopoeialMarker(
        marker="x", smiles="C", expected_rt_range_min=(1.0, 2.0),
        uv_lambda_max_nm=[425.0], uv_secondary_max_nm=[280.0],
        reference="Ph.Int.",
    )
    assert check_uv_range([281.0], marker) is True
    assert check_uv_range([500.0], marker) is False
    assert check_uv_range([], marker) is None       # nothing observed
    no_uv = PharmacopoeialMarker(
        marker="y", smiles="C", expected_rt_range_min=(1.0, 2.0), reference="r"
    )
    assert check_uv_range([425.0], no_uv) is None   # nothing expected


def test_evaluate_markers_leaves_unchecked_expectations_none() -> None:
    hits = evaluate_markers("Curcuma longa", observed_rt_min=None, observed_uv_maxima_nm=None)
    assert hits
    assert all(h.rt_in_range is None for h in hits)
    assert all(h.uv_in_range is None for h in hits)


def test_evaluate_markers_checks_rt_when_observed() -> None:
    hits = evaluate_markers("Curcuma longa", observed_rt_min=15.0, observed_uv_maxima_nm=[])
    assert hits
    assert all(h.rt_in_range is not None for h in hits)


def test_load_markers_skips_invalid_entries(tmp_path) -> None:
    bad_file = tmp_path / "markers.json"
    bad_file.write_text(json.dumps({
        "_meta": {"note": "skip underscore keys"},
        "Test species": [
            {"marker": "good", "smiles": "C",
             "expected_rt_range_min": [1.0, 2.0], "reference": "r1"},
            {"marker": "inverted", "smiles": "C",
             "expected_rt_range_min": [5.0, 1.0], "reference": "r2"},
            {"marker": "missing-ref", "smiles": "C",
             "expected_rt_range_min": [1.0, 2.0]},
        ],
    }))
    table = load_markers(bad_file)
    assert list(table) == ["Test species"]
    assert [m.marker for m in table["Test species"]] == ["good"]


def test_missing_marker_file_degrades_to_empty(tmp_path) -> None:
    table = load_markers(tmp_path / "does-not-exist.json")
    assert table == {}