"""Unit tests for the growth shared modules.

Covers the pieces with no service surface of their own: plan metadata,
citation rendering/minting, share-token cryptography, the read-through
cache, and cohort metrics/guardrails.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from phyto_shared.analytics import (
    ANALYTICS_EVENTS,
    cohort_month_of,
    compute_cohort_metrics,
    emit_event,
    evaluate_guardrails,
)
from phyto_shared.audit_chain import ChainedAuditLogger
from phyto_shared.citation import (
    MockDOIRegistrar,
    build_citation_record,
    canonical_id_for,
    doi_from_identifier,
    mint_analysis_doi,
    registrar_from_env,
    render_bibtex,
    render_plaintext,
)
from phyto_shared.plans import (
    PLAN_FEATURES,
    PLAN_RANK,
    plan_at_or_above,
    plan_features,
    workspace_caps,
)
from phyto_shared.query_cache import InMemoryQueryCache, cache_key, normalize_query
from phyto_shared.share import (
    DEFAULT_SHARE_EXPIRY_DAYS,
    MAX_SHARE_EXPIRY_DAYS,
    ExpiredShareTokenError,
    InMemoryShareRegistry,
    InvalidShareTokenError,
    RevokedShareTokenError,
    ShareTokenManager,
    share_url_for,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


# ---------------------------------------------------------------------------
# Plans (ADR-0047)
# ---------------------------------------------------------------------------


class TestPlans:
    def test_free_tier_allowance_is_50(self):
        assert plan_features("free").monthly_tokens == 50

    def test_none_means_free(self):
        assert plan_features(None) is PLAN_FEATURES["free"]

    def test_unknown_plan_raises_rather_than_downgrading(self):
        # Silently treating an unknown plan as free would quietly strip a
        # paying customer's features.
        with pytest.raises(ValueError, match="unknown plan"):
            plan_features("platinum")

    def test_rank_order_is_free_to_enterprise(self):
        assert PLAN_RANK["free"] < PLAN_RANK["pro"] < PLAN_RANK["lab"]
        assert PLAN_RANK["lab"] < PLAN_RANK["department"] < PLAN_RANK["enterprise"]

    @pytest.mark.parametrize(
        "plan,minimum,expected",
        [
            ("free", "pro", False),
            ("pro", "pro", True),
            ("lab", "pro", True),
            ("department", "department", True),
            (None, "pro", False),
            ("bogus", "free", False),
        ],
    )
    def test_plan_at_or_above(self, plan, minimum, expected):
        assert plan_at_or_above(plan, minimum) is expected

    def test_only_free_uses_distilled_model(self):
        assert [n for n, f in PLAN_FEATURES.items() if f.distilled_llm] == ["free"]

    def test_api_access_is_pro_and_above(self):
        assert plan_features("free").api_access is False
        for plan in ("pro", "lab", "department", "enterprise"):
            assert plan_features(plan).api_access is True

    def test_sso_and_audit_export_are_department_and_above(self):
        assert plan_features("lab").sso is False
        for plan in ("department", "enterprise"):
            assert plan_features(plan).sso is True
            assert plan_features(plan).audit_export is True

    def test_enterprise_is_marked_custom(self):
        assert plan_features("enterprise").custom is True
        assert plan_features("department").custom is False

    def test_workspace_caps(self):
        assert workspace_caps("classroom").seats == 30
        assert workspace_caps("classroom").monthly_pool_tokens == 5_000
        assert workspace_caps("lab").seats == 10
        assert workspace_caps("department").seats == 200

    def test_unknown_workspace_type_raises(self):
        with pytest.raises(ValueError, match="unknown workspace type"):
            workspace_caps("spaceship")


# ---------------------------------------------------------------------------
# Citation (ADR-0043)
# ---------------------------------------------------------------------------


def _record(**overrides):
    base = dict(
        run_id="run-123",
        title="Analysis of sample s1",
        authors=["alice@example.org"],
        model_version="orchestrator:0.1.0",
        cost_model_version="2026-10-01",
        provenance_manifest_hash=HASH_A,
        audit_chain_head=HASH_B,
    )
    base.update(overrides)
    return build_citation_record(**base)


class TestCitation:
    def test_canonical_id_is_stable(self):
        assert canonical_id_for("run-123") == "phytospectra.run:run-123"

    def test_record_embeds_provenance(self):
        record = _record()
        assert record.provenance_manifest_hash == HASH_A
        assert record.audit_chain_head == HASH_B
        assert record.cost_model_version == "2026-10-01"

    def test_citation_text_contains_bibtex_and_plaintext(self):
        record = _record()
        assert "@misc{phytospectra.run:run-123," in record.citation_text
        assert "alice@example.org" in record.citation_text

    def test_bibtex_uses_doi_when_present(self):
        record = _record(doi="https://doi.org/10.5281/zenodo.1")
        assert "url = {https://doi.org/10.5281/zenodo.1}" in render_bibtex(record)

    def test_bibtex_falls_back_to_app_url(self):
        assert "/runs/run-123" in render_bibtex(_record())

    def test_plaintext_lists_authors_and_year(self):
        record = _record(created_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        plain = render_plaintext(record)
        assert plain.startswith("alice@example.org (2026).")
        assert "phytospectra.run:run-123" in plain

    def test_mint_without_registrar_returns_canonical(self):
        assert mint_analysis_doi("run-9", {}, registrar=None) == (
            "phytospectra.run:run-9"
        )

    def test_mint_is_idempotent_with_registrar(self):
        registrar = MockDOIRegistrar()
        first = mint_analysis_doi("run-9", {}, registrar=registrar)
        second = mint_analysis_doi("run-9", {}, registrar=registrar)
        assert first == second
        assert first.startswith("https://doi.org/")
        assert registrar.calls == ["run-9", "run-9"]

    def test_registration_failure_never_fails_the_run(self):
        class BrokenRegistrar:
            def register(self, run_id, metadata):
                raise RuntimeError("Zenodo is down")

        assert mint_analysis_doi("run-9", {}, registrar=BrokenRegistrar()) == (
            "phytospectra.run:run-9"
        )

    def test_doi_from_identifier(self):
        assert doi_from_identifier("https://doi.org/10.1/x") == "https://doi.org/10.1/x"
        assert doi_from_identifier("phytospectra.run:r1") is None

    def test_registrar_from_env_is_none_without_token(self, monkeypatch):
        monkeypatch.delenv("ZENODO_TOKEN", raising=False)
        assert registrar_from_env() is None


# ---------------------------------------------------------------------------
# Share tokens (ADR-0044)
# ---------------------------------------------------------------------------


class TestShareTokens:
    def _manager(self, registry=None):
        return ShareTokenManager("k" * 32, registry)

    def test_create_and_verify(self):
        manager = self._manager()
        token, claims = manager.create_token("run-1")
        verified = manager.verify(token)
        assert verified.run_id == "run-1"
        assert verified.scope == "read_only"
        assert verified.token_id == claims.token_id

    def test_share_url_shape(self):
        assert share_url_for("abc").endswith("/share/abc")
        assert share_url_for("abc").startswith("https://")

    def test_default_expiry_is_30_days(self):
        _, claims = self._manager().create_token("run-1")
        assert claims.expires_at - claims.issued_at == timedelta(
            days=DEFAULT_SHARE_EXPIRY_DAYS
        )

    def test_expiry_window_is_clamped(self):
        manager = self._manager()
        with pytest.raises(ValueError):
            manager.create_token("run-1", expires_in_days=0)
        with pytest.raises(ValueError):
            manager.create_token("run-1", expires_in_days=MAX_SHARE_EXPIRY_DAYS + 1)

    def test_expired_token_rejected(self):
        manager = self._manager()
        token, _ = manager.create_token(
            "run-1",
            expires_in_days=1,
            now=datetime.now(timezone.utc) - timedelta(days=2),
        )
        with pytest.raises(ExpiredShareTokenError):
            manager.verify(token)

    def test_tampered_payload_rejected(self):
        manager = self._manager()
        token, _ = manager.create_token("run-1")
        body, _, signature = token.partition(".")
        replacement = "aa" if body[-2:] != "aa" else "bb"
        with pytest.raises(InvalidShareTokenError):
            manager.verify(body[:-2] + replacement + "." + signature)

    def test_wrong_key_rejected(self):
        token, _ = self._manager().create_token("run-1")
        with pytest.raises(InvalidShareTokenError):
            ShareTokenManager("z" * 32).verify(token)

    def test_malformed_token_rejected(self):
        with pytest.raises(InvalidShareTokenError):
            self._manager().verify("not-a-token")

    def test_revocation_is_effective(self):
        registry = InMemoryShareRegistry()
        manager = self._manager(registry)
        token, _ = manager.create_token("run-1")
        assert registry.revoke_all("run-1") == 1
        with pytest.raises(RevokedShareTokenError):
            manager.verify(token)

    def test_revocation_only_affects_its_run(self):
        registry = InMemoryShareRegistry()
        manager = self._manager(registry)
        keep, _ = manager.create_token("run-1")
        drop, _ = manager.create_token("run-2")
        registry.revoke_all("run-2")
        assert manager.verify(keep).run_id == "run-1"
        with pytest.raises(RevokedShareTokenError):
            manager.verify(drop)

    def test_revoking_expired_tokens_reports_zero(self):
        registry = InMemoryShareRegistry()
        manager = self._manager(registry)
        manager.create_token(
            "run-1", expires_in_days=1, now=datetime.now(timezone.utc) - timedelta(days=2)
        )
        assert registry.revoke_all("run-1") == 0

    def test_short_key_rejected(self):
        with pytest.raises(ValueError, match="at least 16"):
            ShareTokenManager("short")

    def test_from_env_refuses_without_key_outside_dry_run(self, monkeypatch):
        monkeypatch.delenv("SHARE_HMAC_KEY", raising=False)
        monkeypatch.setenv("PHYTO_DRY_RUN", "0")
        with pytest.raises(RuntimeError, match="SHARE_HMAC_KEY"):
            ShareTokenManager.from_env()

    def test_from_env_uses_dev_key_in_dry_run(self, monkeypatch):
        monkeypatch.delenv("SHARE_HMAC_KEY", raising=False)
        monkeypatch.setenv("PHYTO_DRY_RUN", "1")
        manager = ShareTokenManager.from_env()
        assert manager.verify(manager.create_token("run-1")[0]).run_id == "run-1"


# ---------------------------------------------------------------------------
# Read-through cache (ADR-0046)
# ---------------------------------------------------------------------------


class TestQueryCache:
    def test_normalization_ignores_case_and_whitespace(self):
        assert normalize_query("  Quercetin   Bioactivity ") == normalize_query(
            "quercetin bioactivity"
        )

    def test_filter_order_does_not_split_the_cache(self):
        assert cache_key("op", "q", {"b": 2, "a": 1}) == cache_key(
            "op", "q", {"a": 1, "b": 2}
        )

    def test_different_filters_miss(self):
        assert cache_key("op", "q", {"a": 1}) != cache_key("op", "q", {"a": 2})

    def test_different_operation_misses(self):
        assert cache_key("op1", "q") != cache_key("op2", "q")

    def test_put_then_get(self):
        cache = InMemoryQueryCache()
        cache.put("k", {"value": 1})
        assert cache.get("k") == {"value": 1}

    def test_miss_returns_none(self):
        assert InMemoryQueryCache().get("absent") is None

    def test_expiry_is_enforced_on_read(self):
        cache = InMemoryQueryCache()
        cache.put("k", {"value": 1}, ttl_seconds=10, now=100.0)
        assert cache.get("k", now=105.0) == {"value": 1}
        assert cache.get("k", now=111.0) is None

    def test_get_returns_a_copy(self):
        cache = InMemoryQueryCache()
        cache.put("k", {"nested": [1, 2]})
        cache.get("k")["nested"].append(3)
        assert cache.get("k") == {"nested": [1, 2]}


# ---------------------------------------------------------------------------
# Cohort analytics (ADR-0048)
# ---------------------------------------------------------------------------


class TestAnalyticsEvents:
    def test_unknown_event_raises(self, tmp_path):
        sink = ChainedAuditLogger(tmp_path / "audit.jsonl")
        with pytest.raises(ValueError, match="unknown analytics event"):
            emit_event(sink, "user_signed_up", user_id="alice")

    def test_event_is_appended_as_analytics_kind(self, tmp_path):
        sink = ChainedAuditLogger(tmp_path / "audit.jsonl")
        record = emit_event(sink, "user_signup", user_id="alice")
        assert record["kind"] == "analytics_event"
        assert record["user_id"] == "alice"
        assert record["cohort_month"] == cohort_month_of(datetime.now(timezone.utc))
        assert sink.verify_chain() is True

    def test_event_vocabulary_is_the_documented_set(self):
        assert ANALYTICS_EVENTS == {
            "user_signup",
            "run_started",
            "run_completed",
            "run_shared",
            "run_cited",
            "plan_changed",
            "workspace_member_activated",
        }


def _analytics(records, kind="analytics_event"):
    """Build audit-shaped analytics records for metric tests."""
    out = []
    for i, (event, user, month, detail, ts) in enumerate(records):
        out.append(
            {
                "seq": i + 1,
                "kind": kind,
                "event": event,
                "user_id": user,
                "cohort_month": month,
                "detail": detail,
                "timestamp": ts,
            }
        )
    return out


class TestCohortMetrics:
    def test_signup_alone_does_not_activate(self):
        records = _analytics(
            [("user_signup", "alice", "2026-01", {}, "2026-01-05T00:00:00+00:00")]
        )
        metrics = compute_cohort_metrics(records)
        assert metrics["2026-01"].signups == 1
        assert metrics["2026-01"].activated == 0
        assert metrics["2026-01"].activated_rate == 0.0

    def test_completed_run_activates(self):
        records = _analytics(
            [
                ("user_signup", "alice", "2026-01", {}, "2026-01-05T00:00:00+00:00"),
                (
                    "run_completed",
                    "alice",
                    "2026-01",
                    {"cost_usd": 0.5},
                    "2026-01-06T00:00:00+00:00",
                ),
            ]
        )
        metrics = compute_cohort_metrics(records)
        assert metrics["2026-01"].activated == 1
        assert metrics["2026-01"].activated_rate == 1.0
        assert metrics["2026-01"].infra_cost_usd == 0.5

    def test_d30_requires_activity_inside_the_window(self):
        records = _analytics(
            [
                ("user_signup", "alice", "2026-01", {}, "2026-01-01T00:00:00+00:00"),
                ("run_completed", "alice", "2026-01", {}, "2026-01-02T00:00:00+00:00"),
                ("run_started", "alice", "2026-01", {}, "2026-01-30T00:00:00+00:00"),
            ]
        )
        metrics = compute_cohort_metrics(records)
        assert metrics["2026-01"].d30_retained == 1

    def test_activity_outside_window_is_not_d30(self):
        records = _analytics(
            [
                ("user_signup", "alice", "2026-01", {}, "2026-01-01T00:00:00+00:00"),
                ("run_completed", "alice", "2026-01", {}, "2026-01-02T00:00:00+00:00"),
                ("run_started", "alice", "2026-01", {}, "2026-03-01T00:00:00+00:00"),
            ]
        )
        assert compute_cohort_metrics(records)["2026-01"].d30_retained == 0

    def test_plan_change_counts_as_conversion(self):
        records = _analytics(
            [
                ("user_signup", "alice", "2026-01", {}, "2026-01-01T00:00:00+00:00"),
                ("run_completed", "alice", "2026-01", {}, "2026-01-02T00:00:00+00:00"),
                (
                    "plan_changed",
                    "alice",
                    "2026-01",
                    {"old_plan": "free", "new_plan": "pro", "mrr_usd": 15.0},
                    "2026-01-10T00:00:00+00:00",
                ),
            ]
        )
        metrics = compute_cohort_metrics(records)["2026-01"]
        assert metrics.converted == 1
        assert metrics.conversion_rate == 1.0
        assert metrics.gross_revenue_usd == 15.0

    def test_gross_margin_is_none_without_revenue(self):
        records = _analytics(
            [
                ("user_signup", "alice", "2026-01", {}, "2026-01-01T00:00:00+00:00"),
                (
                    "run_completed",
                    "alice",
                    "2026-01",
                    {"cost_usd": 2.0},
                    "2026-01-02T00:00:00+00:00",
                ),
            ]
        )
        metrics = compute_cohort_metrics(records)["2026-01"]
        # Undefined, not silently 0 or -inf.
        assert metrics.gross_margin is None
        assert metrics.contribution_per_user == -2.0

    def test_workflow_events_are_ignored(self):
        # The engine logs its own "run_started" with kind="event"; it must
        # not be mistaken for an analytics record.
        records = _analytics(
            [("run_started", "alice", "2026-01", {}, "2026-01-01T00:00:00+00:00")],
            kind="event",
        )
        assert compute_cohort_metrics(records) == {}

    def test_blank_user_is_skipped(self):
        records = _analytics(
            [("user_signup", "", "2026-01", {}, "2026-01-01T00:00:00+00:00")]
        )
        assert compute_cohort_metrics(records) == {}


class TestGuardrails:
    def _metrics(self, **overrides):
        base = dict(
            cohort_month="2026-01",
            signups=10,
            activated=5,
            activated_rate=0.5,
            d30_retained=3,
            d30_rate=0.6,
            converted=1,
            conversion_rate=0.2,
            gross_revenue_usd=0.0,
            infra_cost_usd=0.0,
            gross_margin=None,
            contribution_per_user=1.0,
        )
        base.update(overrides)
        from phyto_shared.analytics import CohortMetrics

        return CohortMetrics(**base)

    def test_negative_contribution_fires(self):
        alert = evaluate_guardrails({"2026-01": self._metrics(contribution_per_user=-1.5)})
        assert [a.kind for a in alert] == ["negative_contribution"]

    def test_healthy_cohort_fires_nothing(self):
        assert evaluate_guardrails({"2026-01": self._metrics()}) == []

    def test_signups_rising_with_flat_contribution_fires(self):
        older = self._metrics(cohort_month="2026-01", signups=10, contribution_per_user=2.0)
        newer = self._metrics(cohort_month="2026-02", signups=20, contribution_per_user=2.0)
        alerts = evaluate_guardrails({"2026-01": older, "2026-02": newer})
        assert [a.kind for a in alerts] == ["contribution_declining_while_signups_rise"]

    def test_signups_rising_with_improving_contribution_is_clean(self):
        older = self._metrics(cohort_month="2026-01", signups=10, contribution_per_user=2.0)
        newer = self._metrics(cohort_month="2026-02", signups=20, contribution_per_user=3.0)
        assert evaluate_guardrails({"2026-01": older, "2026-02": newer}) == []
