"""Per-user entitlements: plan, monthly allowance, usage this period.

The monthly allowance (ADR-0045) is a cap on tokens SPENT per calendar
month, independent of the token balance: a free user is blocked when the
allowance is exhausted even with a positive balance, because free-tier
compute is the dominant cost term in the margin equation.

Reset semantics (precise, because "allowance resets monthly" is easy to
say and easy to get wrong):

* ``used_this_month`` resets to 0 at ``resets_at`` (first of next month).
* The allowance is re-granted into the balance by topping the balance UP
  TO the allowance, not by adding to it: unspent allowance never
  accumulates month over month, and purchased tokens are never revoked
  (``balance = max(balance, monthly_allowance)`` on rollover).
* A plan change sets ``monthly_allowance`` from ``PLAN_FEATURES`` and
  grants the new plan's allowance into the balance; a downgrade never
  reduces the balance (already-granted tokens stay granted).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

from phyto_shared.plans import PlanName, plan_features

__all__ = [
    "AllowanceExceeded",
    "UserTokenStatus",
    "EntitlementsClient",
    "MockEntitlementsClient",
    "FirestoreEntitlementsClient",
    "month_period",
    "next_month_first",
]


class AllowanceExceeded(Exception):
    """The monthly allowance cannot cover the requested tokens."""

    def __init__(self, status: "UserTokenStatus", requested: int):
        self.status = status
        self.requested = requested
        remaining = max(0, status.monthly_allowance - status.used_this_month)
        super().__init__(
            f"Monthly allowance for plan {status.plan!r} would be exceeded: "
            f"{status.used_this_month} used + {requested} requested > "
            f"{status.monthly_allowance}; {remaining} remaining until "
            f"{status.resets_at.isoformat()}"
        )


def month_period(moment: datetime) -> str:
    """``YYYY-MM`` for the month containing ``moment`` (UTC)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m")


