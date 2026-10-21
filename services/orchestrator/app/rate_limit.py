"""Per-IP sliding-window rate limiter for the public share endpoints.

Share URLs are unauthenticated (ADR-0044), so they are the one surface a
scraper can hit without an account; 60 reads per minute per IP is far
above any human reading pace and far below scraping throughput.

In-memory windows are correct for the single-instance local plane and
good enough to demonstrate the contract; a multi-instance deployment
moves the counter to Redis (documented in ADR-0044).  The interface
(``allow(key)``) does not change when the backend does.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

__all__ = ["SlidingWindowRateLimiter", "DEFAULT_SHARE_RATE_LIMIT"]

DEFAULT_SHARE_RATE_LIMIT = 60  # requests per minute per IP


class SlidingWindowRateLimiter:
    """True sliding window over the last ``window_seconds``."""

    def __init__(self, max_requests: int = DEFAULT_SHARE_RATE_LIMIT, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, *, now: float | None = None) -> bool:
        moment = now if now is not None else time.monotonic()
        cutoff = moment - self._window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._max:
                return False
            hits.append(moment)
            return True
