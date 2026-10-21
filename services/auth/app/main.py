"""FastAPI entrypoint for the auth service.

PLACEHOLDER: no authentication logic is implemented yet. This module exists so
the token-system integration has a stable import path and a deployable unit.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from . import __version__

app = FastAPI(
    title="PhytoCompanion Auth Service",
    version=__version__,
    description="Firebase identity verification and synchronous token reservation (pending).",
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "auth",
        "version": __version__,
        "token_layer": "not_implemented",
    }
