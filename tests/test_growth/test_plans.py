"""Smoke tests for plan tiers and feature gating (ADR-0047).

The observable contract: the plan catalogue defines free/pro/lab/department,
and the gates behave as documented -- free has no API access, pro does, and
SSO plus audit export are department-level.
"""

from __future__ import annotations

import pytest

from phyto_shared.plans import (
    PLAN_FEATURES,
    PLAN_RANK,
    plan_at_or_above,
    plan_features,
    workspace_caps,
)


class TestPlanCatalogue:
    def test_catalogue_contains_the_documented_tiers(self):
        assert {"free", "pro", "lab", "department"} <= set(PLAN_FEATURES)

    def test_rank_order_runs_from_free_to_enterprise(self):
        assert PLAN_RANK["free"] < PLAN_RANK["pro"] < PLAN_RANK["lab"]
        assert PLAN_RANK["lab"] < PLAN_RANK["department"]
        assert PLAN_RANK["department"] < PLAN_RANK["enterprise"]

    def test_free_tier_is_fifty_tokens(self):
        assert plan_features("free").monthly_tokens == 50

    def test_unknown_plan_raises_instead_of_downgrading(self):
        with pytest.raises(ValueError):
            plan_features("platinum")

    def test_none_resolves_to_free(self):
        assert plan_features(None) == plan_features("free")


class TestFeatureGates:
    def test_free_user_is_denied_api_access(self):
        assert plan_features("free").api_access is False
        assert plan_at_or_above("free", "pro") is False

    def test_pro_user_is_granted_api_access(self):
        assert plan_features("pro").api_access is True
        assert plan_at_or_above("pro", "pro") is True

    def test_department_user_is_granted_sso_and_audit_export(self):
        features = plan_features("department")
        assert features.sso is True
        assert features.audit_export is True
        assert plan_at_or_above("department", "department") is True

    def test_sso_is_not_granted_below_department(self):
        assert plan_features("pro").sso is False
        assert plan_at_or_above("pro", "department") is False

    def test_only_free_routes_to_the_distilled_model(self):
        assert plan_features("free").distilled_llm is True
        for plan in ("pro", "lab", "department"):
            assert plan_features(plan).distilled_llm is False


class TestWorkspaceCaps:
    def test_classroom_is_capped_at_thirty_seats_and_five_thousand_tokens(self):
        caps = workspace_caps("classroom")
        assert caps.seats == 30
        assert caps.monthly_pool_tokens == 5_000

    def test_unknown_workspace_type_raises(self):
        with pytest.raises(ValueError):
            workspace_caps("seminar")
