"""Source/tier whitelist (ADR-0056, Part 1): NP-MRD is CC-BY-NC and can
never feed a proprietary-tier analysis."""

from __future__ import annotations

from phyto_shared.data_governance import (
    NON_COMMERCIAL_SOURCES,
    SOURCE_DATASET_IDS,
    is_source_allowed_in_tier,
)


class TestSourceTierWhitelist:
    def test_np_mrd_never_proprietary(self):
        assert is_source_allowed_in_tier("np_mrd", "proprietary") is False
        # The derived NP-MRD fixture corpus carries the same terms.
        assert is_source_allowed_in_tier("np_mrd_fixtures", "proprietary") is False

    def test_np_mrd_allowed_public_and_training_only(self):
        assert is_source_allowed_in_tier("np_mrd", "public") is True
        assert is_source_allowed_in_tier("np_mrd", "training_only") is True

    def test_commercial_sources_allowed_proprietary(self):
        for source in ("gnps_public", "in_house_standard", "coconut", "lotus", "user_upload"):
            assert is_source_allowed_in_tier(source, "proprietary") is True

    def test_non_commercial_set_matches_documented_licences(self):
        # Every entry in the non-commercial set is a documented source ID
        # with a non-commercial (or share-alike) row in DATA_SOURCES.md.
        assert NON_COMMERCIAL_SOURCES == {"np_mrd", "np_mrd_fixtures", "nmrshiftdb2"}
        for source in NON_COMMERCIAL_SOURCES:
            assert source in SOURCE_DATASET_IDS