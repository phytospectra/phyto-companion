"""FastAPI entrypoint for the auth service.

This service owns the primitives (Firebase verification, reservation client,
usage publisher, middleware); every other service imports them to install
``AuthMiddleware``. It also owns the user-facing account surface:

* ``GET /billing/balance`` -- balance plus the monthly allowance state
  (ADR-0045) so the frontend can show exactly what is left and when it
  resets.
* ``POST /auth/signup`` -- records the ``user_signup`` cohort event
  (ADR-0048), idempotently per user.
* ``GET /sso/metadata`` -- department-and-above SSO configuration.
* Billing and workspace routes (installed from their modules).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from phyto_shared.analytics import emit_event
from phyto_shared.audit_chain import ChainedAuditLogger
from phyto_shared.plans import plan_at_or_above

from . import __version__
from .billing_routes import (
    APIKeyStore,
    MockStripeClient,
    RealStripeClient,
    StripeClient,
    WebhookProcessor,
    install_billing_routes,
)
from .config import AuthConfig
from .entitlements import (
    EntitlementsClient,
    MockEntitlementsClient,
)
from .firebase_auth import FirebaseAuthClient, MockFirebaseAuthClient, get_firebase_client
from .middleware import AuthMiddleware
from .pubsub_publisher import LoggingPublisher, UsagePublisher, get_publisher
from .token_balance import (
    MockTokenBalanceClient,
    TokenBalanceClient,
    get_token_balance_client,
)
from .workspace_routes import install_workspace_routes
from .workspaces import InMemoryWorkspaceStore, WorkspaceStore


def create_app(
    config: AuthConfig | None = None,
    *,
    firebase_client: FirebaseAuthClient | None = None,
    token_client: TokenBalanceClient | None = None,
    publisher: UsagePublisher | None = None,
    entitlements: EntitlementsClient | None = None,
    workspaces: WorkspaceStore | None = None,
    stripe: StripeClient | None = None,
    audit_path: str | None = None,
) -> FastAPI:
    cfg = config or AuthConfig.from_env()
    firebase = firebase_client or get_firebase_client(cfg)
    tokens = token_client or get_token_balance_client(cfg.dry_run, cfg.project_id)
    events = publisher or get_publisher(cfg)
    # Entitlements default: enforced in every mode that runs the middleware
    # (mock in dry-run, in-memory store in tests that pass their own).
    entitlements = entitlements or MockEntitlementsClient(ledger=tokens)
    workspace_store = workspaces or InMemoryWorkspaceStore()
    stripe_client = stripe or (
        MockStripeClient() if cfg.dry_run else RealStripeClient()
    )
    audit_log = ChainedAuditLogger(
        audit_path
        or os.environ.get("PHYTO_AUTH_AUDIT_LOG")
        or str(Path(tempfile.gettempdir()) / "phyto_companion_auth_audit.jsonl")
    )

    app = FastAPI(
        title="PhytoCompanion Auth Service",
        version=__version__,
        description="Firebase identity verification and synchronous token reservation.",
    )

    app.add_middleware(
        AuthMiddleware,
        firebase_client=firebase,
        token_client=tokens,
        publisher=events,
        enabled=cfg.enabled,
        entitlements=entitlements,
        workspaces=workspace_store,
        analytics_sink=audit_log,
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "auth",
            "version": __version__,
            "dry_run": cfg.dry_run,
            "token_layer": "active",
        }

    @app.get("/billing/balance")
    def balance(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        status = entitlements.status(user.uid)
        # The ledger owns the balance; the entitlements own the allowance.
        # Read both so the response can never disagree with either.
        balance_value = tokens.get_balance(user.uid)
        return {
            "user_id": user.uid,
            "balance": balance_value,
            "plan": status.plan,
            "monthly_allowance": status.monthly_allowance,
            "used_this_month": status.used_this_month,
            "resets_at": status.resets_at.isoformat(),
        }

    @app.post("/auth/signup")
    def record_signup(request: Request) -> dict[str, Any]:
        """Record the cohort-defining signup event (ADR-0048).

        Idempotent per user: the frontend calls this once after account
        creation, and a retry must not mint a second cohort member.
        """
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        already = any(
            record.get("event") == "user_signup"
            and record.get("user_id") == user.uid
            for record in audit_log.records()
        )
        if already:
            return {"recorded": False, "duplicate": True}
        emit_event(audit_log, "user_signup", user_id=user.uid)
        return {"recorded": True, "duplicate": False}

    @app.get("/sso/metadata")
    def sso_metadata(request: Request) -> dict[str, Any]:
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        plan = getattr(request.state, "plan", None) or user.claims.get("plan")
        if not plan_at_or_above(plan, "department"):
            raise HTTPException(
                status_code=403,
                detail=f"SSO requires the department plan (current: {plan or 'free'}).",
            )
        return {
            "sso_enabled": True,
            "protocol": "oidc",
            "authorization_endpoint": os.environ.get(
                "SSO_AUTHORIZATION_ENDPOINT", "https://sso.example.com/authorize"
            ),
            "domains": [d for d in os.environ.get("SSO_DOMAINS", "").split(",") if d],
        }

    install_billing_routes(
        app,
        stripe=stripe_client,
        webhook=WebhookProcessor(entitlements, analytics_sink=audit_log),
        api_keys=APIKeyStore(),
    )
    install_workspace_routes(app, workspaces=workspace_store, stripe=stripe_client)

    app.state.audit_log = audit_log
    app.state.entitlements = entitlements
    app.state.workspaces = workspace_store
    return app


app = create_app()


# Re-exported so sibling services and tests have one import site for mocks.
__all__ = [
    "app",
    "create_app",
    "AuthConfig",
    "AuthMiddleware",
    "FirebaseAuthClient",
    "MockFirebaseAuthClient",
    "TokenBalanceClient",
    "MockTokenBalanceClient",
    "EntitlementsClient",
    "MockEntitlementsClient",
    "WorkspaceStore",
    "InMemoryWorkspaceStore",
    "UsagePublisher",
    "LoggingPublisher",
]
