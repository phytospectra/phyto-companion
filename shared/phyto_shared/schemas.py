"""API I/O schemas for the PhytoCompanion services.

These mirror the domain models (``models.py``) but stay thin: they exist
to define the wire contracts between services and the frontend.  Every
response schema carries ``provenance_manifest_id`` so any answer can be
traced back to its provenance record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal  # noqa: F401  (Literal used below)

from pydantic import Field, model_validator

from .constants import ConfidenceVerdict
from .models import StrictModel

__all__ = [
    "PHARMACOLOGY_DISCLAIMER",
    "SpectrumUploadRequest",
    "SpectrumUploadResponse",
    "AnalysisRequest",
    "AnalysisResponse",
    "NetworkRequest",
    "NetworkResponse",
    "ElucidationRequest",
    "ElucidationResponse",
    "ElucidationCandidateSummary",
    "PharmacologyRequest",
    "PharmacologyResponse",
    "ErrorResponse",
]

PHARMACOLOGY_DISCLAIMER = (
    "Mechanistic interpretations produced by this service are hypotheses to "
    "guide experimental validation. They are not conclusions and must not "
    "be reported as established mechanisms."
)


# ---------------------------------------------------------------------------
# Spectrum upload
# ---------------------------------------------------------------------------


class SpectrumUploadRequest(StrictModel):
    """One raw file plus its acquisition metadata, base64-encoded."""

    filename: str = Field(..., min_length=1)
    format: Literal[
        "bruker", "varian", "jeol", "mzml", "mzxml", "mgf", "jcamp_dx", "csv"
    ]
    content_b64: str = Field(..., min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class SpectrumUploadResponse(StrictModel):
    spectrum_id: str
    provenance_manifest_id: str
    warnings: list[str] = Field(default_factory=list)
    received_at: datetime


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------


class AnalysisRequest(StrictModel):
    sample_id: str = Field(..., min_length=1)
    workflow: Literal[
        "single_compound",
        "compare_extracts",
        "novelty_scan",
        "build_network",
        "mechanism_hypothesis",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = Field(
        default=False,
        description="Validate the workflow graph without executing cloud calls",
    )


class AnalysisResponse(StrictModel):
    run_id: str
    status: Literal["queued", "running", "complete", "failed", "awaiting_review"]
    provenance_manifest_id: str
    summary: dict[str, Any] | None = None
    results_ref: str | None = Field(
        default=None, description="URI of the full results artifact"
    )


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------


class NetworkRequest(StrictModel):
    sample_id: str = Field(..., min_length=1)
    mode: Literal[
        "classical", "fbmn", "iimn", "hsqc", "fused", "bioactivity", "building_blocks"
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)


class NetworkResponse(StrictModel):
    network_id: str
    n_nodes: int = Field(..., ge=0)
    n_edges: int = Field(..., ge=0)
    modality_overlap_stats: dict[str, float] = Field(default_factory=dict)
    graphml_ref: str | None = None
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Elucidation
# ---------------------------------------------------------------------------


class ElucidationRequest(StrictModel):
    sample_id: str = Field(..., min_length=1)
    constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="formula, mz, adduct, solvent, and peak-list references",
    )
    max_candidates: int = Field(default=50, ge=1, le=500)


class ElucidationCandidateSummary(StrictModel):
    """One ranked structure as returned over the API."""

    rank: int = Field(..., ge=1)
    name: str
    smiles: str
    score: float
    ranking_convention: Literal["higher_is_better", "lower_is_better"]
    verdict: ConfidenceVerdict
    coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    margin: float | None = None
    ranking_interval: tuple[int, int] | None = None
    tied_candidate_count: int = Field(default=1, ge=1)


class ElucidationResponse(StrictModel):
    run_id: str
    candidates: list[ElucidationCandidateSummary] = Field(default_factory=list)
    evidence_graph_refs: list[str] = Field(default_factory=list)
    requires_human_review: bool = Field(
        default=True,
        description="True whenever any verdict above CANDIDATE is present",
    )
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Pharmacology
# ---------------------------------------------------------------------------


class PharmacologyRequest(StrictModel):
    sample_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    smiles: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _at_least_one_identifier(self) -> "PharmacologyRequest":
        if not self.sample_id and not self.candidate_ids and not self.smiles:
            raise ValueError(
                "provide at least one of: sample_id, candidate_ids, smiles"
            )
        return self


class PharmacologyResponse(StrictModel):
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    disclaimer: str = PHARMACOLOGY_DISCLAIMER
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorResponse(StrictModel):
    status: int = Field(..., ge=100, le=599)
    code: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)
