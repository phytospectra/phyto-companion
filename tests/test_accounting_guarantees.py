"""Deterministic accounting-guarantee boundaries and fault injection.

These tests pin what the request-key claim mechanism actually guarantees
(at-most-once handler admission, exactly-once accounting settlement) and the
crash/replay windows that remain. They are offline store semantics; the
Firestore path shares the design but is not executed here.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from auth.token_balance import MockTokenBalanceClient
from phyto_shared.usage_events import ReservationRequest


def _claim_and_charge(store, uid, key, tokens):
    """Mirror the middleware claim+charge sequence against the raw ledger."""
    claim = store.reserve(ReservationRequest(
        user_id=uid, idempotency_key=f"claim:{key}",
        operation="POST /elucidate", tokens=0,
    ))
    if claim.status != "reserved":
        return claim, None
    charge = store.reserve(ReservationRequest(
        user_id=uid, idempotency_key=f"charge:{key}",
        operation="POST /elucidate", tokens=tokens,
    ))
    return claim, charge


def test_duplicate_key_is_rejected_without_second_charge():
    store = MockTokenBalanceClient({"alice": 100})
    first, charge = _claim_and_charge(store, "alice", "operational-key-0001", 10)
    assert first.status == "reserved" and charge.status == "reserved"
    assert store.get_balance("alice") == 90
    duplicate, second_charge = _claim_and_charge(store, "alice", "operational-key-0001", 10)
    assert duplicate.status == "already_exists"
    assert second_charge is None  # claim refusal precedes any second charge
    assert store.get_balance("alice") == 90  # no second deduction


def test_same_key_different_content_is_content_blind():
    # Content is not bound to keys: reuse is rejected regardless of payload.
    store = MockTokenBalanceClient({"alice": 100})
    _claim_and_charge(store, "alice", "operational-key-0001", 10)
    duplicate, _ = _claim_and_charge(store, "alice", "operational-key-0001", 99)
    assert duplicate.status == "already_exists"
    assert store.get_balance("alice") == 90


def test_same_key_is_scoped_to_user():
    store = MockTokenBalanceClient({"alice": 100, "bob": 100})
    _claim_and_charge(store, "alice", "operational-key-0001", 10)
    claim, charge = _claim_and_charge(store, "bob", "operational-key-0001", 10)
    assert claim.status == "reserved" and charge.status == "reserved"
    assert store.get_balance("alice") == 90 and store.get_balance("bob") == 90


def test_parallel_same_key_admits_exactly_one_handler():
    store = MockTokenBalanceClient({"alice": 100})
    admitted = []
    barrier = threading.Barrier(2)

    def attempt():
        claim, charge = _claim_and_charge(store, "alice", "operational-parallel-01", 10)
        barrier.wait()
        if claim.status == "reserved":
            admitted.append(1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: attempt(), range(2)))
    assert len(admitted) == 1
    assert store.get_balance("alice") == 90


def test_settlement_is_exactly_once_each_way():
    store = MockTokenBalanceClient({"alice": 100})
    _, charge = _claim_and_charge(store, "alice", "operational-key-0001", 10)
    assert store.confirm(charge.reservation_id) is True
    assert store.confirm(charge.reservation_id) is False  # second confirm refused
    assert store.refund(charge.reservation_id, "late refund attempt") is False
    assert store.get_balance("alice") == 90


def test_fresh_process_loses_claims_and_allows_replay():
    # The mock ledger is process-local: a restart forgets keys entirely.
    first = MockTokenBalanceClient({"alice": 100})
    _claim_and_charge(first, "alice", "operational-key-0001", 10)
    restarted = MockTokenBalanceClient({"alice": 100})
    claim, charge = _claim_and_charge(restarted, "alice", "operational-key-0001", 10)
    assert claim.status == "reserved" and charge.status == "reserved"
    # The durable Firestore path persists claims; the mock window is a
    # documented test/dry-run boundary, not a production guarantee.


def test_claims_are_never_expired_or_cleared():
    store = MockTokenBalanceClient({"alice": 100})
    _, charge = _claim_and_charge(store, "alice", "operational-key-0001", 10)
    store.refund(charge.reservation_id, "endpoint failed")
    assert store.get_balance("alice") == 100  # refund restored balance
    duplicate, _ = _claim_and_charge(store, "alice", "operational-key-0001", 10)
    assert duplicate.status == "already_exists"  # key consumed forever
    # No expiry API exists; recovery is a NEW client key, never key clearing.
