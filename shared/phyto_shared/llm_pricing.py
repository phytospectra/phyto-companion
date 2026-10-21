"""Token prices for Tier-3 LLM operations and deterministic retrieval (ADR-0028).

Kept separate from ``token_pricing.py`` because LLM prices change with the
hosted model's cost curve, independently of the deterministic API routes.
``token_pricing.ROUTE_PRICING`` imports these values rather than duplicating
the numbers, so there is exactly one place to change a price.

The named constants below are the canonical values from the pricing
specification.  The mappings are built FROM them so a caller can import either
a semantic name (``LLM_HYPOTHESIZE_TOKENS``) or look one up by operation
(``LLM_OPERATION_PRICING["hypothesize"]``) and get the same number.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

__all__ = [
    "LITERATURE_SEARCH_TOKENS",
    "LITERATURE_COMPOUND_TOKENS",
    "NMR_SEARCH_PEAKS_TOKENS",
    "NMR_COMPOUND_SHIFTS_TOKENS",
    "LLM_EXPLAIN_TOKENS",
    "LLM_HYPOTHESIZE_TOKENS",
    "LLM_RECOMMEND_EXPERIMENTS_TOKENS",
    "LLM_SUMMARIZE_LITERATURE_TOKENS",
    "LLM_OPERATION_PRICING",
    "LITERATURE_ROUTE_PRICING",
    "NMR_ROUTE_PRICING",
    "cost_for_llm_operation",
]


# --- Literature retrieval (Tier 1, deterministic) -------------------------
LITERATURE_SEARCH_TOKENS = 2
LITERATURE_COMPOUND_TOKENS = 5

# --- NMR structure search (Tier 1, deterministic) -------------------------
NMR_SEARCH_PEAKS_TOKENS = 3
NMR_COMPOUND_SHIFTS_TOKENS = 2

# --- Tier-3 generation ----------------------------------------------------
LLM_EXPLAIN_TOKENS = 10
LLM_HYPOTHESIZE_TOKENS = 25
LLM_RECOMMEND_EXPERIMENTS_TOKENS = 15
LLM_SUMMARIZE_LITERATURE_TOKENS = 20


# Tokens charged per operation.  These reflect relative compute: a hypothesis
# is generated over multiple retrieved papers, an explanation over one graph.
LLM_OPERATION_PRICING: Mapping[str, int] = MappingProxyType(
    {
        "explain": LLM_EXPLAIN_TOKENS,
        "hypothesize": LLM_HYPOTHESIZE_TOKENS,
        "recommend_experiments": LLM_RECOMMEND_EXPERIMENTS_TOKENS,
        "summarize_literature": LLM_SUMMARIZE_LITERATURE_TOKENS,
    }
)

# Deterministic literature retrieval is far cheaper than generation.
LITERATURE_ROUTE_PRICING: Mapping[str, int] = MappingProxyType(
    {
        "search": LITERATURE_SEARCH_TOKENS,
        "compound": LITERATURE_COMPOUND_TOKENS,
    }
)

# Spectral database lookup: a peak search fans out to three databases, while a
# single-compound shift lookup hits one.
NMR_ROUTE_PRICING: Mapping[str, int] = MappingProxyType(
    {
        "peaks": NMR_SEARCH_PEAKS_TOKENS,
        "compound_shifts": NMR_COMPOUND_SHIFTS_TOKENS,
    }
)


def cost_for_llm_operation(operation: str) -> int | None:
    """Return the token cost for an LLM operation, or ``None`` if unknown.

    ``None`` rather than 0: an unpriced operation must not be served for free
    by accident.
    """
    return LLM_OPERATION_PRICING.get(operation.strip().lower())
