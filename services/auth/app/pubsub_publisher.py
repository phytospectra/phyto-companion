"""Publishes usage events to Pub/Sub. Logging mock for local."""

from __future__ import annotations

import logging
from typing import Protocol

from phyto_shared.usage_events import UsageEvent

from .config import AuthConfig

logger = logging.getLogger("phyto.auth")


class UsagePublisher(Protocol):
    def publish(self, event: UsageEvent) -> None: ...


class RealPubSubPublisher:
    def __init__(self, project_id: str, topic: str = "usage-events"):
        try:
            from google.cloud import pubsub_v1
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-pubsub not installed. "
                "Install with: pip install google-cloud-pubsub"
            ) from e
        self._publisher = pubsub_v1.PublisherClient()
        self._topic = self._publisher.topic_path(project_id, topic)

    def publish(self, event: UsageEvent) -> None:
        future = self._publisher.publish(
            self._topic, event.model_dump_json().encode()
        )
        future.result(timeout=5)


class LoggingPublisher:
    """Local: log only. No external service touched."""

    def publish(self, event: UsageEvent) -> None:
        logger.info("usage_event: %s", event.model_dump_json())


def get_publisher(config: AuthConfig) -> UsagePublisher:
    if config.dry_run:
        return LoggingPublisher()
    return RealPubSubPublisher(config.project_id)
