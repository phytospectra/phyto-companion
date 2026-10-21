"""Billing routes: plans, Stripe checkout, webhook, API keys (ADR-0047).

Prices live in Stripe, never in code.  This module exposes:

* ``GET /billing/plans`` -- public; plan FEATURES (not prices) so the
  frontend can render the pricing page; prices are fetched from Stripe.
* ``POST /billing/subscribe`` -- auth; returns a Stripe Checkout URL.
* ``POST /billing/webhook`` -- public (Stripe calls it, signature
  verified in production); idempotent per ``session.id``.
* ``GET/POST /billing/api-keys`` -- auth; pro and above (api_access).

The Stripe client is a protocol with a deterministic mock and a thin real
client.  Product/price creation is a MANUAL Stripe step (see
docs/STRIPE_SETUP.md); this code only creates checkout sessions from
prices that already exist there.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.plans import PLAN_FEATURES, plan_at_or_above

from .entitlements import EntitlementsClient

logger = logging.getLogger("phyto.billing")

# Mock-only MRR per plan so webhook events carry gross_revenue_usd without
# putting real prices in code.  The real webhook reads amount_total from
# the Stripe session.  Test/dry-run values; NOT the product's prices.
MOCK_PLAN_MRR_USD = {"pro": 15.0, "lab": 79.0, "department": 599.0}
SUBSCRIBABLE_PLANS = ("pro", "lab", "department")


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: str = Field(..., description="One of: pro, lab, department")


class SubscribeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_url: str
    session_id: str
    plan: str


class StripeClient(Protocol):
    def create_checkout_session(
        self, *, user_id: str, user_email: str | None, plan: str
    ) -> dict[str, Any]:
        """Returns {session_id, checkout_url, amount_usd, metadata}."""


class MockStripeClient:
    """Deterministic in-process Stripe (tests, dry-run)."""

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}

    def create_checkout_session(
        self, *, user_id: str, user_email: str | None, plan: str
    ) -> dict[str, Any]:
        session_id = f"cs_mock_{secrets.token_urlsafe(16)}"
        session = {
            "session_id": session_id,
            "checkout_url": f"https://checkout.stripe.com/c/pay/{session_id}",
            "amount_usd": MOCK_PLAN_MRR_USD.get(plan, 0.0),
            "metadata": {"user_id": user_id, "plan": plan},
        }
        self.sessions[session_id] = session
        return dict(session)


class RealStripeClient:
    """Thin Stripe Checkout client.

    Requires ``STRIPE_SECRET_KEY``; prices must already exist in Stripe
    (manual setup, docs/STRIPE_SETUP.md).  Uses stdlib urllib: Stripe's
    form API is simple enough, and the local plane must not grow
    dependencies for a cloud-gated feature.
    """

    def __init__(self, api_key: str | None = None, base_url: str = "https://api.stripe.com"):
        self._api_key = api_key or os.environ.get("STRIPE_SECRET_KEY", "")
        self._base = base_url.rstrip("/")

    def _price_for(self, plan: str) -> str:
        """The Stripe price id for a plan, from STRIPE_PRICE_<PLAN>."""
        price_id = os.environ.get(f"STRIPE_PRICE_{plan.upper()}")
        if not price_id:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"No Stripe price configured for plan {plan!r}; set "
                    f"STRIPE_PRICE_{plan.upper()} (see docs/STRIPE_SETUP.md)"
                ),
            )
        return price_id

    def create_checkout_session(
        self, *, user_id: str, user_email: str | None, plan: str
    ) -> dict[str, Any]:
        import json as _json
        import urllib.parse
        import urllib.request

        if not self._api_key:
            raise HTTPException(
                status_code=503,
                detail="Stripe is not configured; set STRIPE_SECRET_KEY",
            )
        base_url = os.environ.get("PHYTO_APP_BASE_URL", "https://app.phytospectra.com")
        form = {
            "mode": "subscription",
            "line_items[0][price]": self._price_for(plan),
            "line_items[0][quantity]": "1",
            "success_url": f"{base_url}/billing/success",
            "cancel_url": f"{base_url}/pricing",
            "client_reference_id": user_id,
            "metadata[user_id]": user_id,
            "metadata[plan]": plan,
        }
        if user_email:
            form["customer_email"] = user_email
        request = urllib.request.Request(
            f"{self._base}/v1/checkout/sessions",
            data=urllib.parse.urlencode(form).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            session = _json.loads(response.read())
        return {
            "session_id": session["id"],
            "checkout_url": session["url"],
            "amount_usd": 0.0,  # resolved by the webhook from the session
            "metadata": {"user_id": user_id, "plan": plan},
        }


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class WebhookEvent(BaseModel):
    """The subset of Stripe's event format the handler relies on."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    type: str
    data: dict = Field(default_factory=dict)


