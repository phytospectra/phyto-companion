"""One-call installation of the token layer into a service app.

Every service's ``create_app()`` calls :func:`install_auth`. Keeping the wiring
in one place means middleware order, CORS policy and the health contract
cannot drift between services.

Client construction is gated on ``config.dry_run``/``config.enabled`` so that
importing a service locally never imports ``firebase-admin`` or
``google-cloud-firestore``. Real clients are only built when the service is
actually going to authenticate.

Middleware order: auth is added first and CORS second, which puts CORS
outermost so 401/402/503 responses still carry CORS headers.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import AuthConfig
from .entitlements import (
    EntitlementsClient,
    FirestoreEntitlementsClient,
    MockEntitlementsClient,
)
from .firebase_auth import (
    FirebaseAuthClient,
    MockFirebaseAuthClient,
    get_firebase_client,
)
from .middleware import AuthMiddleware
from .pubsub_publisher import LoggingPublisher, UsagePublisher, get_publisher
from .token_balance import (
    MockTokenBalanceClient,
    TokenBalanceClient,
    get_token_balance_client,
)
from .workspaces import FirestoreWorkspaceStore, InMemoryWorkspaceStore, WorkspaceStore

__all__ = [
    "TokenLayer",
    "build_token_layer",
    "cors_origins_from_env",
    "install_auth",
]


def cors_origins_from_env() -> list[str]:
    raw = os.getenv("PHYTO_CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class TokenLayer:
    """The collaborators the middleware needs, resolved together."""

    __slots__ = (
        "firebase",
        "tokens",
        "publisher",
        "entitlements",
        "workspaces",
        "analytics_sink",
    )

    def __init__(
        self,
        firebase: FirebaseAuthClient,
        tokens: TokenBalanceClient,
        publisher: UsagePublisher,
        entitlements: EntitlementsClient | None = None,
        workspaces: WorkspaceStore | None = None,
        analytics_sink=None,
    ):
        self.firebase = firebase
        self.tokens = tokens
        self.publisher = publisher
        self.entitlements = entitlements
        self.workspaces = workspaces
        self.analytics_sink = analytics_sink


def build_token_layer(config: AuthConfig) -> TokenLayer:
    if not config.enabled:
        # Enforcement off: the middleware short-circuits before consulting
        # any collaborator, so mocks keep the app importable without GCP.
        return TokenLayer(
            MockFirebaseAuthClient(),
            MockTokenBalanceClient(),
            LoggingPublisher(),
            entitlements=None,
            workspaces=None,
        )
    if config.dry_run:
        # Enforced but offline (tests, local plane): deterministic mocks,
        # with the allowance cap active so behaviour matches production.
        return TokenLayer(
            MockFirebaseAuthClient(),
            MockTokenBalanceClient(),
            LoggingPublisher(),
            entitlements=MockEntitlementsClient(),
            workspaces=InMemoryWorkspaceStore(),
        )
    tokens = get_token_balance_client(config.dry_run, config.project_id)
    return TokenLayer(
        get_firebase_client(config),
        tokens,
        get_publisher(config),
        entitlements=FirestoreEntitlementsClient(tokens, config.project_id),
        workspaces=FirestoreWorkspaceStore(config.project_id),
    )


def install_auth(
    app: FastAPI,
    config: AuthConfig | None = None,
    *,
    layer: TokenLayer | None = None,
) -> AuthConfig:
    """Install AuthMiddleware (and CORS) on ``app``; returns the config used."""
    cfg = config or AuthConfig.from_env()
    collaborators = layer or build_token_layer(cfg)

    app.add_middleware(
        AuthMiddleware,
        firebase_client=collaborators.firebase,
        token_client=collaborators.tokens,
        publisher=collaborators.publisher,
        enabled=cfg.enabled,
        entitlements=collaborators.entitlements,
        workspaces=collaborators.workspaces,
        analytics_sink=collaborators.analytics_sink,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_from_env(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["WWW-Authenticate", "X-Cache"],
    )
    app.state.auth_config = cfg
    app.state.auth_layer = collaborators
    if cfg.enabled:
        _warn_unpriced_routes(app)
    return cfg


def _warn_unpriced_routes(app: FastAPI) -> None:
    """Loudly list routes that enforcement will reject with 503.

    ``ROUTE_PRICING`` was written against the planned API surface and does not
    yet cover every implemented path. With enforcement on, an unpriced route
    returns 503 by design, so this must be visible at boot rather than
    discovered as a production outage.
    """
    from phyto_shared.token_pricing import cost_for_route

    unpriced = sorted(
        {
            f"{method} {route.path}"
            for route in app.routes
            if getattr(route, "methods", None)
            for method in route.methods
            if method not in {"HEAD", "OPTIONS"}
            and not route.path.startswith(("/docs", "/redoc", "/openapi"))
            and cost_for_route(method, route.path) is None
        }
    )
    if unpriced:
        logger.warning(
            "auth enforcement is ON but %d route(s) have no price and will "
            "return 503: %s",
            len(unpriced),
            ", ".join(unpriced),
        )


logger = logging.getLogger("phyto.auth")
