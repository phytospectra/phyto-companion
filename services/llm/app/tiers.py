"""Model-tier routing for the LLM service (ADR-0046).

Free-tier cost is the dominant term in the margin equation, so free users
are routed to a distilled model; paid tiers keep the full model.  The
grounding gate applies IDENTICALLY to both -- distillation must not lower
quality below the honesty threshold, so a distilled output that fails the
citation contract is rejected exactly like a full-model output.

If the distilled model is unavailable, free users fall back to the full
model and the fallback is logged as a COST ANOMALY: it silently reopens
the exact cost leak the tier exists to close, so it must be visible.
"""

from __future__ import annotations

import logging
import os

from .clients import LLMClient, MockLLMClient

__all__ = [
    "MODEL_TIERS",
    "DISTILLED_MODEL_ID",
    "FULL_MODEL_ID",
    "DistilledLLMClient",
    "select_model_tier",
    "route_client_for_plan",
]

logger = logging.getLogger("phyto.llm.tiers")

# Model ids per tier.  Cloud deployments override these with the hosted
# endpoint ids (env); the defaults name the mock/local plane equivalents so
# dry-run responses are honest about which tier served them.
DISTILLED_MODEL_ID = os.environ.get(
    "PHYTO_DISTILLED_MODEL", "qwen2.5-1.5b-instruct-distilled"
)
FULL_MODEL_ID = os.environ.get("PHYTO_FULL_MODEL", "qwen2.5-7b-instruct-qlora")

MODEL_TIERS: dict[str, str] = {
    "free": DISTILLED_MODEL_ID,
    "pro": FULL_MODEL_ID,
    "lab": FULL_MODEL_ID,
    "department": FULL_MODEL_ID,
    "enterprise": FULL_MODEL_ID,
}


class DistilledLLMClient(MockLLMClient):
    """The free tier's distilled model (deterministic local stand-in).

    Shares the mock's grounded renderer ON PURPOSE: the local plane cannot
    honestly simulate a worse model, and the contract under test is the
    ROUTING and the GATE, not the distilled weights.  A test that needs a
    low-quality distilled output injects a stub; the gate must reject it
    identically (that is the point of ADR-0046).
    """

    model_id = DISTILLED_MODEL_ID
    model_version = "distilled-1.0.0"

    @property
    def available(self) -> bool:
        """Ops kill-switch for the distilled endpoint (e.g. bad deploy)."""
        return os.environ.get("PHYTO_DISTILLED_DISABLED", "0") != "1"


def select_model_tier(plan: str | None) -> str:
    """'distilled' for plans whose features say so, else 'full'."""
    from phyto_shared.plans import plan_features

    features = plan_features(plan)
    return "distilled" if features.distilled_llm else "full"


def route_client_for_plan(
    base_client: LLMClient, plan: str | None
) -> tuple[LLMClient, str]:
    """Return ``(client, tier)`` for the user's plan.

    ``base_client`` is the provider-resolved client (vertex/openrouter/
    local).  Free-plan requests are swapped onto the distilled tier; if
    the distilled model is unavailable the request falls back to the full
    model and the cost anomaly is logged.
    """
    tier = select_model_tier(plan)
    if tier != "distilled":
        return base_client, "full"

    distilled = DistilledLLMClient()
    if not distilled.available:
        logger.warning(
            "COST ANOMALY: distilled model unavailable; serving free-tier "
            "request with the full model (%s)", FULL_MODEL_ID,
        )
        return base_client, "full"
    return distilled, "distilled"