class WebhookProcessor:
    """Idempotent checkout.session.completed processing.

    Dedup key: ``session.id``.  Re-delivery (Stripe retries) returns the
    stored outcome instead of re-granting tokens.
    """

    def __init__(self, entitlements: EntitlementsClient | None, analytics_sink=None):
        self._entitlements = entitlements
        self._analytics = analytics_sink
        self._processed: dict[str, dict] = {}

    def handle(self, event: WebhookEvent) -> dict:
        if event.type != "checkout.session.completed":
            return {"status": "ignored", "type": event.type}
        session = (event.data or {}).get("object") or {}
        session_id = str(session.get("id") or "")
        if not session_id:
            return {"status": "malformed", "error": "missing session id"}
        if session_id in self._processed:
            return {"status": "ok", "duplicate": True, **self._processed[session_id]}

        metadata = session.get("metadata") or {}
        user_id = str(
            metadata.get("user_id") or session.get("client_reference_id") or ""
        )
        plan = str(metadata.get("plan") or "")
        if not user_id or not plan:
            return {
                "status": "malformed",
                "error": "session metadata must carry user_id and plan",
            }

        old_plan = "free"
        mrr_usd = float(session.get("amount_total") or 0) / 100.0
        if self._entitlements is not None:
            old_plan = self._entitlements.status(user_id).plan
            self._entitlements.set_plan(user_id, plan)

        outcome = {"user_id": user_id, "plan": plan, "duplicate": False}
        self._processed[session_id] = outcome

        if self._analytics is not None:
            from phyto_shared.analytics import emit_event

            emit_event(
                self._analytics,
                "plan_changed",
                user_id=user_id,
                properties={
                    "old_plan": old_plan,
                    "new_plan": plan,
                    "mrr_usd": mrr_usd,
                },
            )
        return {"status": "ok", **outcome}


# ---------------------------------------------------------------------------
# API keys (plan-gated: api_access)
# ---------------------------------------------------------------------------


class APIKeyStore:
    """Stores API key hashes (never the raw key after creation)."""

    def __init__(self) -> None:
        self._keys: dict[str, dict] = {}

    def create(self, user_id: str, label: str) -> dict:
        raw = f"pk_{secrets.token_urlsafe(24)}"
        record = {
            "key_hash": hashlib.sha256(raw.encode()).hexdigest(),
            "label": label,
            "user_id": user_id,
        }
        self._keys[record["key_hash"]] = record
        return {"api_key": raw, "label": label}

    def list_for(self, user_id: str) -> list[dict]:
        return [
            {"label": record["label"]}
            for record in self._keys.values()
            if record["user_id"] == user_id
        ]


def require_plan(request: Request, minimum: str) -> None:
    """Feature gate by plan rank (ADR-0047).  The plan comes from the
    middleware's entitlements lookup (server-side, from the user document)
    or, when entitlements are not wired, from the token's claims."""
    plan = getattr(request.state, "plan", None)
    if plan is None and getattr(request.state, "user", None) is not None:
        plan = request.state.user.claims.get("plan")
    if not plan_at_or_above(plan, minimum):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This feature requires the {minimum} plan or above "
                f"(current plan: {plan or 'free'})."
            ),
        )


def install_billing_routes(
    app,
    *,
    stripe: StripeClient,
    webhook: WebhookProcessor,
    api_keys: APIKeyStore,
) -> None:
    """Attach the billing routes to the auth service app."""

    @app.get("/billing/plans")
    def plans() -> dict:
        """Public plan features (no prices -- those live in Stripe)."""
        return {
            "plans": [
                {"name": name, "features": features._asdict()}
                for name, features in PLAN_FEATURES.items()
            ]
        }

    @app.post("/billing/subscribe", response_model=SubscribeResponse)
    def subscribe(request: SubscribeRequest, http_request: Request) -> SubscribeResponse:
        user = getattr(http_request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        if request.plan not in SUBSCRIBABLE_PLANS:
            raise HTTPException(
                status_code=422,
                detail=f"plan must be one of {SUBSCRIBABLE_PLANS}",
            )
        session = stripe.create_checkout_session(
            user_id=user.uid, user_email=user.email, plan=request.plan
        )
        return SubscribeResponse(
            checkout_url=session["checkout_url"],
            session_id=session["session_id"],
            plan=request.plan,
        )

    @app.post("/billing/webhook")
    def stripe_webhook(event: WebhookEvent) -> dict:
        result = webhook.handle(event)
        if result.get("status") == "malformed":
            # Acknowledge with 200: Stripe retries 5xx forever, and a
            # malformed session will never become well-formed.
            logger.warning("malformed Stripe webhook: %s", result.get("error"))
        return result

    @app.post("/billing/api-keys")
    def create_api_key(http_request: Request, label: str = "default") -> dict:
        user = getattr(http_request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        require_plan(http_request, "pro")
        return api_keys.create(user.uid, label)

    @app.get("/billing/api-keys")
    def list_api_keys(http_request: Request) -> dict:
        user = getattr(http_request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        require_plan(http_request, "pro")
        return {"keys": api_keys.list_for(user.uid)}
