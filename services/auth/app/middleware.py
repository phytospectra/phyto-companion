"""Token-metering authentication middleware.

Order of operations for every non-public request:

1. Verify ``Authorization: Bearer <token>`` -> 401 on failure.
2. Resolve the route price with :func:`phyto_shared.token_pricing.cost_for_route`.
   An unpriced route is a configuration error -> 503, never a silent free call.
3. If the price is above zero:
   a. When the user belongs to a workspace with a positive pool, draw from
      the pool first (ADR-0047); pool draws bypass the personal monthly
      allowance -- the pool is the workspace's budget, not the member's.
   b. Otherwise enforce the monthly allowance (ADR-0045: a request that
      would exceed the allowance is refused with 402 even when the balance
      could cover it), then reserve synchronously -> 402 when the balance
      is short.
4. Run the endpoint. A 2xx/3xx confirms the hold; anything else -- including a
   raised exception -- refunds it.  A response carrying ``X-Cache: HIT`` was
   served from the query cache and is refunded (0 tokens, ADR-0046).
5. Publish an audit ``UsageEvent``. Publishing is advisory: a publisher
   failure is logged and never fails the user's request (ADR-0028).

``request.state.user``, ``request.state.plan`` (when entitlements are
wired), ``request.state.workspace_id`` (pool draws) and
``request.state.reservation_id`` are attached for downstream handlers.

Errors are returned as ``JSONResponse`` rather than raised as
``HTTPException``: this middleware sits outside Starlette's
``ExceptionMiddleware``, so a raised exception here would become an opaque
500 instead of the intended status.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from phyto_shared.token_pricing import cost_for_route
from phyto_shared.usage_events import ReservationRequest, UsageEvent

from .entitlements import AllowanceExceeded, EntitlementsClient
from .firebase_auth import FirebaseAuthClient
from .pubsub_publisher import UsagePublisher
from .token_balance import TokenBalanceClient
from .workspaces import InsufficientPoolError, WorkspaceStore

logger = logging.getLogger("phyto.auth")

PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
        # Public commerce surfaces: the pricing page (ADR-0047) and the
        # Stripe webhook (Stripe signs the payload; verified by the handler).
        "/billing/plans",
        "/billing/webhook",
    }
)
PUBLIC_PREFIXES = (
    # Share URLs are unauthenticated by design (ADR-0044): the signed token
    # IS the credential, and requiring an account would kill the loop.
    "/share/",
)
TOPUP_URL = "/billing/topup"
IDEMPOTENCY_MIN_LENGTH = 16


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _payment_required(detail: str, *, balance: int, required: int) -> JSONResponse:
    """The 402 contract (ADR-0028): allowance- and balance-based refusals
    share one shape so clients need a single handling path."""
    return JSONResponse(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        content={
            "detail": detail,
            "balance": balance,
            "required": required,
            "topup_url": TOPUP_URL,
        },
    )


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        firebase_client: FirebaseAuthClient,
        token_client: TokenBalanceClient,
        publisher: UsagePublisher,
        enabled: bool = True,
        entitlements: EntitlementsClient | None = None,
        workspaces: WorkspaceStore | None = None,
        analytics_sink=None,
        replay_routes: frozenset[str] | None = None,
    ):
        super().__init__(app)
        self._firebase = firebase_client
        self._tokens = token_client
        self._publisher = publisher
        self._enabled = enabled
        # Optional collaborators so direct constructions (existing tests,
        # minimal deployments) keep working; install_auth always wires them.
        self._entitlements = entitlements
        self._workspaces = workspaces
        self._analytics_sink = analytics_sink
        # Routes that implement the durable replay contract themselves ("POST
        # /analysis"): when a consumed key arrives there, the middleware
        # passes the request through WITHOUT a reservation and the handler
        # resolves replay vs conflict from durable state. No other route may
        # execute a handler after a consumed key: that would run work nobody
        # was charged for.
        self._replay_routes = replay_routes or frozenset()

    async def dispatch(self, request: Request, call_next):
        if not self._enabled or self._is_public(request.url.path):
            return await call_next(request)

        user = self._authenticate(request)
        if isinstance(user, JSONResponse):
            return user
        request.state.user = user
        request.state.reservation_id = None
        request.state.idempotency_reservation_id = None
        request.state.workspace_id = None
        request.state.idempotency_replay = False

        entitlement_status = None
        if self._entitlements is not None:
            entitlement_status = self._entitlements.status(user.uid)
            request.state.plan = entitlement_status.plan

        price = cost_for_route(request.method, request.url.path)
        if price is None:
            # A configuration error, not a client error: serving it free would
            # bill nobody for work that costs real money.
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "detail": (
                        f"Route {request.method} {request.url.path} has no "
                        "configured token price; refusing to serve an unmetered "
                        "request."
                    )
                },
            )

        operation = f"{request.method} {request.url.path}"
        reservation_id: str | None = None
        reserved = 0
        workspace_id: str | None = None

        if price.tokens > 0:
            idempotency_key = request.headers.get("Idempotency-Key")
            if idempotency_key is None:
                idempotency_key = uuid.uuid4().hex
            elif len(idempotency_key) < IDEMPOTENCY_MIN_LENGTH:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "detail": (
                            "Idempotency-Key must be at least "
                            f"{IDEMPOTENCY_MIN_LENGTH} characters."
                        )
                    },
                )

            # Claim the client key atomically in the shared ledger before budget
            # selection. This prevents concurrent retries, including retries that
            # would otherwise switch between a workspace and personal balance.
            digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
            claim = self._tokens.reserve(ReservationRequest(
                user_id=user.uid, idempotency_key=f"claim:{digest}",
                operation=operation, tokens=0,
            ))
            if claim.status == "already_exists":
                if operation in self._replay_routes:
                    # Durable-replay passthrough: the handler owns the replay
                    # decision (replay the stored response, or a stable
                    # documented conflict) and is responsible for refusing
                    # anything else. Nothing is reserved here, so a replay can
                    # never reserve or charge a second time.
                    request.state.idempotency_replay = True
                    return await call_next(request)
                return JSONResponse(
                    status_code=status.HTTP_409_CONFLICT,
                    content={"detail": "Idempotency-Key already used; response replay is unavailable."},
                )
            if claim.status != "reserved":
                return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                    content={"detail": "Unable to claim Idempotency-Key."})
            request.state.idempotency_reservation_id = claim.reservation_id
            try:
                reservation = self._reserve_tokens(
                    user_uid=user.uid,
                    idempotency_key=f"charge:{digest}",
                    operation=operation,
                    tokens=price.tokens,
                )
            except Exception:
                self._tokens.refund(claim.reservation_id, "reservation_error")
                raise
            if isinstance(reservation, JSONResponse):
                self._tokens.refund(claim.reservation_id, "reservation_refused")
                return reservation
            reservation_id, reserved, workspace_id = reservation
            request.state.reservation_id = reservation_id
            request.state.workspace_id = workspace_id

        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:
            self._settle(
                reservation_id=reservation_id,
                reserved=reserved,
                user_id=user.uid,
                operation=operation,
                started=started,
                confirmed=False,
                error=repr(exc),
                workspace_id=workspace_id,
            )
            raise

        succeeded = 200 <= response.status_code < 400
        cache_hit = response.headers.get("X-Cache") == "HIT"
        # A handler-replayed idempotent response is free like a cache hit:
        # the original request already paid; the replayed copy must never
        # reserve or charge again.
        replay_hit = response.headers.get("X-Idempotency-Replay") == "true"
        confirmed = succeeded and not cache_hit and not replay_hit
        self._settle(
            reservation_id=reservation_id,
            reserved=reserved,
            user_id=user.uid,
            operation=operation,
            started=started,
            confirmed=confirmed,
            error=None if succeeded else f"HTTP {response.status_code}",
            workspace_id=workspace_id,
            cache_hit=cache_hit,
            replay_hit=replay_hit,
        )
        return response

    # -- helpers ---------------------------------------------------------
    def _authenticate(self, request: Request):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme != "Bearer" or not token:
            return _unauthorized(
                "Missing or malformed Authorization header; expected 'Bearer <token>'."
            )
        try:
            return self._firebase.verify_token(token)
        except Exception as exc:
            detail = getattr(exc, "detail", "Invalid or expired token.")
            status_code = getattr(exc, "status_code", status.HTTP_401_UNAUTHORIZED)
            if status_code != status.HTTP_401_UNAUTHORIZED:
                return JSONResponse(status_code=status_code, content={"detail": detail})
            return _unauthorized(str(detail))

    def _is_public(self, path: str) -> bool:
        return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)

    def _reserve_tokens(
        self, *, user_uid: str, idempotency_key: str, operation: str, tokens: int
    ):
        """Reserve ``tokens``; returns (reservation_id, tokens, workspace_id)
        or a JSONResponse refusal.  Pool first, then personal (ADR-0047)."""
        if self._workspaces is not None:
            pool = self._workspaces.pool_for_user(user_uid)
            if pool is not None:
                workspace_id, remaining = pool
                if remaining >= tokens:
                    try:
                        draw_id = self._workspaces.draw(
                            workspace_id, user_uid, tokens, operation
                        )
                        return draw_id, tokens, workspace_id
                    except InsufficientPoolError:
                        # Lost a race for the last pool tokens: fall through
                        # to the personal reservation below.
                        pass
                # Pool too small: fall through to personal, but reflect the
                # combined balance in a refusal (ADR-0047, 5.3).
                personal = self._personal_reservation(
                    user_uid=user_uid,
                    idempotency_key=idempotency_key,
                    operation=operation,
                    tokens=tokens,
                    extra_balance=remaining,
                )
                return personal
        return self._personal_reservation(
            user_uid=user_uid,
            idempotency_key=idempotency_key,
            operation=operation,
            tokens=tokens,
            extra_balance=0,
        )

    def _personal_reservation(
        self,
        *,
        user_uid: str,
        idempotency_key: str,
        operation: str,
        tokens: int,
        extra_balance: int,
    ):
        if self._entitlements is not None:
            try:
                self._entitlements.check_allowance(user_uid, tokens)
            except AllowanceExceeded as exc:
                # Same 402 shape as a balance refusal (ADR-0045): the
                # monthly allowance is the binding constraint here.
                return _payment_required(
                    f"Monthly allowance for plan {exc.status.plan!r} exceeded; "
                    f"resets {exc.status.resets_at.isoformat()}.",
                    balance=exc.status.monthly_allowance - exc.status.used_this_month,
                    required=tokens,
                )

        result = self._tokens.reserve(
            ReservationRequest(
                user_id=user_uid,
                idempotency_key=idempotency_key,
                operation=operation,
                tokens=tokens,
            )
        )
        if result.status == "insufficient_balance":
            return _payment_required(
                "Insufficient token balance.",
                balance=result.balance_before + extra_balance,
                required=tokens,
            )
        return result.reservation_id, result.tokens_reserved, None

    def _settle(
        self,
        *,
        reservation_id: str | None,
        reserved: int,
        user_id: str,
        operation: str,
        started: float,
        confirmed: bool,
        error: str | None,
        workspace_id: str | None = None,
        cache_hit: bool = False,
        replay_hit: bool = False,
    ) -> None:
        if reservation_id is None or reserved == 0:
            return

        if workspace_id is not None:
            # Workspace pool draw: settle against the pool ledger; personal
            # balance and allowance were never touched.
            if self._workspaces is not None:
                self._workspaces.settle_draw(
                    reservation_id, confirmed=confirmed, operation=operation
                )
        elif confirmed:
            self._tokens.confirm(reservation_id)
            if self._entitlements is not None:
                self._entitlements.consume(user_id, reserved)
        else:
            reason = (
                "served as idempotent replay" if replay_hit
                else "served from query cache" if cache_hit
                else (error or "endpoint failed")
            )
            self._tokens.refund(reservation_id, reason=reason)
            # The allowance is only INCREMENTED on confirm (via ``consume``);
            # ``check_allowance`` merely checks.  Refunding must therefore
            # not decrement it: doing so would let a user erase real usage
            # from earlier requests by making a failing or cache-served call.

        event = UsageEvent(
            event_id=str(uuid.uuid4()),
            reservation_id=reservation_id,
            user_id=user_id,
            operation=operation,
            tokens_consumed=reserved if confirmed else 0,
            status="confirmed" if confirmed else "refunded",
            occurred_at=datetime.now(timezone.utc),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=error,
            workspace_id=workspace_id,
            cache_hit=cache_hit,
        )
        try:
            self._publisher.publish(event)
        except Exception:  # advisory only: never fail the request for audit
            logger.exception("failed to publish usage event %s", event.event_id)

        if (
            confirmed
            and workspace_id is not None
            and operation == "POST /analysis"
            and self._analytics_sink is not None
        ):
            # The teaching-loop metric: activation is a completed analysis,
            # never a sign-in (ADR-0047).  Emitted where the draw settled.
            from phyto_shared.analytics import emit_event

            try:
                emit_event(
                    self._analytics_sink,
                    "workspace_member_activated",
                    user_id=user_id,
                    properties={"workspace_id": workspace_id},
                )
            except Exception:
                logger.exception("failed to record workspace activation event")
