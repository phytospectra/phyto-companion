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

from .chromatography import AnchorStandard, LCMethod, ReferenceStandard
from .metabolomics import MetabolomicsInput
from .constants import ConfidenceVerdict
from .data_governance import (
    ABSStatus,
    DataTier,
    DEFAULT_ABS_STATUS,
    DEFAULT_DATA_TIER,
    SOURCE_DATASET_IDS,
)
from .models import StrictModel

__all__ = [
    "PHARMACOLOGY_DISCLAIMER",
    "SpectrumUploadRequest",
    "SpectrumUploadResponse",
    "SampleMetadata",
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
    """One raw file plus its acquisition metadata, base64-encoded.

    Governance (ADR-0056): every contribution declares its data tier, ABS
    status and source dataset.  All three default to the most restrictive
    setting -- a contribution is ``training_only`` with ``unknown`` ABS
    status until a curator with established provenance promotes it, so
    nothing becomes public or licensable by accident.
    """

    filename: str = Field(..., min_length=1)
    format: Literal[
        "bruker", "varian", "jeol", "mzml", "mzxml", "mgf", "jcamp_dx", "csv"
    ]
    content_b64: str = Field(..., min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    data_tier: DataTier = Field(
        default=DEFAULT_DATA_TIER,
        description="Intended disposition; 'training_only' until promoted",
    )
    abs_status: ABSStatus = Field(
        default=DEFAULT_ABS_STATUS,
        description="ABS status of the underlying collection",
    )
    source_dataset: str = Field(
        default="user_upload",
        description="One of phyto_shared.data_governance.SOURCE_DATASET_IDS",
    )

    @model_validator(mode="after")
    def _source_dataset_known(self) -> "SpectrumUploadRequest":
        if self.source_dataset not in SOURCE_DATASET_IDS:
            raise ValueError(
                f"unknown source_dataset {self.source_dataset!r}; must be one "
                f"of {SOURCE_DATASET_IDS}"
            )
        return self


class SpectrumUploadResponse(StrictModel):
    spectrum_id: str
    provenance_manifest_id: str
    warnings: list[str] = Field(default_factory=list)
    received_at: datetime


# ---------------------------------------------------------------------------
# Analysis orchestration
# ---------------------------------------------------------------------------


class SampleMetadata(StrictModel):
    """User-supplied biological provenance of one sample.

    Species/genus/family drive the taxonomy-aware reranker (ADR-0052):
    when at least one of them is present the identify workflow resolves
    the taxon and builds a compound prior; when none is, the reranker is
    skipped rather than run on a guessed organism.  The extraction
    solvent modulates the prior bonus softly -- an unknown solvent is a
    WARNING (the solvent filter is skipped), never an error: rejecting a
    sample because its solvent was phrased unusually would trade a
    heuristic for a hard failure.
    """

    species: str | None = Field(default=None, min_length=1)
    genus: str | None = Field(default=None, min_length=1)
    family: str | None = Field(default=None, min_length=1)
    extraction_solvent: str | None = Field(
        default=None,
        description="Primary extraction solvent; see "
        "phyto_shared.solvent_prior.KNOWN_SOLVENTS",
    )
    extract_id: str | None = Field(
        default=None,
        description="The lab's identifier for THIS extract. When an "
        "isolation record exists for it (isolation service, ADR-0055) "
        "the identify workflow loads it and reranks candidates against "
        "the bench evidence; otherwise that layer is skipped with a "
        "visible note.",
    )
    # --- chromatographic metadata (ADR-0061..ADR-0064) --------------------
    # All optional: a layer runs only when its metadata was supplied.  When
    # none of lc_method / reference_standards / anchor_series is present the
    # corresponding layers are SKIPPED and the report notes "layer skipped:
    # no metadata" -- a skipped layer is never silently folded into the
    # ranking.
    lc_method: LCMethod | None = Field(
        default=None,
        description="The LC method the sample was run under.  Required for "
        "RT prediction and RI conversion; RT prediction is only valid for "
        "the method used in training (ADR-0063).",
    )
    reference_standards: list[ReferenceStandard] | None = Field(
        default=None,
        description="Authentic standards run under the same method; enables "
        "Level-1 matching and the RT offset calibration (ADR-0061).",
    )
    anchor_series: list[AnchorStandard] | None = Field(
        default=None,
        description="Co-injected homologous anchor series (e.g. cocamide DEA); "
        "enables retention-index normalization (ADR-0064).  Off by default: "
        "requires the user to have co-injected anchors.",
    )
    observed_rt_min: float | None = Field(
        default=None,
        gt=0.0,
        le=240.0,
        description="Retention time of the unknown peak on the supplied LC "
        "method (PDA/MS detection).  Drives the RT/RI reranking and the "
        "pharmacopoeial marker check (ADR-0063, ADR-0066); absent when the "
        "run is NMR-only.",
    )
    observed_uv_spectrum: list[tuple[float, float]] | None = Field(
        default=None,
        description="PDA spectrum of the unknown peak, [(wavelength_nm, "
        "absorbance), ...]; drives the UV chromophore layer (ADR-0062) and "
        "the marker UV check (ADR-0066).",
    )
    # --- metabolomics dataset reference (ADR-0068..ADR-0072) ---------------
    metabolomics: MetabolomicsInput | None = Field(
        default=None,
        description="Reference to a metabolomics dataset (raw Bruker NMR "
        "directories or an MZmine-format feature-table CSV) plus the "
        "sample sheet. When absent the metabolomics evidence layers "
        "(PCA, supervised model, VIP, STOCSY) are SKIPPED with a visible "
        "reason -- never silently (ADR-0068).",
    )

    def has_taxon_hint(self) -> bool:
        """True when at least one taxonomic rank was supplied."""
        return bool(self.species or self.genus or self.family)

    def solvent_is_known(self) -> bool:
        """True when the solvent, if supplied, has a compatibility profile."""
        if self.extraction_solvent is None:
            return True
        from .solvent_prior import normalise_solvent

        try:
            normalise_solvent(self.extraction_solvent)
        except Exception:
            return False
        return True

    def solvent_warning(self) -> str | None:
        """The warning to surface for an unrecognised solvent, else None."""
        if self.extraction_solvent is None or self.solvent_is_known():
            return None
        from .solvent_prior import KNOWN_SOLVENTS

        return (
            f"unknown extraction solvent {self.extraction_solvent!r}: the "
            "solvent compatibility filter will be skipped (known solvents: "
            f"{', '.join(sorted(KNOWN_SOLVENTS))})"
        )


class AnalysisRequest(StrictModel):
    sample_id: str = Field(..., min_length=1)
    workflow: Literal[
        "single_compound",
        "compare_extracts",
        "novelty_scan",
        "build_network",
        "mechanism_hypothesis",
        "identify",
    ]
    parameters: dict[str, Any] = Field(default_factory=dict)
    modalities: list[str] = Field(
        default_factory=list,
        description="Observation modalities the user actually supplied "
        "(h_1d, c_1d, hsqc, ms1, ms2). Empty means unspecified: planning is "
        "then unconstrained and treats every workflow step as possible. "
        "When set, steps the supplied evidence cannot support are pruned and "
        "reported instead of being run (ADR-0050).",
    )
    sample_metadata: SampleMetadata | None = Field(
        default=None,
        description="Biological provenance of the sample (species/genus/"
        "family, extraction solvent). When a taxon hint is present the "
        "identify workflow resolves the taxonomy and reranks candidates "
        "against the taxon prior (ADR-0052); otherwise that step is "
        "skipped with a logged reason.",
    )
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
