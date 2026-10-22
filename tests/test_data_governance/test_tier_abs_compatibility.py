"""Tier/ABS compatibility matrix (ADR-0056, Part 1)."""

from __future__ import annotations

import pytest

from phyto_shared.data_governance import is_tier_compatible_with_abs


class TestTierAbsCompatibility:
    def test_public_requires_clear_or_exempt(self):
        assert is_tier_compatible_with_abs("public", "clear") is True
        assert is_tier_compatible_with_abs("public", "exempt") is True
        assert is_tier_compatible_with_abs("public", "pending") is False
        assert is_tier_compatible_with_abs("public", "unknown") is False

    def test_proprietary_requires_clear(self):
        assert is_tier_compatible_with_abs("proprietary", "clear") is True
        assert is_tier_compatible_with_abs("proprietary", "exempt") is False
        assert is_tier_compatible_with_abs("proprietary", "pending") is False
        assert is_tier_compatible_with_abs("proprietary", "unknown") is False

    def test_training_only_allows_any_status(self):
        for status in ("clear", "pending", "exempt", "unknown"):
            assert is_tier_compatible_with_abs("training_only", status) is True

    def test_unknown_abs_never_reaches_promotable_tier(self):
        # "unknown" is treated as pending for every downstream decision:
        # neither promotable tier may be entered with it.
        assert is_tier_compatible_with_abs("public", "unknown") is False
        assert is_tier_compatible_with_abs("proprietary", "unknown") is False

    def test_helper_is_pure_and_total_over_the_enum(self):
        # Deterministic: same inputs, same answer, every time.
        for tier in ("public", "proprietary", "training_only"):
            for status in ("clear", "pending", "exempt", "unknown"):
                first = is_tier_compatible_with_abs(tier, status)
                second = is_tier_compatible_with_abs(tier, status)
                assert first == second

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            is_tier_compatible_with_abs("galactic", "clear")  # type: ignore[arg-type]