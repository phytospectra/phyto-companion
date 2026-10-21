"""Grounding verification is implemented in ``phyto_shared.grounding``.

It is re-exported here so the service's existing imports keep working and so
the public surface of this module is unchanged.  The implementation moved to
the shared library because the orchestrator's QA/Critic agent must apply the
identical rules; two copies could drift, and a grounding gate that disagrees
with the service that produced the score is not a gate.
"""

from phyto_shared.grounding import (  # noqa: F401  (re-exported API)
    GROUNDING_MIN_SCORE,
    GroundingReport,
    compute_grounding,
    extract_citations,
    extract_claims,
)

__all__ = [
    "extract_citations",
    "extract_claims",
    "compute_grounding",
    "GroundingReport",
    "GROUNDING_MIN_SCORE",
]
