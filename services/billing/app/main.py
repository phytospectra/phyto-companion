"""FastAPI entrypoint for the billing worker.

PLACEHOLDER: no billing logic is implemented yet. This module exists so the
token-system integration has a stable import path and a deployable unit.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from . import __version__

app = FastAPI(
    title="PhytoCompanion Billing Service",
    version=__version__,
    description="Asynchronous usage audit and Stripe top-up (pending).",
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "billing",
        "version": __version__,
        "token_layer": "not_implemented",
    }
