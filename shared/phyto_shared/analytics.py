"""Cohort analytics: event emission and per-cohort metric computation.

The five metrics that tell us whether growth is profitable -- activation,
D30 retention, conversion, gross margin, contribution per user -- are
computed automatically per monthly cohort (ADR-0048).  Growth is only
pursued while ``contribution_per_user`` is positive and rising; the
guardrail turns that sentence into code.

Events are appended to hash-chained audit logs via
``ChainedAuditLogger.log_analytics`` -- the audit chain already gives
tamper-evidence, so a separate analytics pipeline would buy nothing and
could disagree with the audit trail about what happened.

Event vocabulary (the names are the contract; do not rename casually):

* ``user_signup``               detail: {cohort_month}
* ``run_started``               detail: {run_id, workspace_id, plan}
* ``run_completed``             detail: {run_id, tokens_consumed,
                                  model_tier, cost_usd}
* ``run_shared``                detail: {share_token, run_id}
* ``run_cited``                 detail: {citation_id, run_id}
* ``plan_changed``              detail: {old_plan, new_plan, mrr_usd}
* ``workspace_member_activated`` detail: {workspace_id}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ANALYTICS_EVENTS",
    "cohort_month_of",
    "AnalyticsSink",
    "emit_event",
    "CohortMetrics",
    "GuardrailAlert",
    "compute_cohort_metrics",
    "evaluate_guardrails",
]


ANALYTICS_EVENTS = frozenset(
    {
        "user_signup",
        "run_started",
        "run_completed",
        "run_shared",
        "run_cited",
        "plan_changed",
        "workspace_member_activated",
    }
)


def cohort_month_of(moment: datetime) -> str:
    """``YYYY-MM`` in UTC; the cohort key."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


class AnalyticsSink(Protocol):
    """Anything that can append analytics events (ChainedAuditLogger)."""

    def log_analytics(
        self,
        *,
        event: str,
        user_id: str,
        cohort_month: str | None = None,
        run_id: str | None = None,
        properties: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]: ...


