"""Synchronous token reservation against a balance ledger.

Two implementations behind one protocol:

* :class:`MockTokenBalanceClient` -- in-process, deterministic, no network.
  Used for tests and ``PHYTO_DRY_RUN=1``.
* :class:`FirestoreTokenBalanceClient` -- the production store. Every
  state-changing operation runs inside a Firestore transaction, so two
  concurrent requests cannot both spend the last token.

Reservation is synchronous by design (ADR-0028): a request either has tokens
held for it before the endpoint runs, or it is refused. Settlement is separate
-- ``confirm`` keeps the hold, ``refund`` returns it.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

from phyto_shared.usage_events import ReservationRequest, ReservationResult

__all__ = [
    "MockTokenBalanceClient",
    "FirestoreTokenBalanceClient",
    "TokenBalanceClient",
    "get_token_balance_client",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


ReservationStatus = Literal["reserved", "confirmed", "refunded"]


@dataclass
class _Reservation:
    reservation_id: str
    user_id: str
    operation: str
    tokens_reserved: int
    idempotency_key: str
    status: ReservationStatus = "reserved"
    created_at: datetime = field(default_factory=_utcnow)


class TokenBalanceClient(Protocol):
    def reserve(self, request: ReservationRequest) -> ReservationResult: ...
    def confirm(self, reservation_id: str) -> bool: ...
    def refund(self, reservation_id: str, reason: str) -> bool: ...
    def get_balance(self, user_id: str) -> int: ...
    def topup(self, user_id: str, tokens: int, metadata: dict | None = None) -> int: ...


class MockTokenBalanceClient:
    """Deterministic in-process ledger.

    ``reserve`` is atomic (guarded by a lock) and idempotent on
    ``(user_id, idempotency_key)``. A retry with the same key returns the
    original result with status ``already_exists`` and does not decrement
    again. ``confirm`` and ``refund`` are each applied at most once because
    they only act on a reservation still in ``reserved`` state.
    """

    def __init__(self, initial_balances: dict[str, int] | None = None):
        self._lock = threading.Lock()
        self._balances: dict[str, int] = dict(initial_balances or {})
        self._reservations: dict[str, _Reservation] = {}
        self._by_key: dict[tuple[str, str], ReservationResult] = {}

    # -- reservation ------------------------------------------------------
    def reserve(self, request: ReservationRequest) -> ReservationResult:
        with self._lock:
            key = (request.user_id, request.idempotency_key)
            original = self._by_key.get(key)
            if original is not None:
                return original.model_copy(update={"status": "already_exists"})

            balance = self._balances.get(request.user_id, 0)
            if balance < request.tokens:
                return ReservationResult(
                    reservation_id=str(uuid.uuid4()),
                    user_id=request.user_id,
                    tokens_reserved=0,
                    balance_before=balance,
                    balance_after=balance,
                    status="insufficient_balance",
                    created_at=_utcnow(),
                )

            reservation_id = str(uuid.uuid4())
            after = balance - request.tokens
            self._balances[request.user_id] = after
            self._reservations[reservation_id] = _Reservation(
                reservation_id=reservation_id,
                user_id=request.user_id,
                operation=request.operation,
                tokens_reserved=request.tokens,
                idempotency_key=request.idempotency_key,
            )
            result = ReservationResult(
                reservation_id=reservation_id,
                user_id=request.user_id,
                tokens_reserved=request.tokens,
                balance_before=balance,
                balance_after=after,
                status="reserved",
                created_at=_utcnow(),
            )
            self._by_key[key] = result
            return result

    # -- settlement -------------------------------------------------------
    def confirm(self, reservation_id: str) -> bool:
        """Keep the hold. Idempotent: a second confirm returns False."""
        with self._lock:
            record = self._reservations.get(reservation_id)
            if record is None or record.status != "reserved":
                return False
            record.status = "confirmed"
            return True

    def refund(self, reservation_id: str, reason: str) -> bool:
        """Return held tokens. A second refund cannot restore twice."""
        with self._lock:
            record = self._reservations.get(reservation_id)
            if record is None or record.status != "reserved":
                return False
            self._balances[record.user_id] = (
                self._balances.get(record.user_id, 0) + record.tokens_reserved
            )
            record.status = "refunded"
            return True

    # -- balance ----------------------------------------------------------
    def get_balance(self, user_id: str) -> int:
        with self._lock:
            return self._balances.get(user_id, 0)

    def topup(self, user_id: str, tokens: int, metadata: dict | None = None) -> int:
        if tokens < 0:
            raise ValueError("topup tokens must be non-negative")
        with self._lock:
            balance = self._balances.get(user_id, 0) + tokens
            self._balances[user_id] = balance
            return balance


class FirestoreTokenBalanceClient:
    """Production ledger.

    Collections:
      ``users/{user_id}``                     -- ``token_balance`` (int)
      ``reservations/{reservation_id}``       -- hold state
      ``idempotency_keys/{user_id}:{key}``    -- original result for retries

    Every state-changing operation runs as a Firestore transaction. Reads are
    issued before writes inside each transaction, which Firestore requires.
    """

    def __init__(self, project_id: str, client=None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._firestore = firestore
        self._db = client if client is not None else firestore.Client(project=project_id)

    # -- transaction plumbing --------------------------------------------
    def _run(self, operation, *args):
        step = self._firestore.transactional(
            lambda transaction: operation(transaction, *args)
        )
        return step(self._db.transaction())

    # -- reservation ------------------------------------------------------
    def reserve(self, request: ReservationRequest) -> ReservationResult:
        return self._run(self._reserve, request)

    def _reserve(self, transaction, request: ReservationRequest) -> ReservationResult:
        idem_ref = self._db.collection("idempotency_keys").document(
            f"{request.user_id}:{request.idempotency_key}"
        )
        idem_snap = idem_ref.get(transaction=transaction)
        if idem_snap.exists:
            stored = (idem_snap.to_dict() or {}).get("result") or {}
            return ReservationResult(
                reservation_id=stored.get("reservation_id", ""),
                user_id=request.user_id,
                tokens_reserved=int(stored.get("tokens_reserved", 0)),
                balance_before=int(stored.get("balance_before", 0)),
                balance_after=int(stored.get("balance_after", 0)),
                status="already_exists",
                created_at=_utcnow(),
            )

        user_ref = self._db.collection("users").document(request.user_id)
        user_snap = user_ref.get(transaction=transaction)
        balance = (
            int((user_snap.to_dict() or {}).get("token_balance", 0))
            if user_snap.exists
            else 0
        )

        if balance < request.tokens:
            return ReservationResult(
                reservation_id=str(uuid.uuid4()),
                user_id=request.user_id,
                tokens_reserved=0,
                balance_before=balance,
                balance_after=balance,
                status="insufficient_balance",
                created_at=_utcnow(),
            )

        reservation_id = str(uuid.uuid4())
        after = balance - request.tokens
        result = ReservationResult(
            reservation_id=reservation_id,
            user_id=request.user_id,
            tokens_reserved=request.tokens,
            balance_before=balance,
            balance_after=after,
            status="reserved",
            created_at=_utcnow(),
        )
        transaction.set(
            self._db.collection("reservations").document(reservation_id),
            {
                "user_id": request.user_id,
                "operation": request.operation,
                "tokens_reserved": request.tokens,
                "idempotency_key": request.idempotency_key,
                "status": "reserved",
                "created_at": _utcnow(),
            },
        )
        transaction.set(user_ref, {"token_balance": after}, merge=True)
        transaction.set(
            idem_ref,
            {"result": result.model_dump(mode="json"), "created_at": _utcnow()},
        )
        return result

    # -- settlement -------------------------------------------------------
    def confirm(self, reservation_id: str) -> bool:
        return self._run(self._confirm, reservation_id)

    def _confirm(self, transaction, reservation_id: str) -> bool:
        ref = self._db.collection("reservations").document(reservation_id)
        snap = ref.get(transaction=transaction)
        if not snap.exists or (snap.to_dict() or {}).get("status") != "reserved":
            return False
        transaction.set(ref, {"status": "confirmed", "confirmed_at": _utcnow()}, merge=True)
        return True

    def refund(self, reservation_id: str, reason: str) -> bool:
        return self._run(self._refund, reservation_id, reason)

    def _refund(self, transaction, reservation_id: str, reason: str) -> bool:
        ref = self._db.collection("reservations").document(reservation_id)
        snap = ref.get(transaction=transaction)
        data = snap.to_dict() or {}
        if not snap.exists or data.get("status") != "reserved":
            return False
        user_id = data["user_id"]
        tokens = int(data["tokens_reserved"])
        user_ref = self._db.collection("users").document(user_id)
        user_snap = user_ref.get(transaction=transaction)
        balance = (
            int((user_snap.to_dict() or {}).get("token_balance", 0))
            if user_snap.exists
            else 0
        )
        transaction.set(user_ref, {"token_balance": balance + tokens}, merge=True)
        transaction.set(
            ref,
            {"status": "refunded", "refund_reason": reason, "refunded_at": _utcnow()},
            merge=True,
        )
        return True

    # -- balance ----------------------------------------------------------
    def get_balance(self, user_id: str) -> int:
        snap = self._db.collection("users").document(user_id).get()
        if not snap.exists:
            return 0
        return int((snap.to_dict() or {}).get("token_balance", 0))

    def topup(self, user_id: str, tokens: int, metadata: dict | None = None) -> int:
        if tokens < 0:
            raise ValueError("topup tokens must be non-negative")
        return self._run(self._topup, user_id, tokens, metadata or {})

    def _topup(self, transaction, user_id: str, tokens: int, metadata: dict) -> int:
        user_ref = self._db.collection("users").document(user_id)
        snap = user_ref.get(transaction=transaction)
        balance = (
            int((snap.to_dict() or {}).get("token_balance", 0)) if snap.exists else 0
        )
        after = balance + tokens
        transaction.set(
            user_ref,
            {"token_balance": after, "last_topup_metadata": metadata},
            merge=True,
        )
        return after


def get_token_balance_client(
    dry_run: bool, project_id: str
) -> TokenBalanceClient:
    if dry_run:
        return MockTokenBalanceClient()
    return FirestoreTokenBalanceClient(project_id)
