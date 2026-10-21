"""Domain exception hierarchy for PhytoCompanion.

Seven exceptions, deliberately shallow.  Every exception carries a
``context`` dict so callers can attach the offending data (file names,
hashes, candidate ids, ...) without losing the original stack trace.

Standard library only.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PhytoCompanionError",
    "InvalidInputError",
    "InsufficientEvidenceError",
    "AmbiguousResultError",
    "ProvenanceError",
    "CloudServiceError",
    "ToolTierViolationError",
    "SandboxError",
]


class PhytoCompanionError(Exception):
    """Base class for all PhytoCompanion domain errors."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        # Copied so later mutation of the caller's dict cannot rewrite history.
        self.context: dict[str, Any] = dict(context) if context else {}


class InvalidInputError(PhytoCompanionError):
    """Bad user input: unparseable spectrum, invalid molecule, bad metadata."""


class InsufficientEvidenceError(PhytoCompanionError):
    """The available evidence cannot support the claim being attempted.

    Prefer this over silently weakening a claim (honesty over optimism).
    """


class AmbiguousResultError(PhytoCompanionError):
    """Multiple candidates are statistically tied and cannot be separated."""


class ProvenanceError(PhytoCompanionError):
    """Missing, mismatched, or tampered provenance (hash verification failed)."""


class CloudServiceError(PhytoCompanionError):
    """A cloud-plane dependency is unavailable or misbehaving.

    GNPS, Vertex AI, Cloud SQL, BigQuery, Secret Manager.  Raised by client
    wrappers so the local plane can fall back to mocks/failure reporting.
    """


class ToolTierViolationError(PhytoCompanionError):
    """A tool was used outside its tier contract.

    Typical cases: a Tier-3 (LLM) output consumed without QA/Critic
    verification, or a Tier-2 model invoked without confidence reporting.
    """


class SandboxError(PhytoCompanionError):
    """Sandboxed code execution failed or exceeded its resource limits."""
