"""Reservation ledger semantics: atomicity, idempotency, refunds."""

from __future__ import annotations

import pytest

from auth.token_balance import MockTokenBalanceClient
from phyto_shared.usage_events import ReservationRequest

KEY = "idem-key-00000001"


def _request(tokens: int, key: str = KEY, user: str = "uid-1") -> ReservationRequest:
    return ReservationRequest(
        user_id=user, idempotency_key=key, operation="POST /elucidate", tokens=tokens
    )


@pytest.fixture()
def client() -> MockTokenBalanceClient:
    return MockTokenBalanceClient({"uid-1": 100})


def test_reserve_decrements_balance(client):
    result = client.reserve(_request(20))
    assert (result.status, result.tokens_reserved) == ("reserved", 20)
    assert (result.balance_before, result.balance_after) == (100, 80)
    assert client.get_balance("uid-1") == 80


def test_reserve_insufficient_returns_no_charge(client):
    result = client.reserve(_request(500))
    assert result.status == "insufficient_balance"
    assert result.tokens_reserved == 0
    assert result.balance_before == result.balance_after == 100
    assert client.get_balance("uid-1") == 100


def test_reserve_idempotent_returns_same(client):
    first = client.reserve(_request(20))
    second = client.reserve(_request(20))
    assert second.status == "already_exists"
    assert second.reservation_id == first.reservation_id
    assert second.balance_after == first.balance_after
    assert client.get_balance("uid-1") == 80  # charged once, not twice


def test_distinct_keys_reserve_separately(client):
    client.reserve(_request(20, key="idem-key-aaaaaaaa"))
    client.reserve(_request(20, key="idem-key-bbbbbbbb"))
    assert client.get_balance("uid-1") == 60


def test_confirm_is_idempotent(client):
    reservation = client.reserve(_request(20))
    assert client.confirm(reservation.reservation_id) is True
    assert client.confirm(reservation.reservation_id) is False
    assert client.get_balance("uid-1") == 80  # spent, never refunded by a second confirm


def test_refund_restores_balance(client):
    reservation = client.reserve(_request(20))
    assert client.refund(reservation.reservation_id, reason="HTTP 500") is True
    assert client.get_balance("uid-1") == 100


def test_double_refund_does_not_double_restore(client):
    reservation = client.reserve(_request(20))
    assert client.refund(reservation.reservation_id, reason="first") is True
    assert client.refund(reservation.reservation_id, reason="second") is False
    assert client.get_balance("uid-1") == 100


def test_confirm_after_refund_is_rejected(client):
    reservation = client.reserve(_request(20))
    client.refund(reservation.reservation_id, reason="failed")
    assert client.confirm(reservation.reservation_id) is False
    assert client.get_balance("uid-1") == 100


def test_topup_credits_and_returns_balance(client):
    assert client.topup("uid-1", 50) == 150
    assert client.get_balance("uid-1") == 150
    assert client.get_balance("stranger") == 0


def test_topup_rejects_negative(client):
    with pytest.raises(ValueError):
        client.topup("uid-1", -5)


def test_zero_token_reservation_is_free(client):
    result = client.reserve(_request(0))
    assert result.status == "reserved"
    assert result.balance_after == 100
