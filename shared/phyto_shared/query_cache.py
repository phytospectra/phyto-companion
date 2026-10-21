"""Read-through query cache for deterministic retrieval (ADR-0046).

Literature and NMR-search results are cached for 24 hours, keyed by the
SHA256 of the *normalized* query (lowercased, trimmed, whitespace-
collapsed, filters sorted) plus the operation.  A cache hit is free
(0 tokens) and carries ``X-Cache: HIT``; a miss is charged normally and
writes the cache.  Both measures cut free-tier cost without lowering
quality: the cached value is byte-identical to the upstream response.

Normalization exists so ``"Quercetin bioactivity"`` and
``"  quercetin   bioactivity "`` share an entry; anything semantically
different (different filters, different peaks) must not.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from .provenance import sha256_dict

__all__ = [
    "CACHE_TTL_SECONDS",
    "normalize_query",
    "cache_key",
    "QueryCache",
    "InMemoryQueryCache",
    "FirestoreQueryCache",
]

CACHE_TTL_SECONDS = 24 * 60 * 60
"""24h TTL: long enough to absorb repeated free-tier traffic, short
enough that a retracted paper stops being served within a day."""

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_query(
    query: str,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical form of a query: lowercase, trim, collapse whitespace,
    and sort filter items so key order never splits the cache."""
    normalized = _WHITESPACE_RE.sub(" ", query.strip().lower())
    sorted_filters = (
        {key: filters[key] for key in sorted(filters)} if filters else {}
    )
    return {"query": normalized, "filters": sorted_filters}


def cache_key(operation: str, query: str, filters: Mapping[str, Any] | None = None) -> str:
    normalized = normalize_query(query, filters)
    return sha256_dict({"operation": operation, **normalized})


class QueryCache(Protocol):
    def get(self, key: str, *, now: float | None = None) -> dict[str, Any] | None:
        """The cached payload, or None on miss/expiry."""

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> None: ...


class InMemoryQueryCache:
    """Thread-safe process-local cache (tests, dry-run, single instance)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, key: str, *, now: float | None = None) -> dict[str, Any] | None:
        moment = now if now is not None else time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if moment >= expires_at:
                del self._entries[key]
                return None
            return json.loads(json.dumps(payload))  # defensive copy

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> None:
        moment = now if now is not None else time.monotonic()
        with self._lock:
            self._entries[key] = (moment + ttl_seconds, json.loads(json.dumps(payload)))


class FirestoreQueryCache:
    """Production cache: Firestore collection ``query_cache``.

    Document fields: ``payload`` (the JSON response), ``expires_at`` (UTC
    datetime).  The 24h TTL is enforced on read; a Firestore TTL policy
    on ``expires_at`` garbage-collects the rest.
    """

    COLLECTION = "query_cache"

    def __init__(self, client=None, project_id: str | None = None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._db = client if client is not None else firestore.Client(project=project_id)

    def get(self, key: str, *, now: float | None = None) -> dict[str, Any] | None:
        snap = self._db.collection(self.COLLECTION).document(key).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        expires_at = data.get("expires_at")
        if expires_at is not None and datetime.now(timezone.utc) >= expires_at:
            return None
        return dict(data.get("payload") or {})

    def put(
        self,
        key: str,
        payload: dict[str, Any],
        *,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        now: float | None = None,
    ) -> None:
        self._db.collection(self.COLLECTION).document(key).set(
            {
                "payload": payload,
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            }
        )
