"""Smoke tests for distilled model routing (ADR-0046).

The observable contract: the free plan is served by the distilled model id and
paid plans by the full model id, and the tier is derived from PLAN_FEATURES
rather than hardcoded per call site.
"""

from __future__ import annotations

import pytest

from llm.clients import MockLLMClient
from llm.tiers import (
    DISTILLED_MODEL_ID,
    FULL_MODEL_ID,
    MODEL_TIERS,
    DistilledLLMClient,
    route_client_for_plan,
    select_model_tier,
)


class TestModelIds:
    def test_free_plan_maps_to_the_distilled_model(self):
        assert MODEL_TIERS["free"] == DISTILLED_MODEL_ID
        assert DISTILLED_MODEL_ID != FULL_MODEL_ID

    def test_pro_plan_maps_to_the_full_model(self):
        assert MODEL_TIERS["pro"] == FULL_MODEL_ID

    def test_department_plan_maps_to_the_full_model(self):
        assert MODEL_TIERS["department"] == FULL_MODEL_ID

    def test_every_paid_plan_keeps_the_full_model(self):
        for plan in ("pro", "lab", "department", "enterprise"):
            assert MODEL_TIERS[plan] == FULL_MODEL_ID


class TestTierSelection:
    def test_select_model_tier_free_is_distilled(self):
        assert select_model_tier("free") == "distilled"

    def test_select_model_tier_paid_is_full(self):
        for plan in ("pro", "lab", "department"):
            assert select_model_tier(plan) == "full"

    def test_none_plan_behaves_as_free(self):
        assert select_model_tier(None) == "distilled"

    def test_free_plan_is_routed_to_the_distilled_client(self):
        client, tier = route_client_for_plan(MockLLMClient(), "free")
        assert isinstance(client, DistilledLLMClient)
        assert tier == "distilled"
        assert client.model_id == DISTILLED_MODEL_ID

    def test_pro_plan_keeps_the_base_client(self):
        base = MockLLMClient()
        client, tier = route_client_for_plan(base, "pro")
        assert client is base
        assert tier == "full"

    def test_unavailable_distilled_model_falls_back_and_is_visible(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv("PHYTO_DISTILLED_DISABLED", "1")
        base = MockLLMClient()
        with caplog.at_level("WARNING", logger="phyto.llm.tiers"):
            client, tier = route_client_for_plan(base, "free")
        assert client is base
        assert tier == "full"
        assert "COST ANOMALY" in caplog.text