def next_month_first(moment: datetime) -> datetime:
    """00:00 UTC on the first day of the month after ``moment``."""
    utc = moment.astimezone(timezone.utc)
    if utc.month == 12:
        return datetime(utc.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(utc.year, utc.month + 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class UserTokenStatus:
    """Everything the middleware and the balance endpoint need."""

    user_id: str
    plan: PlanName
    balance: int
    monthly_allowance: int
    used_this_month: int
    resets_at: datetime
    period: str


class EntitlementsClient(Protocol):
    def status(self, user_id: str) -> UserTokenStatus: ...
    def check_allowance(self, user_id: str, tokens: int) -> UserTokenStatus:
        """Current status; raises :class:`AllowanceExceeded` if the
        month's spend plus ``tokens`` would exceed the allowance."""
    def consume(self, user_id: str, tokens: int) -> None:
        """Record confirmed spend against this month's allowance."""
    def return_tokens(self, user_id: str, tokens: int) -> None:
        """Return refunded tokens to this month's allowance."""
    def set_plan(self, user_id: str, plan: PlanName) -> UserTokenStatus: ...
    def grant_topup(self, user_id: str, tokens: int) -> None:
        """Grant purchased tokens into the balance."""


@dataclass
class _UserState:
    plan: PlanName = "free"
    used_this_month: int = 0
    period: str = ""
    resets_at: datetime | None = None
    last_grant_period: str = ""


class MockEntitlementsClient:
    """In-process entitlements ledger (tests, dry-run, single instance).

    Tracks plan, period usage and rollover grants.  The balance lives in
    the token ledger; this client takes an optional ledger-like object
    with ``get_balance``/``topup`` so rollover grants reach the balance it
    caps.  Without one it still enforces the allowance cap (pure tracking).
    """

    def __init__(
        self,
        initial_plans: dict[str, PlanName] | None = None,
        ledger=None,
        now: datetime | None = None,
    ):
        self._lock = threading.Lock()
        self._users: dict[str, _UserState] = {}
        self._ledger = ledger
        self._fixed_now = now
        for uid, plan in (initial_plans or {}).items():
            self._users[uid] = _UserState(plan=plan)

    def _now(self) -> datetime:
        return self._fixed_now or datetime.now(timezone.utc)

    # -- internals ----------------------------------------------------------
    def _state(self, user_id: str) -> _UserState:
        state = self._users.get(user_id)
        if state is None:
            state = _UserState(plan="free")
            self._users[user_id] = state
        moment = self._now()
        period = month_period(moment)
        if state.period != period:
            state.period = period
            state.used_this_month = 0
            state.resets_at = next_month_first(moment)
        return state

    def _rollover_grant(self, user_id: str, state: _UserState) -> None:
        """Top the balance up to the allowance once per period (idempotent)."""
        if state.last_grant_period == state.period:
            return
        state.last_grant_period = state.period
        if self._ledger is None:
            return
        features = plan_features(state.plan)
        balance = self._ledger.get_balance(user_id)
        if balance < features.monthly_tokens:
            self._ledger.topup(
                user_id,
                features.monthly_tokens - balance,
                metadata={"kind": "monthly_allowance", "period": state.period},
            )

    # -- protocol -------------------------------------------------------------
    def status(self, user_id: str) -> UserTokenStatus:
        with self._lock:
            state = self._state(user_id)
            self._rollover_grant(user_id, state)
            features = plan_features(state.plan)
            balance = (
                self._ledger.get_balance(user_id) if self._ledger is not None else 0
            )
            return UserTokenStatus(
                user_id=user_id,
                plan=state.plan,
                balance=balance,
                monthly_allowance=features.monthly_tokens,
                used_this_month=state.used_this_month,
                resets_at=state.resets_at or next_month_first(self._now()),
                period=state.period,
            )

    def check_allowance(self, user_id: str, tokens: int) -> UserTokenStatus:
        current = self.status(user_id)
        if current.used_this_month + tokens > current.monthly_allowance:
            raise AllowanceExceeded(current, tokens)
        return current

    def consume(self, user_id: str, tokens: int) -> None:
        with self._lock:
            state = self._state(user_id)
            state.used_this_month += tokens

    def return_tokens(self, user_id: str, tokens: int) -> None:
        with self._lock:
            state = self._state(user_id)
            state.used_this_month = max(0, state.used_this_month - tokens)

    def set_plan(self, user_id: str, plan: PlanName) -> UserTokenStatus:
        plan_features(plan)  # raises ValueError for unknown plans
        with self._lock:
            state = self._state(user_id)
            state.plan = plan
            # Grant the new plan's allowance immediately; the next rollover
            # re-tops-up under the new plan.  A later downgrade never takes
            # granted tokens back.
            state.last_grant_period = ""
        current = self.status(user_id)
        if self._ledger is not None:
            features = plan_features(plan)
            if current.balance < features.monthly_tokens:
                self._ledger.topup(
                    user_id,
                    features.monthly_tokens - current.balance,
                    metadata={"kind": "plan_change_grant", "plan": plan},
                )
        return self.status(user_id)

    def grant_topup(self, user_id: str, tokens: int) -> None:
        """Purchased tokens: straight into the balance, never into the cap."""
        if self._ledger is not None:
            self._ledger.topup(user_id, tokens, metadata={"kind": "purchase"})


class FirestoreEntitlementsClient:
    """Production entitlements store.

    ``users/{user_id}`` fields: ``plan``, ``period``, ``used_this_month``,
    ``last_grant_period``.  Balance stays in the token ledger document;
    this client writes grants through a ``TokenBalanceClient``.
    """

    def __init__(self, ledger, project_id: str, client=None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._firestore = firestore
        self._db = client if client is not None else firestore.Client(project=project_id)
        self._ledger = ledger

    def _doc(self, user_id: str):
        return self._db.collection("users").document(user_id)

    def _load(self, user_id: str) -> dict:
        snap = self._doc(user_id).get()
        return dict(snap.to_dict() or {})

    def _apply_rollover(self, user_id: str, data: dict, moment: datetime) -> dict:
        period = month_period(moment)
        if data.get("period") != period:
            data["period"] = period
            data["used_this_month"] = 0
        if data.get("last_grant_period") != period:
            data["last_grant_period"] = period
            features = plan_features(data.get("plan", "free"))
            balance = self._ledger.get_balance(user_id)
            if balance < features.monthly_tokens:
                self._ledger.topup(
                    user_id,
                    features.monthly_tokens - balance,
                    metadata={"kind": "monthly_allowance", "period": period},
                )
        self._doc(user_id).set(data, merge=True)
        return data

    def status(self, user_id: str) -> UserTokenStatus:
        moment = datetime.now(timezone.utc)
        data = self._apply_rollover(user_id, self._load(user_id), moment)
        features = plan_features(data.get("plan", "free"))
        return UserTokenStatus(
            user_id=user_id,
            plan=data.get("plan", "free"),
            balance=self._ledger.get_balance(user_id),
            monthly_allowance=features.monthly_tokens,
            used_this_month=int(data.get("used_this_month", 0)),
            resets_at=next_month_first(moment),
            period=data.get("period", month_period(moment)),
        )

    def check_allowance(self, user_id: str, tokens: int) -> UserTokenStatus:
        current = self.status(user_id)
        if current.used_this_month + tokens > current.monthly_allowance:
            raise AllowanceExceeded(current, tokens)
        return current

    def consume(self, user_id: str, tokens: int) -> None:
        data = self._load(user_id)
        data["used_this_month"] = int(data.get("used_this_month", 0)) + tokens
        self._doc(user_id).set(data, merge=True)

    def return_tokens(self, user_id: str, tokens: int) -> None:
        data = self._load(user_id)
        data["used_this_month"] = max(0, int(data.get("used_this_month", 0)) - tokens)
        self._doc(user_id).set(data, merge=True)

    def set_plan(self, user_id: str, plan: PlanName) -> UserTokenStatus:
        if not plan_features(plan):
            raise ValueError(f"unknown plan {plan!r}")
        data = self._load(user_id)
        data["plan"] = plan
        data["last_grant_period"] = ""  # force grant on next status()
        self._doc(user_id).set(data, merge=True)
        return self.status(user_id)

    def grant_topup(self, user_id: str, tokens: int) -> None:
        self._ledger.topup(user_id, tokens, metadata={"kind": "purchase"})
