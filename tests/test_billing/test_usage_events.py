"""Tests for the token metering domain models (ADR-0038).

The schemas are the contract between the auth middleware, the billing worker
and the ledger, so their field names and validation rules are pinned here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from phyto_shared.usage_events import (
    LedgerEntry,
    ReservationRequest,
    ReservationResult,
    UsageEvent,
)

NOW = datetime.now(timezone.utc)


def _request(**overrides) -> ReservationRequest:
    fields = dict(
        user_id="uid-1",
        idempotency_key="a" * 16,
        operation="POST /elucidate",
        tokens=20,
    )
    fields.update(overrides)
    return ReservationRequest(**fields)


class TestReservationRequest:
    def test_valid_request(self):
        assert _request().tokens == 20

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValidationError):
            _request(tokens=-1)

    def test_tokens_above_ceiling_rejected(self):
        # A single reservation should never exceed 100k tokens.
        with pytest.raises(ValidationError):
            _request(tokens=100_001)

    def test_short_idempotency_key_rejected(self):
        # 16 chars is the floor; shorter keys collide too easily.
        with pytest.raises(ValidationError):
            _request(idempotency_key="short")

    def test_long_idempotency_key_rejected(self):
        with pytest.raises(ValidationError):
            _request(idempotency_key="a" * 129)

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            _request(sneaky=True)

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValidationError):
            _request(user_id="")

    def test_metadata_defaults_to_empty_dict(self):
        assert _request().metadata == {}

    def test_models_are_frozen(self):
        req = _request()
        with pytest.raises(ValidationError):
            req.tokens = 99


class TestReservationResult:
    def _result(self, **overrides) -> ReservationResult:
        fields = dict(
            reservation_id="r1",
            user_id="uid-1",
            tokens_reserved=20,
            balance_before=100,
            balance_after=80,
            status="reserved",
            created_at=NOW,
        )
        fields.update(overrides)
        return ReservationResult(**fields)

    def test_valid_reservation(self):
        result = self._result()
        assert result.status == "reserved"
        assert result.balance_after == 80

    @pytest.mark.parametrize("status", ["reserved", "already_exists", "insufficient_balance"])
    def test_all_statuses_accepted(self, status):
        assert self._result(status=status).status == status

    def test_unknown_status_rejected(self):
        with pytest.raises(ValidationError):
            self._result(status="refunded")

    def test_models_are_frozen(self):
        with pytest.raises(ValidationError):
            self._result().balance_after = 0


class TestUsageEvent:
    def _event(self, **overrides) -> UsageEvent:
        fields = dict(
            event_id="e1",
            reservation_id="r1",
            user_id="uid-1",
            operation="POST /elucidate",
            tokens_consumed=20,
            status="confirmed",
            occurred_at=NOW,
        )
        fields.update(overrides)
        return UsageEvent(**fields)

    def test_valid_event(self):
        event = self._event()
        assert event.status == "confirmed"
        assert event.duration_ms is None
        assert event.error is None

    def test_refunded_event_carries_error(self):
        event = self._event(status="refunded", tokens_consumed=0, error="HTTP 500")
        assert event.error == "HTTP 500"

    def test_only_confirmed_or_refunded_allowed(self):
        with pytest.raises(ValidationError):
            self._event(status="pending")

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            self._event(status_code=200)


class TestLedgerEntry:
    def _entry(self, **overrides) -> LedgerEntry:
        fields = dict(
            entry_id="l1",
            user_id="uid-1",
            event_type="topup",
            tokens=500,
            balance_after=500,
            occurred_at=NOW,
        )
        fields.update(overrides)
        return LedgerEntry(**fields)

    def test_valid_entry(self):
        entry = self._entry()
        assert entry.event_type == "topup"
        assert entry.reservation_id is None
        assert entry.operation is None

    @pytest.mark.parametrize("event_type", ["reserve", "confirm", "refund", "topup"])
    def test_all_event_types_accepted(self, event_type):
        assert self._entry(event_type=event_type).event_type == event_type

    def test_invalid_event_type_rejected(self):
        with pytest.raises(ValidationError):
            self._entry(event_type="purchase")

    def test_models_are_frozen(self):
        with pytest.raises(ValidationError):
            self._entry().tokens = 0
