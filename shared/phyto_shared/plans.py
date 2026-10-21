"""Plan metadata: what each subscription tier includes (ADR-0047).

PRICES DO NOT LIVE HERE.  Prices live in Stripe; this file describes
features per plan so the middleware, the LLM tier router and the workspace
caps share one source of truth.  Moving a number here would let code and
Stripe disagree about what a user bought.

``monthly_tokens`` is the per-user monthly allowance enforced by the auth
middleware (ADR-0045) and mirrored into ``MODEL_TIERS`` routing by the LLM
service (ADR-0046): ``distilled_llm`` selects the model tier for the plan.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, NamedTuple

__all__ = [
    "PlanName",
    "PLAN_FEATURES",
    "PlanFeatures",
    "plan_features",
    "PLAN_RANK",
    "plan_at_or_above",
    "WORKSPACE_CAPS",
    "workspace_caps",
]


PlanName = str
"""One of the keys of ``PLAN_FEATURES``.  A plain string (not Literal) so
unknown plans from a future database row fail loudly at ``plan_features``
instead of failing Pydantic validation deep inside a request handler."""


class PlanFeatures(NamedTuple):
    monthly_tokens: int
    seats: int
    priority: bool
    distilled_llm: bool
    api_access: bool
    sso: bool = False
    audit_export: bool = False
    custom: bool = False
    """``enterprise`` only: every limit is negotiated per contract."""


PLAN_FEATURES: Mapping[str, PlanFeatures] = MappingProxyType(
    {
        "free": PlanFeatures(
            monthly_tokens=50, seats=1, priority=False,
            distilled_llm=True, api_access=False,
        ),
        "pro": PlanFeatures(
            monthly_tokens=2000, seats=1, priority=False,
            distilled_llm=False, api_access=True,
        ),
        "lab": PlanFeatures(
            monthly_tokens=10_000, seats=10, priority=True,
            distilled_llm=False, api_access=True,
        ),
        "department": PlanFeatures(
            monthly_tokens=100_000, seats=200, priority=True,
            distilled_llm=False, api_access=True,
            sso=True, audit_export=True,
        ),
        # Enterprise is negotiated per contract (ADR-0047); the entry exists
        # so entitlement checks can rank it, but every concrete limit is TBD
        # until a contract defines it.
        "enterprise": PlanFeatures(
            monthly_tokens=0, seats=0, priority=True,
            distilled_llm=False, api_access=True,
            sso=True, audit_export=True, custom=True,
        ),
    }
)

# Free < pro < lab < department < enterprise.  Used by feature gates
# ("pro and above unlocks API access"); the order matches the pricing page.
PLAN_RANK: Mapping[str, int] = MappingProxyType(
    {name: rank for rank, name in enumerate(PLAN_FEATURES)}
)


def plan_features(plan: str | None) -> PlanFeatures:
    """Features for ``plan``; unknown plans raise (they must never silently
    downgrade to free, because free is the most restricted tier)."""
    try:
        return PLAN_FEATURES[plan or "free"]
    except KeyError:
        raise ValueError(
            f"unknown plan {plan!r}; known plans: {sorted(PLAN_FEATURES)}"
        ) from None


def plan_at_or_above(plan: str | None, minimum: str) -> bool:
    """True when ``plan`` ranks at or above ``minimum`` (e.g. API access)."""
    rank = PLAN_RANK.get(plan or "free")
    if rank is None:
        return False
    return rank >= PLAN_RANK[minimum]


class WorkspaceCaps(NamedTuple):
    seats: int
    monthly_pool_tokens: int


# Workspace caps by type (ADR-0047).  Classroom workspaces exist for the
# teaching loop; lab and department workspaces are the institutional tiers.
WORKSPACE_CAPS: Mapping[str, WorkspaceCaps] = MappingProxyType(
    {
        "classroom": WorkspaceCaps(seats=30, monthly_pool_tokens=5_000),
        "lab": WorkspaceCaps(seats=10, monthly_pool_tokens=10_000),
        "department": WorkspaceCaps(seats=200, monthly_pool_tokens=100_000),
    }
)


def workspace_caps(workspace_type: str) -> WorkspaceCaps:
    try:
        return WORKSPACE_CAPS[workspace_type]
    except KeyError:
        raise ValueError(
            f"unknown workspace type {workspace_type!r}; "
            f"known types: {sorted(WORKSPACE_CAPS)}"
        ) from None
