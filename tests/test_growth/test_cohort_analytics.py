"""Smoke tests for cohort analytics (ADR-0048).

The observable contract: a synthetic month of events yields the five metrics,
activation requires a completed run (not a signup), and contribution per user
goes negative when infra cost exceeds revenue.
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

JAN = datetime(2026, 1, 1, tzinfo=timezone.utc)
FEB = datetime(2026, 2, 1, tzinfo=timezone.utc)


def _event(sink, event, *, user_id, at, **properties):
    return emit_event(sink, event, user_id=user_id, properties=properties, timestamp=at)


def _january_log(tmp_path):
    """3 signups, 2 completed runs, 1 conversion: revenue $10, infra $6."""
    log = ChainedAuditLogger(tmp_path / "audit.jsonl")
    for user in ("u1", "u2", "u3"):
        _event(log, "user_signup", user_id=user, at=JAN + timedelta(days=1))
    for user in ("u1", "u2"):
        _event(
            log, "run_completed", user_id=user, at=JAN + timedelta(days=2),
            cost_usd=3.0, tokens_consumed=20, model_tier="distilled",
        )
    _event(
        log, "plan_changed", user_id="u1", at=JAN + timedelta(days=3),
        old_plan="free", new_plan="pro", mrr_usd=10.0,
    )
    return log.records()


class TestFiveMetrics:
    def test_synthetic_month_yields_all_five_metrics(self, tmp_path):
        january = compute_cohort_metrics(_january_log(tmp_path))["2026-01"]
        assert january.signups == 3
        assert january.activated == 2            # activation
        assert january.d30_retained >= 0         # D30
        assert january.converted == 1            # conversion
        assert january.gross_margin is not None  # gross margin
        assert january.contribution_per_user is not None

    def test_contribution_is_negative_when_cost_exceeds_revenue(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "loss.jsonl")
        _event(log, "user_signup", user_id="u1", at=JAN)
        _event(
            log, "run_completed", user_id="u1", at=JAN + timedelta(days=1),
            cost_usd=40.0,
        )
        assert compute_cohort_metrics(log.records())["2026-01"].contribution_per_user < 0

    def test_gross_margin_is_none_without_revenue(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "free.jsonl")
        _event(log, "user_signup", user_id="u1", at=JAN)
        _event(
            log, "run_completed", user_id="u1", at=JAN + timedelta(days=1),
            cost_usd=1.0,
        )
        assert compute_cohort_metrics(log.records())["2026-01"].gross_margin is None


class TestActivation:
    def test_signup_alone_does_not_activate(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "signup.jsonl")
        _event(log, "user_signup", user_id="u1", at=JAN)
        january = compute_cohort_metrics(log.records())["2026-01"]
        assert january.signups == 1
        assert january.activated == 0
        assert january.activated_rate == 0.0

    def test_completed_run_activates(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "complete.jsonl")
        _event(log, "user_signup", user_id="u1", at=JAN)
        _event(log, "run_completed", user_id="u1", at=JAN + timedelta(days=1))
        assert compute_cohort_metrics(log.records())["2026-01"].activated == 1


class TestGuardrails:
    def test_negative_contribution_fires(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "neg.jsonl")
        _event(log, "user_signup", user_id="u1", at=JAN)
        _event(
            log, "run_completed", user_id="u1", at=JAN + timedelta(days=1),
            cost_usd=40.0,
        )
        alerts = evaluate_guardrails(compute_cohort_metrics(log.records()))
        assert [alert.kind for alert in alerts] == ["negative_contribution"]

    def test_healthy_cohort_fires_nothing(self, tmp_path):
        alerts = evaluate_guardrails(compute_cohort_metrics(_january_log(tmp_path)))
        assert alerts == []

    def test_event_vocabulary_is_stable(self):
        assert ANALYTICS_EVENTS == frozenset({
            "user_signup", "run_started", "run_completed", "run_shared",
            "run_cited", "plan_changed", "workspace_member_activated",
        })

    def test_unknown_event_name_is_rejected(self, tmp_path):
        log = ChainedAuditLogger(tmp_path / "typo.jsonl")
        with pytest.raises(ValueError):
            emit_event(log, "run_completeed", user_id="u1")

    def test_cohort_key_is_utc_year_month(self):
        assert cohort_month_of(JAN) == "2026-01"
        assert cohort_month_of(FEB) == "2026-02"
