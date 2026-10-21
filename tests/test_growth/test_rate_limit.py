"""Smoke tests for the share-read rate limiter (ADR-0049).

The observable contract: a request inside the limit is allowed, requests past
the limit are refused, and the window slides rather than resetting as a block.
"""

from __future__ import annotations

from orchestrator.rate_limit import DEFAULT_SHARE_RATE_LIMIT, SlidingWindowRateLimiter


class TestRateLimit:
    def test_single_request_within_the_limit_is_allowed(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        assert limiter.allow("10.0.0.1", now=1000.0) is True

    def test_requests_beyond_the_limit_are_rejected(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        assert limiter.allow("10.0.0.1", now=1000.0) is True
        assert limiter.allow("10.0.0.1", now=1000.1) is True
        assert limiter.allow("10.0.0.1", now=1000.2) is False

    def test_rejection_is_per_key(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
        assert limiter.allow("10.0.0.1", now=1000.0) is True
        assert limiter.allow("10.0.0.1", now=1000.1) is False
        assert limiter.allow("10.0.0.2", now=1000.2) is True

    def test_window_slides_and_allows_again(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        assert limiter.allow("k", now=1000.0) is True
        assert limiter.allow("k", now=1010.0) is True
        assert limiter.allow("k", now=1020.0) is False
        # Both earlier hits have aged out 60s after the first one.
        assert limiter.allow("k", now=1061.0) is True

    def test_documented_default_is_sixty_per_minute(self):
        assert DEFAULT_SHARE_RATE_LIMIT == 60
        limiter = SlidingWindowRateLimiter()
        # 60 requests pass, the 61st inside the same minute does not.
        for index in range(60):
            assert limiter.allow("k", now=1000.0 + index * 0.01) is True
        assert limiter.allow("k", now=1000.7) is False
