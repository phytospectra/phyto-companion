"""The guided first-story onboarding sequence (ADR-0045).

A new user's first session presents one complete value experience that
fits inside the 50-token free allowance: upload a sample, elucidate, view
the evidence, search NMR and literature, ask for an explanation, build a
network.  Every step is opt-in -- the tokens stay the user's if they skip
-- but the sequence exists so the UI can guide toward the value moment
(the evidence panel) instead of leaving 50 tokens to be spent on dead
ends.

Step costs are DERIVED from ``ROUTE_PRICING``, never hardcoded: the
spec's illustrative token numbers (37 total) predate the frozen base
table, and the table wins (changing it is explicitly out of scope).  What
must hold is the invariant the ADR actually states: the full story fits
within the free monthly allowance with buffer to retry.  A test asserts
that invariant against the live pricing table, so a future price change
that breaks it fails the build rather than quietly pricing the story out
of the free tier.
"""

from __future__ import annotations

from typing import Any

from phyto_shared.plans import plan_features
from phyto_shared.token_pricing import ROUTE_PRICING

__all__ = ["first_story_steps", "first_story_summary"]


def _cost_of(method: str, path: str) -> int:
    return ROUTE_PRICING[(method, path)].tokens


_TEMPLATE: list[dict[str, Any]] = [
    {
        "step": 1,
        "operation": "upload_sample",
        "label": "Upload a demo sample (or your own)",
        "method": "POST",
        "route": "/ingest/peak_csv",
        "explanation": (
            "Parse a peak list. The guided flow offers a demo fixture so "
            "you can see the whole story before uploading your own data."
        ),
    },
    {
        "step": 2,
        "operation": "run_elucidation",
        "label": "Run structure elucidation",
        "method": "POST",
        "route": "/analysis",
        "explanation": (
            "The orchestrated workflow plans, runs and verifies the "
            "elucidation tools for your sample."
        ),
    },
    {
        "step": 3,
        "operation": "view_evidence",
        "label": "View the evidence panel",
        "method": "GET",
        "route": "/runs/{run_id}",
        "tokens": 0,  # reads are free by design; the value moment must be
        "explanation": (
            "The value moment: every claim with the spectra behind it, "
            "gaps included. Reading evidence is always free."
        ),
    },
    {
        "step": 4,
        "operation": "run_nmr_search",
        "label": "Search reference NMR data",
        "method": "POST",
        "route": "/nmr_search/peaks",
        "explanation": "Match your peaks against reference spectra databases.",
    },
    {
        "step": 5,
        "operation": "run_literature_search",
        "label": "Search the literature",
        "method": "POST",
        "route": "/literature/search",
        "explanation": "Retrieve papers about your candidate structures.",
    },
    {
        "step": 6,
        "operation": "request_llm_explanation",
        "label": "Ask for an evidence-grounded explanation",
        "method": "POST",
        "route": "/llm/explain",
        "explanation": (
            "A grounded explanation of the evidence; every claim must cite "
            "the retrieved literature or it is rejected."
        ),
    },
    {
        "step": 7,
        "operation": "build_network",
        "label": "Build a molecular network",
        "method": "POST",
        "route": "/network/build",
        "explanation": "Place your compound among its structural neighbours.",
    },
    {
        "step": 8,
        "operation": "cite_and_share",
        "label": "Get the citation and share the run",
        "method": "GET",
        "route": "/runs/{run_id}/citation",
        "tokens": 0,  # citation retrieval is never metered (ADR-0043)
        "explanation": (
            "A citable identifier for your analysis, and a read-only link "
            "your colleagues can open without an account."
        ),
    },
]


def first_story_steps() -> list[dict[str, Any]]:
    """The guided sequence with each step's live token cost."""
    steps: list[dict[str, Any]] = []
    for template in _TEMPLATE:
        step = dict(template)
        if "tokens" not in step:
            step["tokens"] = _cost_of(step["method"], step["route"])
        steps.append(step)
    return steps


def first_story_summary() -> dict[str, Any]:
    """The sequence plus its budget arithmetic against the free allowance."""
    steps = first_story_steps()
    total = sum(int(step["tokens"]) for step in steps)
    allowance = plan_features("free").monthly_tokens
    return {
        "steps": steps,
        "total_tokens": total,
        "monthly_allowance": allowance,
        "retry_buffer": allowance - total,
        "fits_in_free_tier": total <= allowance,
        "note": (
            "Every step is opt-in; skipped tokens remain the user's. Costs "
            "are derived from ROUTE_PRICING so they stay true to the "
            "actual prices."
        ),
    }
