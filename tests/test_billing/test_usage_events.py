"""Tests for the token usage domain models (ADR-0028)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from phyto_shared.usage_events import (
    LedgerEntry,
    ReservationRequest,
    ReservationResult,
    UsageEvent,
)


class TestReservationRequest:
    def test_valid_request(self):
        req = ReservationRequest(
            user_id="uid-1",
            request_id="req-1",
            method="POST",
            path="/elucidate",
            tokens=20,
        )
        assert req.tokens == 20

    def test_negative_tokens_rejected(self):
        with pytest.raises(ValidationError):
            ReservationRequest(
                user_id="uid-1", request_id="r", method="POST", path="/x", tokens=-1
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ReservationRequest(
                user_id="u", request_id="r", method="POST", path="/x", tokens=1, sneaky=True
            )

    def test_empty_user_id_rejected(self):
        with pytest.raises(ValidationError):
            ReservationRequest(
                user_id="", request_id="r", method="POST", path="/x", tokens=1
            )


class TestReservationResult:
    def test_granted_has_no_reason_requirement(self):
        result = ReservationResult(
            granted=True,
            request_id="r1",
            tokens_reserved=20,
            balance_after=80,
            plan="free",
        )
        assert result.granted and result.reason is None

    def test_denied_without_reason_is_rejected(self):
        # The UI must be able to explain a refusal, so silence is a bug.
        with pytest.raises(ValidationError):
            ReservationResult(
                granted=False,
                request_id="r2",
                tokens_reserved=0,
                balance_after=0,
                plan="free",
            )

    def test_denied_with_reason_is_accepted(self):
        result = ReservationResult(
            granted=False,
            request_id="r3",
            tokens_reserved=0,
            balance_after=5,
            plan="free",
            reason="insufficient_balance",
        )
        assert result.reason == "insufficient_balance"

    def test_negative_balance_rejected(self):
        with pytest.raises(ValidationError):
            ReservationResult(
                granted=True,
                request_id="r4",
                tokens_reserved=10,
                balance_after=-1,
                plan="free",
            )

    def test_invalid_plan_rejected(self):
        with pytest.raises(ValidationError):
            ReservationResult(
                granted=True,
                request_id="r5",
                tokens_reserved=1,
                balance_after=1,
                plan="enterprise",
            )


class TestUsageEvent:
    def test_valid_event_has_timestamp(self):
        event = UsageEvent(
            event_id="e1",
            user_id="uid-1",
            request_id="req-1",
            method="POST",
            path="/elucidate",
            tokens_reserved=20,
            tokens_consumed=20,
            status_code=200,
            duration_ms=1234,
        )
        assert isinstance(event.occurred_at, datetime)

    def test_status_code_bounds(self):
        with pytest.raises(ValidationError):
            UsageEvent(
                event_id="e2",
                user_id="u",
                request_id="r",
                method="POST",
                path="/x",
                tokens_reserved=1,
                tokens_consumed=1,
                status_code=999,
                duration_ms=1,
            )


class TestLedgerEntry:
    def test_purchase_requires_minor_units_not_float(self):
        entry = LedgerEntry(
            entry_id="l1",
            user_id="u",
            kind="purchase",
            delta_tokens=500,
            balance_after=500,
            amount_minor_units=1999,
            currency="USD",
        )
        assert entry.amount_minor_units == 1999
        assert isinstance(entry.amount_minor_units, int)

    def test_float_money_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntry(
                entry_id="l2",
                user_id="u",
                kind="purchase",
                delta_tokens=10,
                balance_after=10,
                amount_minor_units=19.99,
                currency="USD",
            )

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            LedgerEntry(
                entry_id="l3",
                user_id="u",
                kind="refunded",
                delta_tokens=1,
                balance_after=1,
            )

    def test_currency_must_be_three_letters(self):
        with pytest.raises(ValidationError):
            LedgerEntry(
                entry_id="l4",
                user_id="u",
                kind="purchase",
                delta_tokens=1,
                balance_after=1,
                amount_minor_units=100,
                currency="US",
            )
