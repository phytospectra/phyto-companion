"""The daily cohort job and its stores (ADR-0048).

``run_cohort_job`` reads analytics records from audit chains, computes
the per-cohort metrics with ``phyto_shared.analytics``, evaluates the
guardrails, and writes the cohort table to the ``cohorts`` collection
(Firestore in production, in-memory in dry-run).

Scheduling: the job is a pure function of its inputs plus a store, so
the daily trigger is whatever the deployment prefers (Cloud Scheduler ->
admin endpoint, cron -> CLI).  ``POST /admin/cohorts/recompute`` is the
HTTP trigger and the test entry point.

Cross-service wiring: events live in the audit chain of the service that
raised them (runs here, signups and plan changes in the auth service).
In production the daily job is fed the union of the chains (or their
Firestore mirrors); the record schema is identical, which is what makes
that a plumbing task rather than a port.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

from phyto_shared.analytics import (
    CohortMetrics,
    GuardrailAlert,
    compute_cohort_metrics,
    evaluate_guardrails,
)

__all__ = [
    "CohortStore",
    "InMemoryCohortStore",
    "FirestoreCohortStore",
    "run_cohort_job",
]


class CohortStore(Protocol):
    def put(self, metrics: CohortMetrics) -> None: ...
    def put_alerts(self, alerts: list[GuardrailAlert]) -> None: ...
    def all(self) -> dict[str, CohortMetrics]: ...
    def alerts(self) -> list[GuardrailAlert]: ...


class InMemoryCohortStore:
    def __init__(self) -> None:
        self._cohorts: dict[str, CohortMetrics] = {}
        self._alerts: list[GuardrailAlert] = []

    def put(self, metrics: CohortMetrics) -> None:
        self._cohorts[metrics.cohort_month] = metrics

    def put_alerts(self, alerts: list[GuardrailAlert]) -> None:
        self._alerts = alerts

    def all(self) -> dict[str, CohortMetrics]:
        return dict(self._cohorts)

    def alerts(self) -> list[GuardrailAlert]:
        return list(self._alerts)


class FirestoreCohortStore:
    """Production store: collection ``cohorts``, one document per month."""

    def __init__(self, project_id: str, client=None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._db = client if client is not None else firestore.Client(project=project_id)

    def put(self, metrics: CohortMetrics) -> None:
        self._db.collection("cohorts").document(metrics.cohort_month).set(
            metrics.model_dump(mode="json")
        )

    def put_alerts(self, alerts: list[GuardrailAlert]) -> None:
        self._db.collection("cohorts").document("_alerts").set(
            {"alerts": [alert.model_dump(mode="json") for alert in alerts]}
        )

    def all(self) -> dict[str, CohortMetrics]:
        snaps = self._db.collection("cohorts").stream()
        return {
            snap.id: CohortMetrics.model_validate(snap.to_dict())
            for snap in snaps
            if snap.id != "_alerts"
        }

    def alerts(self) -> list[GuardrailAlert]:
        snap = self._db.collection("cohorts").document("_alerts").get()
        if not snap.exists:
            return []
        return [
            GuardrailAlert.model_validate(entry)
            for entry in (snap.to_dict() or {}).get("alerts", [])
        ]


def run_cohort_job(records: Iterable[dict[str, Any]], store: CohortStore) -> dict:
    """Compute metrics + guardrails for ``records`` and persist them.

    Returns the same payload the admin endpoint serves, so a manual run
    and the daily run always agree about what was computed.
    """
    metrics = compute_cohort_metrics(records)
    for cohort_metrics in metrics.values():
        store.put(cohort_metrics)
    alerts = evaluate_guardrails(metrics)
    store.put_alerts(alerts)
    return {
        "cohorts": [
            metrics[month].model_dump(mode="json") for month in sorted(metrics)
        ],
        "alerts": [alert.model_dump(mode="json") for alert in alerts],
    }
