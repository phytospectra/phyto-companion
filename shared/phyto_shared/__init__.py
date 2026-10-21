"""PhytoCompanion shared library.

Import as ``from phyto_shared import models`` or pull specific names from
the re-exports below.  The package has one dependency: pydantic >= 2.
"""

from .constants import (
    ADDUCT_MASSES,
    CONFIDENCE_VERDICT_REQUIREMENTS,
    ELEMENT_MASSES,
    ELEMENT_VALENCES,
    MSI_LEVEL_REQUIREMENTS,
    NOVELTY_STATUS_DEFINITIONS,
    SOLVENT_REFERENCES,
    TOOL_TIER_REQUIREMENTS,
    ConfidenceVerdict,
    MSIConfidenceLevel,
    NoveltyStatus,
    ToolTier,
)
from .exceptions import (
    AmbiguousResultError,
    CloudServiceError,
    InsufficientEvidenceError,
    InvalidInputError,
    PhytoCompanionError,
    ProvenanceError,
    SandboxError,
    ToolTierViolationError,
)
from .models import (
    AnalysisRun,
    Adduct,
    Candidate,
    CandidateRank,
    EvidenceGraph,
    EvidenceItem,
    FusedNetwork,
    IRPeak,
    MolecularFormula,
    MSPeak,
    NetworkEdge,
    NetworkNode,
    Peak1D,
    Peak2D,
    ProvenanceManifest,
    Spectrum,
)
from .provenance import (
    AuditLog,
    ProvenanceBuilder,
    sha256_bytes,
    sha256_dict,
    sha256_file,
    verify_manifest,
)
from .token_pricing import ROUTE_PRICING, FREE_TIER_SIGNUP_TOKENS, RoutePrice, cost_for_route
from .usage_events import LedgerEntry, ReservationRequest, ReservationResult, UsageEvent
from .uncertainty import (
    EvidenceAssessment,
    coverage_confidence,
    is_unique_top1_higher_is_better,
    is_unique_top1_lower_is_better,
    margin_higher_is_better,
    margin_lower_is_better,
    rank_higher_is_better,
    rank_lower_is_better,
    verdict_from_evidence,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # constants
    "ADDUCT_MASSES",
    "CONFIDENCE_VERDICT_REQUIREMENTS",
    "ELEMENT_MASSES",
    "ELEMENT_VALENCES",
    "MSI_LEVEL_REQUIREMENTS",
    "NOVELTY_STATUS_DEFINITIONS",
    "SOLVENT_REFERENCES",
    "TOOL_TIER_REQUIREMENTS",
    "ConfidenceVerdict",
    "MSIConfidenceLevel",
    "NoveltyStatus",
    "ToolTier",
    # exceptions
    "AmbiguousResultError",
    "CloudServiceError",
    "InsufficientEvidenceError",
    "InvalidInputError",
    "PhytoCompanionError",
    "ProvenanceError",
    "SandboxError",
    "ToolTierViolationError",
    # models
    "AnalysisRun",
    "Adduct",
    "Candidate",
    "CandidateRank",
    "EvidenceGraph",
    "EvidenceItem",
    "FusedNetwork",
    "IRPeak",
    "MolecularFormula",
    "MSPeak",
    "NetworkEdge",
    "NetworkNode",
    "Peak1D",
    "Peak2D",
    "ProvenanceManifest",
    "Spectrum",
    # provenance
    "AuditLog",
    "ProvenanceBuilder",
    "sha256_bytes",
    "sha256_dict",
    "sha256_file",
    "verify_manifest",
    # uncertainty
    "EvidenceAssessment",
    "coverage_confidence",
    "is_unique_top1_higher_is_better",
    "is_unique_top1_lower_is_better",
    "margin_higher_is_better",
    "margin_lower_is_better",
    "rank_higher_is_better",
    "rank_lower_is_better",
    "verdict_from_evidence",
    # token system
    "FREE_TIER_SIGNUP_TOKENS",
    "ROUTE_PRICING",
    "RoutePrice",
    "cost_for_route",
    "LedgerEntry",
    "ReservationRequest",
    "ReservationResult",
    "UsageEvent",
]
