"""FastAPI entrypoint for the billing worker.

Pub/Sub pushes a base64-encoded :class:`UsageEvent` to ``POST /pubsub/push``.
The handler must never return 5xx for a malformed message: Pub/Sub retries
5xx indefinitely and a poison message would block the subscription. Malformed
input is acknowledged with 200 and an explicit error. A *valid* message whose
ledger write fails does raise 500, because dropping it silently would lose
billing data.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from phyto_shared.usage_events import UsageEvent

from . import __version__

logger = logging.getLogger("phyto.billing")

__all__ = [
    "app",
    "create_app",
    "BillingConfig",
    "PubSubEnvelope",
    "LedgerWriter",
    "InMemoryLedgerWriter",
    "FirestoreLedgerWriter",
    "get_ledger_writer",
]


# ---------------------------------------------------------------------------
# Configuration and transport models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BillingConfig:
    project_id: str = "phyto-companion-prod"
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "BillingConfig":
        import os

        cloud = os.getenv("PHYTO_ENV") == "cloud"
        # Local is dry-run by default so no GCP client is constructed.
        explicit = os.getenv("PHYTO_DRY_RUN")
        dry_run = (not cloud) if explicit is None else explicit == "1"
        return cls(
            project_id=os.getenv("GOOGLE_CLOUD_PROJECT", "phyto-companion-prod"),
            dry_run=dry_run,
        )


class PubSubEnvelope(BaseModel):
    """The subset of Pub/Sub's push format this worker relies on."""

    model_config = ConfigDict(extra="allow")

    class Message(BaseModel):
        model_config = ConfigDict(extra="allow")

        data: str = Field(..., description="Base64-encoded UsageEvent JSON")
        message_id: str | None = None
        attributes: dict[str, str] = Field(default_factory=dict)

    message: Message
    subscription: str | None = None


# ---------------------------------------------------------------------------
# Ledger writers
# ---------------------------------------------------------------------------


class LedgerWriter(Protocol):
    def write(self, event: UsageEvent) -> bool:
        """Persist one event. Returns False if it was already present."""


class InMemoryLedgerWriter:
    """Idempotent on ``event_id``: the same event can be delivered twice."""

    def __init__(self) -> None:
        self._events: dict[str, UsageEvent] = {}

    def write(self, event: UsageEvent) -> bool:
        if event.event_id in self._events:
            return False
        self._events[event.event_id] = event
        return True

    def all_events(self) -> list[UsageEvent]:
        return list(self._events.values())


class FirestoreLedgerWriter:
    """Firestore document ID is the event ID, which makes the write idempotent.

    ``create()`` fails atomically if the document already exists, so two
    workers racing on the same Pub/Sub redelivery cannot double-write.
    """

    def __init__(self, project_id: str, client=None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._db = client if client is not None else firestore.Client(project=project_id)

    def write(self, event: UsageEvent) -> bool:
        from google.api_core.exceptions import AlreadyExists

        ref = self._db.collection("usage_ledger").document(event.event_id)
        try:
            ref.create(event.model_dump(mode="json"))
        except AlreadyExists:
            return False
        return True


def get_ledger_writer(config: BillingConfig) -> LedgerWriter:
    if config.dry_run:
        return InMemoryLedgerWriter()
    return FirestoreLedgerWriter(config.project_id)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def _decode_event(envelope: PubSubEnvelope) -> UsageEvent:
    raw = base64.b64decode(envelope.message.data, validate=True)
    return UsageEvent.model_validate_json(raw)


def create_app(
    config: BillingConfig | None = None,
    *,
    ledger: LedgerWriter | None = None,
) -> FastAPI:
    cfg = config or BillingConfig.from_env()
    writer = ledger or get_ledger_writer(cfg)

    app = FastAPI(
        title="PhytoCompanion Billing Service",
        version=__version__,
        description="Asynchronous usage audit and Stripe top-up.",
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "billing",
            "version": __version__,
            "dry_run": cfg.dry_run,
            "ledger": type(writer).__name__,
        }

    @app.post("/pubsub/push")
    def pubsub_push(envelope: PubSubEnvelope):
        try:
            event = _decode_event(envelope)
        except (binascii.Error, ValueError, ValidationError) as exc:
            # Acknowledge poison messages; 5xx would be retried forever.
            logger.warning("malformed usage event: %s", exc)
            return JSONResponse(
                status_code=200,
                content={"status": "malformed", "error": f"{type(exc).__name__}: {exc}"},
            )

        try:
            is_new = writer.write(event)
        except Exception:
            # Valid message, failed write: retryable, and must not be dropped.
            logger.exception("ledger write failed for event %s", event.event_id)
            raise

        if not is_new:
            return {"status": "ok", "duplicate": True}
        return {"status": "ok", "duplicate": False}

    return app


app = create_app()
