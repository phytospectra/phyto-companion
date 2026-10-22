"""Provenance-manifest governance enforcement (ADR-0056, Part 4).

The proprietary-tier validator rejects analyses whose inputs include a
non-commercial source (NP-MRD in particular); public and training_only
tiers pass; the commercial-plane flag widens enforcement.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from phyto_shared.data_governance import DEFAULT_ABS_STATUS, DEFAULT_DATA_TIER
from phyto_shared.exceptions import ProvenanceError
from phyto_shared.provenance import ProvenanceBuilder

TS = datetime(2026, 10, 22, tzinfo=timezone.utc)


def test_proprietary_with_gnps_and_inhouse_passes(monkeypatch):
    monkeypatch.delenv("PHYTO_ENV", raising=False)
    monkeypatch.delenv("PHYTO_COMMERCIAL_TIER", raising=False)
    manifest = (
        ProvenanceBuilder()
        .with_input_hash("query.csv", "a" * 64)
        .with_data_governance(
            data_tier="proprietary",
            abs_status="clear",
            source_datasets=["gnps_public", "in_house_standard"],
        )
        .build()
    )
    assert manifest.data_tier == "proprietary"
    assert manifest.source_datasets == ["gnps_public", "in_house_standard"]
    assert manifest.manifest_hash


def test_proprietary_with_np_mrd_fails(monkeypatch, caplog):
    monkeypatch.delenv("PHYTO_ENV", raising=False)
    monkeypatch.delenv("PHYTO_COMMERCIAL_TIER", raising=False)
    builder = ProvenanceBuilder().with_data_governance(
        data_tier="proprietary",
        abs_status="clear",
        source_datasets=["np_mrd", "in_house_standard"],
    )
    with caplog.at_level("WARNING", logger="phyto.provenance"):
        with pytest.raises(ProvenanceError) as excinfo:
            builder.build()
    assert excinfo.value.context["offending_sources"] == ["np_mrd"]
    assert excinfo.value.context["tier"] == "proprietary"
    # The rejection is logged with the offending source and tier.
    assert "np_mrd" in caplog.text and "proprietary" in caplog.text


def test_np_mrd_passes_public_tier(monkeypatch):
    monkeypatch.delenv("PHYTO_ENV", raising=False)
    manifest = (
        ProvenanceBuilder()
        .with_data_governance(
            data_tier="public", abs_status="exempt", source_datasets=["np_mrd"]
        )
        .build()
    )
    assert manifest.data_tier == "public"
    assert manifest.source_datasets == ["np_mrd"]


def test_unknown_abs_defaults_to_training_only_and_cannot_promote(monkeypatch):
    monkeypatch.delenv("PHYTO_ENV", raising=False)
    # Defaults: no with_data_governance call means the most restrictive
    # settings -- training_only, unknown ABS, no sources.
    manifest = ProvenanceBuilder().with_input_hash("x.csv", "b" * 64).build()
    assert manifest.data_tier == DEFAULT_DATA_TIER == "training_only"
    assert manifest.abs_status == DEFAULT_ABS_STATUS == "unknown"

    from phyto_shared.data_governance import is_tier_compatible_with_abs

    # The manifest as built cannot be promoted: unknown ABS blocks both
    # promotable tiers, and a proprietary rebuild fails while the source
    # list still names a non-commercial corpus.
    assert is_tier_compatible_with_abs("public", manifest.abs_status) is False
    assert is_tier_compatible_with_abs("proprietary", manifest.abs_status) is False


def test_cloud_commercial_plane_rejects_np_mrd(monkeypatch):
    # PHYTO_ENV=cloud + PHYTO_COMMERCIAL_TIER=1 enforces the commercial
    # rules even at the default tier.
    monkeypatch.setenv("PHYTO_ENV", "cloud")
    monkeypatch.setenv("PHYTO_COMMERCIAL_TIER", "1")
    with pytest.raises(ProvenanceError) as excinfo:
        ProvenanceBuilder().with_data_governance(
            source_datasets=["np_mrd_fixtures"]
        ).build()
    assert excinfo.value.context["offending_sources"] == ["np_mrd_fixtures"]