def emit_event(
    sink: AnalyticsSink,
    event: str,
    *,
    user_id: str,
    run_id: str | None = None,
    properties: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Append one analytics event to a chained audit sink.

    Unknown event names raise: a typo'd event silently disappears from
    every cohort, which is exactly the failure this guards against.
    """
    if event not in ANALYTICS_EVENTS:
        raise ValueError(
            f"unknown analytics event {event!r}; known: {sorted(ANALYTICS_EVENTS)}"
        )
    return sink.log_analytics(
        event=event,
        user_id=user_id,
        run_id=run_id,
        properties=properties,
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Cohort metrics
# ---------------------------------------------------------------------------


class CohortMetrics(BaseModel):
    """One monthly cohort's profitability metrics (ADR-0048)."""

    model_config = ConfigDict(extra="forbid")

    cohort_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    signups: int = Field(..., ge=0)
    activated: int = Field(..., ge=0)
    activated_rate: float = Field(..., ge=0.0, le=1.0)
    d30_retained: int = Field(..., ge=0)
    d30_rate: float = Field(..., ge=0.0, le=1.0)
    converted: int = Field(..., ge=0)
    conversion_rate: float = Field(..., ge=0.0, le=1.0)
    gross_revenue_usd: float = Field(..., ge=0.0)
    infra_cost_usd: float = Field(..., ge=0.0)
    gross_margin: float | None = Field(
        default=None,
        description="(revenue - infra) / revenue; None when revenue is 0 "
        "(undefined, not silently 0 or -inf)",
    )
    contribution_per_user: float = Field(
        ..., description="(revenue - infra) / signups; negative means growth "
        "destroyed money in this cohort"
    )


class GuardrailAlert(BaseModel):
    """A cohort guardrail firing (ADR-0048)."""

    model_config = ConfigDict(extra="forbid")

    cohort_month: str
    kind: str = Field(
        ..., description="'negative_contribution' or "
        "'contribution_declining_while_signups_rise'"
    )
    message: str


def _parse_timestamp(record: dict[str, Any]) -> datetime | None:
    raw = record.get("timestamp")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def compute_cohort_metrics(
    records: Iterable[dict[str, Any]],
) -> dict[str, CohortMetrics]:
    """Compute per-cohort metrics from audit-chain records.

    Only ``kind == "analytics_event"`` records are considered; workflow
    events share names (the engine logs its own ``run_started``) but never
    carry analytics structure.  Activation requires a ``run_completed``
    event -- a signup alone never counts (the teaching-loop metric is
    "completed one full analysis", not "signed in").
    """
    signups: dict[str, set[str]] = {}
    signup_times: dict[str, datetime] = {}
    completions: dict[str, set[str]] = {}
    activity: dict[str, list[datetime]] = {}
    latest_plan: dict[str, str] = {}
    revenue: dict[str, float] = {}
    infra_cost: dict[str, float] = {}

    for record in records:
        if record.get("kind") != "analytics_event":
            continue
        event = record.get("event")
        user_id = record.get("user_id")
        if not user_id:
            continue
        cohort = record.get("cohort_month") or cohort_month_of(
            _parse_timestamp(record) or datetime.now(timezone.utc)
        )
        detail = record.get("detail") or {}
        moment = _parse_timestamp(record) or datetime.now(timezone.utc)

        if event == "user_signup":
            signups.setdefault(cohort, set()).add(user_id)
            # First signup wins so a duplicate record cannot move the cohort.
            signup_times.setdefault(user_id, moment)
        elif event == "run_completed":
            completions.setdefault(cohort, set()).add(user_id)
            activity.setdefault(user_id, []).append(moment)
            infra_cost[cohort] = infra_cost.get(cohort, 0.0) + float(
                detail.get("cost_usd") or 0.0
            )
        elif event == "run_started":
            activity.setdefault(user_id, []).append(moment)
        elif event == "plan_changed":
            latest_plan[user_id] = str(detail.get("new_plan") or "free")
            revenue[cohort] = revenue.get(cohort, 0.0) + float(
                detail.get("mrr_usd") or 0.0
            )

    metrics: dict[str, CohortMetrics] = {}
    for cohort, cohort_users in signups.items():
        completed = completions.get(cohort, set())
        activated_users = cohort_users & completed
        activated = len(activated_users)

        # D30: ran anything in the [signup+25d, signup+35d] window.
        d30 = 0
        for user in activated_users:
            joined = signup_times[user]
            window_start = joined + timedelta(days=25)
            window_end = joined + timedelta(days=35)
            if any(
                window_start <= stamp <= window_end
                for stamp in activity.get(user, [])
            ):
                d30 += 1

        converted = sum(
            1 for user in cohort_users if latest_plan.get(user, "free") != "free"
        )
        rev = revenue.get(cohort, 0.0)
        cost = infra_cost.get(cohort, 0.0)
        signups_n = len(cohort_users)

        metrics[cohort] = CohortMetrics(
            cohort_month=cohort,
            signups=signups_n,
            activated=activated,
            activated_rate=activated / signups_n,
            d30_retained=d30,
            d30_rate=(d30 / activated) if activated else 0.0,
            converted=converted,
            conversion_rate=(converted / activated) if activated else 0.0,
            gross_revenue_usd=rev,
            infra_cost_usd=cost,
            gross_margin=((rev - cost) / rev) if rev > 0 else None,
            contribution_per_user=(rev - cost) / signups_n,
        )
    return metrics


def evaluate_guardrails(
    metrics: Mapping[str, CohortMetrics],
) -> list[GuardrailAlert]:
    """Fire the profitability guardrails over cohorts, oldest first.

    A negative ``contribution_per_user`` always fires.  A cohort whose
    contribution did not improve (declined or stayed flat) while signups
    rose also fires: buying more signups at flat-or-worse unit economics
    is the exact failure mode ADR-0048 exists to prevent.
    """
    alerts: list[GuardrailAlert] = []
    previous: CohortMetrics | None = None
    for cohort in sorted(metrics):
        current = metrics[cohort]
        if current.contribution_per_user < 0:
            alerts.append(
                GuardrailAlert(
                    cohort_month=cohort,
                    kind="negative_contribution",
                    message=(
                        f"Cohort {cohort} has negative contribution per user "
                        f"(${current.contribution_per_user:.2f}): growth is "
                        "destroying money. Review pricing and free-tier "
                        "limits before pursuing further growth."
                    ),
                )
            )
        if (
            previous is not None
            and current.signups > previous.signups
            and current.contribution_per_user <= previous.contribution_per_user
        ):
            alerts.append(
                GuardrailAlert(
                    cohort_month=cohort,
                    kind="contribution_declining_while_signups_rise",
                    message=(
                        f"Cohort {cohort} signups rose "
                        f"({previous.signups} -> {current.signups}) but "
                        f"contribution per user did not improve "
                        f"(${previous.contribution_per_user:.2f} -> "
                        f"${current.contribution_per_user:.2f})."
                    ),
                )
            )
        previous = current
    return alerts
