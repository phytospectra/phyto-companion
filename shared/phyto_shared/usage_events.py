"""
Shared Pydantic models for token metering.

Every operation that consumes tokens emits a UsageEvent. Every
reservation is recorded as a ReservationResult. The auth middleware
reads and writes these; the billing worker consumes them; the ledger
is built from them.

Design constraints:
- All models are immutable (frozen=True, extra="forbid")
- Timestamps are timezone-aware UTC
- Reservation IDs are UUIDs; event IDs are UUIDs used as Firestore
  dedup keys
- Idempotency keys are client-supplied strings, 16-128 chars
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=16, max_length=128)
    operation: str = Field(..., min_length=1, max_length=256)
    tokens: int = Field(..., ge=0, le=100_000)
    metadata: dict = Field(default_factory=dict)


class ReservationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    user_id: str
    tokens_reserved: int
    balance_before: int
    balance_after: int
    status: Literal["reserved", "already_exists", "insufficient_balance"]
    created_at: datetime


class UsageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(..., description="UUID; Pub/Sub dedup key")
    reservation_id: str
    user_id: str
    operation: str
    tokens_consumed: int
    status: Literal["confirmed", "refunded"]
    occurred_at: datetime
    duration_ms: int | None = None
    error: str | None = None
    workspace_id: str | None = Field(
        default=None,
        description="Set when the tokens were drawn from a workspace pool "
        "rather than the user's personal balance (ADR-0047)",
    )
    cache_hit: bool = Field(
        default=False,
        description="True when the response was served from the query cache "
        "and therefore refunded (0 tokens charged, ADR-0046)",
    )


class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: str
    user_id: str
    event_type: Literal["reserve", "confirm", "refund", "topup"]
    tokens: int
    balance_after: int
    reservation_id: str | None = None
    operation: str | None = None
    occurred_at: datetime
    metadata: dict = Field(default_factory=dict)
