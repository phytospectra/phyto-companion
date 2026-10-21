"""Token usage and reservation domain models.

SCAFFOLDING (Step 6.5). The shape of the token system is fixed by ADR-0028:

* ``ReservationRequest`` -- sent by the auth middleware before a metered call.
* ``ReservationResult``  -- Firestore's answer: granted or denied, plus the
  resulting balance. Reservation is SYNCHRONOUS, so a user cannot outrun their
  balance by firing concurrent requests.
* ``UsageEvent``         -- published to Pub/Sub after a call for the billing
  worker to audit ASYNCHRONOUSLY. It never blocks the user's request.
* ``LedgerEntry``        -- one immutable row of the append-only ledger, the
  authoritative record used for reconciliation and support.

Money is never represented as a float. Token counts are integers; any monetary
amount is carried as minor currency units (e.g. cents) in an ``int``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "TOKEN_STRICT",
    "ReservationRequest",
    "ReservationResult",
    "UsageEvent",
    "LedgerEntry",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """No extra fields: a typo at the billing boundary must fail loudly."""

    model_config = ConfigDict(frozen=False, extra="forbid")


NonNegativeTokens = Annotated[int, Field(ge=0)]
PlanTier = Literal["free", "pro"]
ReservationOutcome = Literal["granted", "denied"]
LedgerKind = Literal["grant", "reserve", "refund", "purchase", "adjustment"]

TOKEN_STRICT = StrictModel


class ReservationRequest(StrictModel):
    """Ask the ledger to hold ``tokens`` for one metered call."""

    user_id: str = Field(min_length=1, description="Firebase UID")
    request_id: str = Field(
        min_length=1,
        description="Idempotency key; retrying with the same id must not double-charge",
    )
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    tokens: NonNegativeTokens


class ReservationResult(StrictModel):
    """The synchronous answer to a :class:`ReservationRequest`."""

    granted: bool
    request_id: str = Field(min_length=1)
    tokens_reserved: NonNegativeTokens
    balance_after: int = Field(
        ge=0,
        description="Remaining balance including the reservation; never negative",
    )
    plan: PlanTier
    reason: str | None = Field(
        default=None,
        description="Populated when denied (e.g. 'insufficient_balance')",
    )

    @model_validator(mode="after")
    def _reason_required_when_denied(self) -> "ReservationResult":
        # A denial with no explanation is unactionable for the UI, which must
        # tell the user why their request was refused.
        if not self.granted and not self.reason:
            raise ValueError("a denied reservation must state a reason")
        return self


class UsageEvent(StrictModel):
    """Emitted to Pub/Sub after a metered call completes.

    This is advisory/audit data. It is published asynchronously and MUST NOT be
    able to block or fail a user request.
    """

    event_id: str = Field(min_length=1, description="Idempotency key for the worker")
    user_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1, description="Correlates with the reservation")
    method: str = Field(min_length=1)
    path: str = Field(min_length=1)
    tokens_reserved: NonNegativeTokens
    tokens_consumed: NonNegativeTokens
    status_code: int = Field(ge=100, le=599)
    duration_ms: NonNegativeTokens
    occurred_at: datetime = Field(default_factory=_utcnow)
    error: str | None = None


class LedgerEntry(StrictModel):
    """One immutable, append-only ledger row."""

    entry_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    kind: LedgerKind
    delta_tokens: int = Field(
        description="Signed change: negative for reserve, positive for grant/refund/purchase"
    )
    balance_after: int = Field(ge=0)
    request_id: str | None = Field(
        default=None, description="Present for reserve/refund entries"
    )
    amount_minor_units: int | None = Field(
        default=None,
        description="Monetary amount in minor units (cents) for purchases; never a float",
    )
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    created_at: datetime = Field(default_factory=_utcnow)
    note: str | None = None
