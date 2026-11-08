"""Tool registry: every capability the orchestrator can delegate to.

Each tool is declared as a ``ToolSpec`` carrying its tier, Pydantic I/O
contract, cost model, and infrastructure needs.  The registry is the
contract between the orchestrator and the tool implementations that land
in Steps 4-6 as separate cloud services; the specs here are the stubs.

Also provides ``MockToolExecutor``: a deterministic, in-process executor
for ``--dry-run`` and local tests.  Local plane only -- no network, no
GPU, no Docker.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phyto_shared.constants import ConfidenceVerdict, ToolTier
from phyto_shared.chromatography import (
    AnchorStandard,
    LCMethod,
    ReferenceStandard,
)
from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.isolation import IsolationRecord
from phyto_shared.literature_models import PaperHit
from phyto_shared.metabolomics import MetabolomicsInput
from phyto_shared.models import (
    Candidate,
    CandidateRank,
    EvidenceGraph,
    EvidenceItem,
    FusedNetwork,
    MolecularFormula,
    MSPeak,
    Peak1D,
    Peak2D,
)
from phyto_shared.nmr_search_models import (
    NMRSpectrumHit,
)
from phyto_shared.taxonomy_models import TaxonPrior, TaxonResolution

__all__ = [
    "ToolSpec",
    "ToolRegistry",
    "MockToolExecutor",
    "build_default_registry",
    "DEFAULT_REGISTRY",
    "TIER1_TOOL_NAMES",
    "TIER2_TOOL_NAMES",
    "TIER3_TOOL_NAMES",
]


# ---------------------------------------------------------------------------
# I/O contract stubs (the API for the Step 4-6 implementations)
# ---------------------------------------------------------------------------


class _Contract(BaseModel):
    """Base for tool I/O contracts: strict, like the domain models."""

    model_config = ConfigDict(extra="forbid")


# --- Tier 1 ---------------------------------------------------------------


class RDKitDescriptorsInput(_Contract):
    smiles: str = Field(..., min_length=1)


class RDKitDescriptorsOutput(_Contract):
    descriptors: dict[str, float]


class RDKitFormulaInput(_Contract):
    smiles: str = Field(..., min_length=1)


class RDKitFormulaOutput(_Contract):
    formula: MolecularFormula


class ExactMassInput(_Contract):
    formula: MolecularFormula


class ExactMassOutput(_Contract):
    exact_mass: float = Field(..., ge=0.0)


class TTestInput(_Contract):
    sample_a: list[float] = Field(..., min_length=2)
    sample_b: list[float] = Field(..., min_length=2)


class TTestOutput(_Contract):
    statistic: float
    p_value: float = Field(..., ge=0.0, le=1.0)


class AnovaInput(_Contract):
    groups: dict[str, list[float]] = Field(..., min_length=2)


class AnovaOutput(_Contract):
    statistic: float
    p_value: float = Field(..., ge=0.0, le=1.0)


class FDRCorrectionInput(_Contract):
    p_values: list[float] = Field(..., min_length=1)
    alpha: float = Field(default=0.05, gt=0.0, lt=1.0)


class FDRCorrectionOutput(_Contract):
    rejected: list[bool]
    adjusted_p_values: list[float]


class ParsePeakCSVInput(_Contract):
    path: str = Field(..., min_length=1)


class ParsePeakCSVOutput(_Contract):
    peaks: list[Peak1D]


class LiteratureSearchInput(_Contract):
    query: str = Field(..., min_length=2, max_length=500)
    max_results: int = Field(default=10, ge=1, le=100)


class LiteratureSearchOutput(_Contract):
    papers: list[PaperHit] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    source_counts: dict[str, int] = Field(default_factory=dict)


class NMRPeakSearchInput(_Contract):
    peaks: list[tuple[float, float]] = Field(..., min_length=1)
    h_tolerance_ppm: float = Field(default=0.05, gt=0.0, le=1.0)
    c_tolerance_ppm: float = Field(default=1.0, gt=0.0, le=20.0)
    max_results: int = Field(default=10, ge=1, le=100)


class NMRPeakSearchOutput(_Contract):
    hits: list[NMRSpectrumHit] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    database_counts: dict[str, int] = Field(default_factory=dict)
    query_peak_count: int = Field(ge=1)


class ReferenceHSQCInput(_Contract):
    """Observed HSQC and an independently established molecular formula."""

    formula: str = Field(..., min_length=1, max_length=100)
    peaks: list[Peak2D] = Field(..., min_length=1, max_length=2000)
    h_tolerance_ppm: float = Field(default=0.05, gt=0, le=1)
    c_tolerance_ppm: float = Field(default=0.5, gt=0, le=20)
    max_results: int = Field(default=10, ge=1, le=100)


class ReferenceHSQCOutput(_Contract):
    """Measured-reference hits; no hit means no usable reference evidence."""

    hits: list[dict[str, Any]] = Field(default_factory=list)
    query_peak_count: int = Field(ge=1)
    formula: str
    unavailable_references: list[str] = Field(default_factory=list)
    limitations: list[str]
    provenance: dict[str, Any]


# --- Tier 1: taxonomy and taxon prior (ADR-0052/0053/0054) ----------------


class ResolveSpeciesInput(_Contract):
    name: str = Field(..., min_length=1)


class ResolveSpeciesOutput(_Contract):
    resolution: TaxonResolution
    lineage_depth: int = Field(..., ge=0, description="lineage ranks resolved")


class TaxonPriorBuildInput(_Contract):
    species: str | None = None
    genus: str | None = None
    family: str | None = None

    @model_validator(mode="after")
    def _at_least_one_rank(self) -> "TaxonPriorBuildInput":
        if not (self.species or self.genus or self.family):
            raise ValueError("provide at least one of species, genus, family")
        return self


class TaxonPriorBuildOutput(_Contract):
    prior: TaxonPrior
    n_species: int = Field(..., ge=0)
    n_genus: int = Field(..., ge=0)
    n_family: int = Field(..., ge=0)


class SpeciesPaperSearchInput(_Contract):
    """PubMed (compound AND species) search (ADR-0052 literature grounding)."""

    compound_name: str = Field(..., min_length=1)
    species_name: str = Field(..., min_length=1)
    max_results: int = Field(default=5, ge=1, le=100)


class SpeciesPaperSearchOutput(_Contract):
    papers: list[PaperHit] = Field(default_factory=list)
    total_count: int = Field(ge=0)
    source_counts: dict[str, int] = Field(default_factory=dict)


class IsolationLoadInput(_Contract):
    """Load the bench isolation record for one extract (ADR-0055)."""

    extract_id: str = Field(..., min_length=1)


class IsolationLoadOutput(_Contract):
    record: IsolationRecord | None = Field(
        default=None,
        description="The user's isolation record for this extract, or None "
        "when none exists -- the identify workflow then notes 'isolation "
        "layer skipped: no record'",
    )

    def serialisable(self) -> dict:
        """JSON-clean output for the engine's task-output store (the
        record carries a datetime the raw dict dump cannot hash)."""
        return {"record": self.record.model_dump(mode="json") if self.record else None}


class IsolationRerankInput(_Contract):
    """One compound's bench evidence for the isolation reranker."""

    record: IsolationRecord | None = None
    compound_id: str | None = None


class IsolationRerankOutput(_Contract):
    ranks: list[CandidateRank] = Field(default_factory=list)
    applied: bool = False


# --- Tier 2 ---------------------------------------------------------------


class GNPSSubmitInput(_Contract):
    mgf_content: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)


class GNPSSubmitOutput(_Contract):
    job_id: str
    status_url: str | None = None


class GNPSFetchInput(_Contract):
    job_id: str = Field(..., min_length=1)


class GNPSFetchOutput(_Contract):
    network: FusedNetwork
    annotations: dict[str, str] = Field(default_factory=dict)


class SiriusFormulaInput(_Contract):
    peaks: list[MSPeak] = Field(..., min_length=1)


class SiriusFormulaOutput(_Contract):
    formula: MolecularFormula
    confidence: float = Field(..., ge=0.0, le=1.0)


class ShiftMPNNInput(_Contract):
    smiles: str = Field(..., min_length=1)
    solvent: str | None = None


class ShiftMPNNOutput(_Contract):
    h_shifts: list[float]
    c_shifts: list[float]
    multiplicities: list[str]


class HungarianMatchInput(_Contract):
    observed: list[Peak2D] = Field(..., min_length=1)
    predicted_h: list[float] = Field(..., min_length=1)
    predicted_c: list[float] = Field(..., min_length=1)
    forbid_ch_multiplicity_exchange: bool = True


class HungarianMatchOutput(_Contract):
    matches: list[tuple[int, int]]
    cost: float = Field(..., ge=0.0)


class NMRNetworkingInput(_Contract):
    spectra: dict[str, list[Peak2D]] = Field(..., min_length=1)


class NMRNetworkingOutput(_Contract):
    network: FusedNetwork


class RankCandidatesInput(_Contract):
    """Elucidation ranking input, with optional taxonomy-aware reranking.

    ``taxon_prior`` and ``apply_taxon_prior`` are the ADR-0052 wiring: the
    prior arrives from the build-taxon-prior task, and when the user
    supplied no species/family the planner never sets the flag, so the
    ranker returns the spectral ranking untouched (with the skip logged).
    """

    h_1d: list[float] | None = None
    c_1d: list[float] | None = None
    hsqc: list[tuple[float, float]] | None = None
    ms2: list[tuple[float, float]] | None = None
    formula: str = Field(..., min_length=1)
    solvent: str | None = None
    taxon_prior: TaxonPrior | None = None
    apply_taxon_prior: bool = False
    isolation_record: IsolationRecord | None = None
    isolation_compound_id: str | None = None


class RankCandidatesOutput(_Contract):
    ranks: list[CandidateRank] = Field(default_factory=list)
    top_candidate_name: str | None = None
    top_evidence_graph: EvidenceGraph | None = None
    taxon_prior_applied: bool = Field(
        default=False,
        description="False when the reranker did not run (no metadata, empty "
        "prior) -- the ranks are then the unmodified spectral ranking",
    )
    isolation_applied: bool = Field(
        default=False,
        description="False when the isolation reranker did not run (no "
        "record supplied) -- the report then notes 'isolation layer "
        "skipped: no record' (ADR-0055)",
    )


class ApplyChromatographyLayersInput(_Contract):
    """Chromatographic evidence layers over ranked candidates
    (ADR-0061..ADR-0066).  Each optional field is one layer's metadata;
    when it is absent that layer is reported as skipped, never silent."""

    ranks: list[CandidateRank] = Field(default_factory=list)
    species: str | None = None
    observed_rt_min: float | None = Field(default=None, gt=0)
    observed_uv_spectrum: list[tuple[float, float]] | None = None
    peak_purity_score: float | None = Field(default=None, ge=0, le=1)
    lc_method: LCMethod | None = None
    reference_standards: list[ReferenceStandard] | None = None
    anchor_series: list[AnchorStandard] | None = None


class ApplyChromatographyLayersOutput(_Contract):
    ranks: list[CandidateRank] = Field(default_factory=list)
    layers_applied: list[str] = Field(default_factory=list)
    skipped_layers: list[dict[str, str]] = Field(default_factory=list)
    top_candidate_name: str | None = None


class MetabolomicsPreprocessInput(_Contract):
    """Run the in-house preprocessing (nmr_preproc pipeline for raw
    Bruker directories, lcms_preproc adapter for an MZmine-format CSV)
    and serialise the result for ``metabolomics.apply_layers``."""

    metabolomics_input: MetabolomicsInput


class MetabolomicsPreprocessOutput(_Contract):
    preprocessing_result: dict[str, Any] | None = None
    skipped: bool = False
    reason: str | None = None


class ApplyMetabolomicsLayersInput(_Contract):
    """Metabolomics evidence layers over a preprocessed feature matrix
    (ADR-0068..ADR-0072).  ``preprocessing_result`` is the output of the
    nmr_preproc / lcms_preproc service (or None: every layer is then
    reported skipped, never silent)."""

    metabolomics_input: MetabolomicsInput
    sample_sheet: dict[str, dict[str, str]] = Field(default_factory=dict)
    preprocessing_result: dict[str, Any] | None = Field(
        default=None,
        description="Serialised PreprocessingResult: matrix (rows x "
        "buckets), ppm axis, sample_ids and the PreprocessingRecord",
    )
    pca_min_samples: int = Field(default=2, ge=2)
    vip_threshold: float = Field(default=1.0, gt=0)
    stocsy_r_threshold: float = Field(default=0.7, gt=0, le=1)
    stocsy_fdr_threshold: float = Field(default=0.05, gt=0, le=1)
    run_2d_stocsy: bool = True


class ApplyMetabolomicsLayersOutput(_Contract):
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    skipped_layers: list[dict[str, str]] = Field(default_factory=list)
    pca: dict[str, Any] | None = None
    model: dict[str, Any] | None = None
    vip_features: list[dict[str, Any]] = Field(default_factory=list)
    stocsy_correlations: list[dict[str, Any]] = Field(default_factory=list)
    reporting_checklist: dict[str, Any] | None = None
    sample_ids: list[str] = Field(default_factory=list)


class DeconvolveSpectrumInput(_Contract):
    """Line-shape deconvolution of overlapped 1D regions (ADR-0073).

    The spectrum arrives as inline arrays (the orchestrator serialises
    what the preprocessing step produced); regions are ppm windows the
    analyst nominates.  Unusable input becomes ``skipped=True`` with
    the NAMED reason, never a guessed fit."""

    ppm: list[float] = Field(..., min_length=8)
    intensity: list[float] = Field(..., min_length=8)
    sf_mhz: float = Field(default=500.0, gt=0)
    regions: list[tuple[float, float]] = Field(..., min_length=1)
    model: str = "pseudovoigt"
    snr_threshold: float = Field(default=10.0, gt=0)
    max_components: int = Field(default=12, ge=1, le=64)
    baseline_degree: int = Field(default=1, ge=0, le=1)
    extra_seeds_ppm: list[float] = Field(default_factory=list)


class DeconvolveSpectrumOutput(_Contract):
    fits: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    model_id: str | None = None
    skipped: bool = False
    reason: str | None = None


class QuantifySampleInput(_Contract):
    """Internal-standard qNMR quantification (ADR-0073).

    ``standard``: {name, n_protons, integral, moles_nmol | mass_mg +
    molecular_weight + purity_fraction}; ``analytes``: list of
    {name, n_protons, integral, molecular_weight?}.  Integrals come
    from the deconvolve/integrate outputs -- ratio units cancel."""

    standard: dict[str, Any]
    analytes: list[dict[str, Any]] = Field(..., min_length=1)
    sample_mass_mg: float | None = Field(default=None, gt=0)


class QuantifySampleOutput(_Contract):
    result: dict[str, Any] | None = None
    skipped: bool = False
    reason: str | None = None


class EvaluateEquationInput(_Contract):
    """Custom-equation evaluation under the AST whitelist (ADR-0074).

    ``expression`` must be whitelisted arithmetic/comparison; ``specs``
    are boolean pass/fail expressions with warning/severe levels."""

    expression: str = Field(..., min_length=1)
    values: dict[str, float] = Field(..., min_length=1)
    name: str = "adhoc"
    specs: list[dict[str, Any]] = Field(default_factory=list)


class EvaluateEquationOutput(_Contract):
    value: float | bool | None = None
    substituted: str = ""
    notes: list[str] = Field(default_factory=list)
    spec_results: list[dict[str, Any]] = Field(default_factory=list)
    skipped: bool = False
    reason: str | None = None


# --- Tier 3 ---------------------------------------------------------------


class LLMExplainInput(_Contract):
    evidence_graph: EvidenceGraph


class LLMExplainOutput(_Contract):
    explanation: str = Field(..., min_length=1)


class LLMProposeExperimentsInput(_Contract):
    candidate: Candidate


class LLMProposeExperimentsOutput(_Contract):
    experiments: list[str] = Field(..., min_length=1)
    rationale: str


class LLMHypothesizeInput(_Contract):
    """Input for a grounded mechanism hypothesis (ADR-0029).

    ``context_papers`` is mandatory: the LLM never answers without retrieved
    evidence, so a hypothesis task that has not been fed a literature result
    cannot even be constructed.
    """

    context_papers: list[PaperHit] = Field(..., min_length=1)
    compound_name: str = Field(..., min_length=1)
    phenotype: str = Field(default="the observed bioactivity", min_length=1)
    candidate: Candidate | None = None


class LLMHypothesizeOutput(_Contract):
    hypothesis: str = Field(..., min_length=1)
    literature_refs: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    grounding_score: float = Field(default=0.0, ge=0.0, le=1.0)
    claims_total: int = Field(default=0, ge=0)
    claims_uncited: int = Field(default=0, ge=0)


class QAVerifyInput(_Contract):
    """Independent grounding audit of a Tier-3 text against its evidence."""

    text: str = Field(..., min_length=1)
    context_papers: list[PaperHit] = Field(..., min_length=1)
    tool_name: str = Field(default="llm.hypothesize", min_length=1)


class QAVerifyOutput(_Contract):
    passed: bool
    grounding_score: float = Field(..., ge=0.0, le=1.0)
    claims_total: int = Field(ge=0)
    claims_uncited: int = Field(ge=0)
    invalid_citations: list[str] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ToolSpec and registry
# ---------------------------------------------------------------------------


class ToolSpec(BaseModel):
    """One delegable capability, with its tier and I/O contract."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    tier: ToolTier
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    estimated_cost_seconds: float = Field(..., ge=0.0)
    requires_network: bool
    requires_gpu: bool = False
    enabled_in_dry_run: bool

    def cost_estimate_usd(self) -> float:
        """Rough cloud cost at $0.000008/vCPU-second (Cloud Run ballpark)."""
        return round(self.estimated_cost_seconds * 0.000008, 8)


class ToolRegistry:
    """Catalog of tools the orchestrator may delegate to."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise InvalidInputError(
                "Tool already registered", context={"tool": spec.name}
            )
        self._tools[spec.name] = spec
        return spec

    def get_tool(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise InvalidInputError(
                "Unknown tool", context={"tool": name, "known": sorted(self._tools)}
            ) from None

    def list_tools(
        self,
        tier: ToolTier | None = None,
        enabled_in_dry_run: bool | None = None,
    ) -> list[ToolSpec]:
        specs = self._tools.values()
        if tier is not None:
            specs = (s for s in specs if s.tier == tier)
        if enabled_in_dry_run is not None:
            specs = (s for s in specs if s.enabled_in_dry_run == enabled_in_dry_run)
        return sorted(specs, key=lambda s: (int(s.tier), s.name))

    def __len__(self) -> int:
        return len(self._tools)


TIER1_TOOL_NAMES = (
    "rdkit.descriptors",
    "rdkit.formula",
    "formula.exact_mass",
    "stats.t_test",
    "stats.anova",
    "stats.fdr_correction",
    "io.parse_peak_csv",
    "literature.search",
    "nmr_search.peaks",
    "taxonomy.resolve_species",
    "taxon_prior.build",
    "literature.search_compound_in_species",
    "isolation.load_record",
    "chromatography.apply_layers",
    "metabolomics.preprocess",
    "metabolomics.apply_layers",
    "nmr_preproc.deconvolve",
    "nmr_preproc.quantify",
    "nmr_preproc.evaluate_equation",
)
TIER2_TOOL_NAMES = (
    "gnps.submit_job",
    "gnps.fetch_results",
    "sirius.predict_formula",
    "shift_mpnn.predict",
    "hungarian.match",
    "nmr_networking.build",
    "qa.verify_grounding",
    "elucidation.rank_candidates",
)
TIER3_TOOL_NAMES = (
    "llm.explain",
    "llm.propose_experiments",
    "llm.hypothesize",
)


def build_default_registry() -> ToolRegistry:
    """Register every stub tool the platform will eventually need."""
    registry = ToolRegistry()

    tier1: list[tuple[str, str, float, bool, type, type]] = [
        (
            "rdkit.descriptors",
            "Molecular descriptors from SMILES (MW, HBD, HBA, logP, TPSA)",
            0.5, False, RDKitDescriptorsInput, RDKitDescriptorsOutput,
        ),
        (
            "rdkit.formula",
            "Molecular formula from SMILES",
            0.2, False, RDKitFormulaInput, RDKitFormulaOutput,
        ),
        (
            "formula.exact_mass",
            "Neutral monoisotopic exact mass from a formula",
            0.05, False, ExactMassInput, ExactMassOutput,
        ),
        (
            "stats.t_test",
            "Two-sample t-test",
            0.1, False, TTestInput, TTestOutput,
        ),
        (
            "stats.anova",
            "One-way ANOVA across groups",
            0.1, False, AnovaInput, AnovaOutput,
        ),
        (
            "stats.fdr_correction",
            "Benjamini-Hochberg FDR correction",
            0.1, False, FDRCorrectionInput, FDRCorrectionOutput,
        ),
        (
            "reference_hsqc.rank",
            "Rank formula-compatible structures against configured measured HSQC references",
            1.0, False, ReferenceHSQCInput, ReferenceHSQCOutput,
        ),
        (
            "io.parse_peak_csv",
            "Read a peak list from CSV",
            0.1, False, ParsePeakCSVInput, ParsePeakCSVOutput,
        ),
        (
            "literature.search",
            "Retrieve literature from PubMed, Semantic Scholar and OpenAlex "
            "with deduplication and BM25 ranking",
            2.0, True, LiteratureSearchInput, LiteratureSearchOutput,
        ),
        (
            "nmr_search.peaks",
            "Search NP-MRD, BMRB and nmrshiftdb2 by observed HSQC peak list",
            2.0, True, NMRPeakSearchInput, NMRPeakSearchOutput,
        ),
        (
            "taxonomy.resolve_species",
            "Resolve a species name to a canonical taxon and lineage "
            "(GBIF primary, ITIS fallback; ADR-0053)",
            1.0, True, ResolveSpeciesInput, ResolveSpeciesOutput,
        ),
        (
            "taxon_prior.build",
            "Query LOTUS/KNApSAcK/NPASS for compounds reported from the "
            "sample's taxon at species, genus and family level (ADR-0054)",
            3.0, True, TaxonPriorBuildInput, TaxonPriorBuildOutput,
        ),
        (
            "literature.search_compound_in_species",
            "PubMed search constrained to (compound AND species): papers "
            "reporting this compound from this organism (ADR-0052)",
            2.0, True, SpeciesPaperSearchInput, SpeciesPaperSearchOutput,
        ),
        (
            "isolation.load_record",
            "Load the bench isolation record (fractions, TLC, yield) for "
            "one extract_id (ADR-0055)",
            0.5, True, IsolationLoadInput, IsolationLoadOutput,
        ),
        (
            "chromatography.apply_layers",
            "Run the chromatographic evidence layers (Level-1 home library, "
            "RT/RI reranking, UV chromophore, peak purity, pharmacopoeial "
            "markers) over ranked candidates (ADR-0061..ADR-0066)",
            0.5, True,
            ApplyChromatographyLayersInput, ApplyChromatographyLayersOutput,
        ),
        (
            "metabolomics.preprocess",
            "Run the metabolomics preprocessing (raw Bruker FID -> bucketed "
            "matrix, or MZmine-format CSV adaptation) and serialise the "
            "result (ADR-0068, ADR-0069)",
            2.0, True,
            MetabolomicsPreprocessInput, MetabolomicsPreprocessOutput,
        ),
        (
            "metabolomics.apply_layers",
            "Run the metabolomics evidence layers (PCA, supervised model "
            "PLS-DA / O-PLS regression, VIP selection, STOCSY, reporting "
            "checklist) over a preprocessed feature matrix "
            "(ADR-0068..ADR-0072)",
            1.0, True,
            ApplyMetabolomicsLayersInput, ApplyMetabolomicsLayersOutput,
        ),
        (
            "nmr_preproc.deconvolve",
            "Line-shape deconvolution of overlapped 1D NMR regions "
            "(pseudo-Voigt/Lorentzian/Gaussian components on a local "
            "baseline, per-region fitted/failed/skipped status) for "
            "extract mixtures (ADR-0073)",
            2.0, True,
            DeconvolveSpectrumInput, DeconvolveSpectrumOutput,
        ),
        (
            "nmr_preproc.quantify",
            "Internal-standard qNMR quantification from region or "
            "fit-based integrals, every result carrying its substituted "
            "equation as audit text (ADR-0073)",
            0.5, True,
            QuantifySampleInput, QuantifySampleOutput,
        ),
        (
            "nmr_preproc.evaluate_equation",
            "Evaluate a user-defined derived quantity or pass/fail "
            "specification under the whitelisted AST evaluator "
            "(no eval/exec; ADR-0074)",
            0.1, True,
            EvaluateEquationInput, EvaluateEquationOutput,
        ),
    ]
    tier2: list[tuple[str, str, float, bool, type, type]] = [
        (
            "gnps.submit_job",
            "Submit a molecular networking job to GNPS2",
            30.0, True, GNPSSubmitInput, GNPSSubmitOutput,
        ),
        (
            "gnps.fetch_results",
            "Retrieve a GNPS network and its annotations",
            5.0, True, GNPSFetchInput, GNPSFetchOutput,
        ),
        (
            "sirius.predict_formula",
            "Molecular formula prediction from MS/MS (SIRIUS)",
            20.0, False, SiriusFormulaInput, SiriusFormulaOutput,
        ),
        (
            "shift_mpnn.predict",
            "Predicted 1H/13C shifts from SMILES (ShiftMPNN, GPU)",
            2.0, False, ShiftMPNNInput, ShiftMPNNOutput,
        ),
        (
            "hungarian.match",
            "Hungarian matching of observed vs predicted shifts with "
            "multiplicity penalty",
            0.5, False, HungarianMatchInput, HungarianMatchOutput,
        ),
        (
            "nmr_networking.build",
            "HSQC molecular network (Modified Hungarian distance)",
            3.0, False, NMRNetworkingInput, NMRNetworkingOutput,
        ),
        (
            "qa.verify_grounding",
            "Independently audit a Tier-3 text: grounding score, uncited claims "
            "and fabricated citations",
            0.2, False, QAVerifyInput, QAVerifyOutput,
        ),
        (
            "elucidation.rank_candidates",
            "Rank structural candidates against observed spectra, then rerank "
            "with the taxon prior and solvent compatibility (ADR-0052)",
            2.0, False, RankCandidatesInput, RankCandidatesOutput,
        ),
    ]
    tier3: list[tuple[str, str, float, bool, type, type]] = [
        (
            "llm.explain",
            "Natural-language explanation of an evidence graph",
            4.0, True, LLMExplainInput, LLMExplainOutput,
        ),
        (
            "llm.propose_experiments",
            "Recommend follow-up experiments for a candidate",
            4.0, True, LLMProposeExperimentsInput, LLMProposeExperimentsOutput,
        ),
        (
            "llm.hypothesize",
            "Propose a grounded biological mechanism hypothesis from retrieved literature",
            6.0, True, LLMHypothesizeInput, LLMHypothesizeOutput,
        ),
    ]

    for tier, stubs in (
        (ToolTier.TIER_1_DETERMINISTIC, tier1),
        (ToolTier.TIER_2_ML_VALIDATED, tier2),
        (ToolTier.TIER_3_LLM_GENERATED, tier3),
    ):
        for name, description, cost, network, in_schema, out_schema in stubs:
            registry.register(
                ToolSpec(
                    name=name,
                    tier=tier,
                    description=description,
                    input_schema=in_schema,
                    output_schema=out_schema,
                    estimated_cost_seconds=cost,
                    requires_network=network,
                    requires_gpu=(name == "shift_mpnn.predict"),
                    enabled_in_dry_run=True,
                )
            )
    return registry


DEFAULT_REGISTRY = build_default_registry()


# ---------------------------------------------------------------------------
# Dry-run mock executor
# ---------------------------------------------------------------------------


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Real BH adjustment (deterministic Tier 1 logic, reused by the mock)."""
    n = len(p_values)
    order = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [0.0] * n
    running = 1.0
    for rank_index in range(n - 1, -1, -1):
        idx = order[rank_index]
        rank = rank_index + 1
        running = min(running, p_values[idx] * n / rank)
        adjusted[idx] = min(1.0, running)
    return adjusted


def _mock_rdkit_descriptors(inp: RDKitDescriptorsInput) -> RDKitDescriptorsOutput:
    heavy = float(max(1, len(inp.smiles) // 3))
    return RDKitDescriptorsOutput(descriptors={"heavy_atoms": heavy, "hbd": 2.0})


def _mock_rdkit_formula(inp: RDKitFormulaInput) -> RDKitFormulaOutput:
    carbons = max(1, len(inp.smiles) // 8)
    return RDKitFormulaOutput(
        formula=MolecularFormula(counts={"C": carbons, "H": 2 * carbons + 2, "O": 2})
    )


def _mock_exact_mass(inp: ExactMassInput) -> ExactMassOutput:
    # Tier 1 arithmetic is exact and cheap: the mock IS the real computation.
    return ExactMassOutput(exact_mass=round(inp.formula.exact_mass(), 6))


def _mock_t_test(inp: TTestInput) -> TTestOutput:
    return TTestOutput(statistic=0.0, p_value=0.5)


def _mock_anova(inp: AnovaInput) -> AnovaOutput:
    return AnovaOutput(statistic=0.0, p_value=0.5)


def _mock_fdr(inp: FDRCorrectionInput) -> FDRCorrectionOutput:
    adjusted = _benjamini_hochberg(inp.p_values)
    return FDRCorrectionOutput(
        rejected=[p_adj < inp.alpha for p_adj in adjusted],
        adjusted_p_values=adjusted,
    )


def _mock_parse_csv(inp: ParsePeakCSVInput) -> ParsePeakCSVOutput:
    return ParsePeakCSVOutput(
        peaks=[Peak1D(ppm=7.26, intensity=100.0, snr=40.0, solvent_flag=True)]
    )


def _mock_gnps_submit(inp: GNPSSubmitInput) -> GNPSSubmitOutput:
    digest = hashlib.sha256(inp.title.encode()).hexdigest()[:10]
    return GNPSSubmitOutput(job_id=f"MOCK-GNPS-{digest}", status_url=None)


def _mock_gnps_fetch(inp: GNPSFetchInput) -> GNPSFetchOutput:
    return GNPSFetchOutput(network=FusedNetwork(), annotations={})


def _mock_sirius(inp: SiriusFormulaInput) -> SiriusFormulaOutput:
    mz = max(100.0, inp.peaks[0].mz)
    carbons = max(1, int(mz // 12.5))
    return SiriusFormulaOutput(
        formula=MolecularFormula(counts={"C": carbons, "H": 2 * carbons + 2}),
        confidence=0.85,
    )


def _mock_shift_mpnn(inp: ShiftMPNNInput) -> ShiftMPNNOutput:
    count = max(1, len(inp.smiles) % 6)
    return ShiftMPNNOutput(
        h_shifts=[round(6.8 + 0.2 * i, 3) for i in range(count)],
        c_shifts=[round(100.0 + 5.0 * i, 2) for i in range(count)],
        multiplicities=["s"] * count,
    )


def _mock_hungarian(inp: HungarianMatchInput) -> HungarianMatchOutput:
    count = min(len(inp.observed), len(inp.predicted_h))
    return HungarianMatchOutput(matches=[(i, i) for i in range(count)], cost=0.0)


def _mock_nmr_networking(inp: NMRNetworkingInput) -> NMRNetworkingOutput:
    return NMRNetworkingOutput(network=FusedNetwork())


def _mock_llm_explain(inp: LLMExplainInput) -> LLMExplainOutput:
    graph = inp.evidence_graph
    text = (
        f"MOCK explanation: the claim '{graph.claim}' is supported by "
        f"{len(graph.evidence)} evidence item(s) at confidence "
        f"{graph.confidence:.2f}; "
        + (f"open gaps: {'; '.join(graph.gaps)}." if graph.gaps else "no open gaps.")
    )
    return LLMExplainOutput(explanation=text)


def _mock_propose_experiments(
    inp: LLMProposeExperimentsInput,
) -> LLMProposeExperimentsOutput:
    return LLMProposeExperimentsOutput(
        experiments=[
            "Acquire HMBC to constrain long-range correlations",
            f"Compare retention and MS/MS against an authentic {inp.candidate.name} standard",
        ],
        rationale="MOCK LLM proposal; every experiment must be reviewed by the PI.",
    )


def _mock_hypothesize(inp: LLMHypothesizeInput) -> LLMHypothesizeOutput:
    """Deterministic grounded hypothesis.

    It cites two of the supplied context papers, so the grounding score is
    non-trivial and the QA/Critic's rules are genuinely exercised rather than
    passing on an empty string.  An uncited mock would make every grounding
    test vacuous.
    """
    first = inp.context_papers[0]
    second = inp.context_papers[1] if len(inp.context_papers) > 1 else first

    def cite(paper: PaperHit) -> str:
        if paper.pmid:
            return f"PMID: {paper.pmid}"
        if paper.doi:
            return f"doi: {paper.doi}"
        return "reference unavailable"

    hypothesis = (
        f"We hypothesise that {inp.compound_name} may modulate {inp.phenotype} "
        f"through the pathway reported in {first.title}. {cite(first)}. "
        "Supporting evidence indicates a dose-dependent effect on the signalling "
        f"described by the second retrieved study. {cite(second)}. "
        "This remains a hypothesis requiring experimental confirmation. "
        f"{cite(first)}."
    )
    from phyto_shared.grounding import compute_grounding

    report = compute_grounding(hypothesis, inp.context_papers)
    return LLMHypothesizeOutput(
        hypothesis=hypothesis,
        literature_refs=[p.pmid or p.doi or "" for p in inp.context_papers[:2]],
        citations=report.valid_citations,
        grounding_score=report.score,
        claims_total=report.claims_total,
        claims_uncited=report.claims_uncited,
    )


def _mock_literature_search(inp: LiteratureSearchInput) -> LiteratureSearchOutput:
    """Deterministic three-paper corpus, matching the literature service's mock."""
    papers = [
        PaperHit(
            pmid="10000001",
            doi="10.1000/mock.1",
            title="Quercetin modulates inflammatory signalling in vitro",
            abstract=(
                "Quercetin dose-dependently inhibits NF-kB activation in "
                "cultured macrophages."
            ),
            year=2021,
            authors=["A. Researcher"],
            journal="Journal of Natural Products",
            source="PubMed",
            url="https://pubmed.ncbi.nlm.nih.gov/10000001/",
        ),
        PaperHit(
            pmid="10000002",
            doi="10.1000/mock.2",
            title="Flavonoid biosynthesis and pathway regulation",
            abstract=(
                "A review of flavonoid biosynthesis, including chalcone "
                "synthase regulation in planta."
            ),
            year=2019,
            authors=["B. Scientist"],
            journal="Phytochemistry",
            source="Semantic Scholar",
            url="https://example.org/mock/2",
        ),
        PaperHit(
            pmid="10000003",
            doi="10.1000/mock.3",
            title="Andrographolide inhibits NF-kB translocation",
            abstract=(
                "Andrographolide reduces cytokine release in activated "
                "macrophages via NF-kB inhibition."
            ),
            year=2020,
            authors=["C. Chemist"],
            journal="Journal of Ethnopharmacology",
            source="OpenAlex",
            url="https://example.org/mock/3",
        ),
    ][: inp.max_results]
    return LiteratureSearchOutput(
        papers=papers,
        total_count=len(papers),
        source_counts={"PubMed": 1, "Semantic Scholar": 1, "OpenAlex": 1},
    )


def _mock_nmr_search(inp: NMRPeakSearchInput) -> NMRPeakSearchOutput:
    """Fixture-backed NMR structure search (no network).

    The fixture reader lives in the nmr_search service, so it is imported
    lazily.  When this package is not importable (the orchestrator container
    copies only ``shared/`` and its own service), the mock falls back to one
    inline deterministic hit rather than failing the workflow: a dry-run
    orchestrator must still be able to exercise its task graph.
    """
    try:
        from nmr_search.clients import MockNMRSearchClient

        hits = MockNMRSearchClient().search_by_peaks(
            inp.peaks, inp.h_tolerance_ppm, inp.c_tolerance_ppm, inp.max_results
        )
        counts = {"local-fixture": len(hits)}
    except ImportError:
        from phyto_shared.nmr_search_models import (
            LicenseInfo,
            NMRSpectrumMatch,
        )

        hits = [
            NMRSpectrumHit(
                database="local-fixture",
                database_id="ORCHESTRATOR-DRYRUN",
                name="dry-run placeholder structure",
                formula="C20H30O5",
                solvent="DMSO-d6",
                match=NMRSpectrumMatch(
                    matched_peaks=len(inp.peaks),
                    query_peaks=len(inp.peaks),
                    distance=0.0,
                    coverage=1.0,
                ),
                license=LicenseInfo(
                    name="CC-BY-NC",
                    commercial_use=False,
                    attribution="synthetic dry-run placeholder",
                ),
            )
        ][: inp.max_results]
        counts = {"dry-run-placeholder": len(hits)}
    return NMRPeakSearchOutput(
        hits=hits,
        total_count=len(hits),
        database_counts=counts,
        query_peak_count=len(inp.peaks),
    )


# ---------------------------------------------------------------------------
# Taxonomy-aware mocks (ADR-0052).  These delegate to the sibling services'
# own mock clients when those packages are importable (the local test plane
# aliases every service), and fail explicitly otherwise: fabricating an
# inline taxonomy would duplicate fixture data and silently diverge from
# the services' behaviour.
# ---------------------------------------------------------------------------


def _taxonomy_mock():
    from taxonomy.clients import MockTaxonomyClient

    return MockTaxonomyClient()


def _mock_resolve_species(inp: ResolveSpeciesInput) -> ResolveSpeciesOutput:
    try:
        client = _taxonomy_mock()
    except ImportError as exc:
        raise CloudServiceError(
            "taxonomy service not staged in this plane; cannot resolve species",
            context={"name": inp.name},
        ) from exc
    resolution = client.resolve(inp.name)
    if resolution is None:
        raise InvalidInputError(
            "mock taxonomy does not know this species", context={"name": inp.name}
        )
    return ResolveSpeciesOutput(
        resolution=resolution, lineage_depth=len(resolution.lineage)
    )


def _mock_taxon_prior_build(inp: TaxonPriorBuildInput) -> TaxonPriorBuildOutput:
    try:
        from taxon_prior.prior import build_taxon_prior
    except ImportError as exc:
        raise CloudServiceError(
            "taxon_prior service not staged in this plane; cannot build prior",
            context={"species": inp.species},
        ) from exc
    prior = build_taxon_prior(
        inp.species, inp.genus, inp.family, resolver=_taxonomy_mock()
    )
    return TaxonPriorBuildOutput(
        prior=prior,
        n_species=len(prior.species_compounds),
        n_genus=len(prior.genus_compounds),
        n_family=len(prior.family_compounds),
    )


def _mock_species_paper_search(
    inp: SpeciesPaperSearchInput,
) -> SpeciesPaperSearchOutput:
    """Two deterministic (compound AND species) hits, PubMed-sourced.

    The titles name both the compound and the species so downstream
    consumers (and tests) can see the query actually constrained the
    retrieval to the organism.
    """
    papers = [
        PaperHit(
            pmid="20000001",
            doi="10.1000/mock.species.1",
            title=(
                f"{inp.compound_name} isolated from {inp.species_name} and "
                "characterised by NMR"
            ),
            abstract=(
                f"Bioactivity-guided isolation of {inp.compound_name} from "
                f"{inp.species_name}; full NMR and HRMS assignment."
            ),
            year=2021,
            authors=["D. Naturalproducts"],
            journal="Journal of Natural Products",
            source="PubMed",
            url="https://pubmed.ncbi.nlm.nih.gov/20000001/",
        ),
        PaperHit(
            pmid="20000002",
            doi="10.1000/mock.species.2",
            title=f"Chemical constituents of {inp.species_name}: {inp.compound_name} and related metabolites",
            abstract=(
                f"Phytochemical investigation of {inp.species_name} affording "
                f"{inp.compound_name} among the major constituents."
            ),
            year=2019,
            authors=["E. Ethnobotanist"],
            journal="Phytochemistry",
            source="PubMed",
            url="https://pubmed.ncbi.nlm.nih.gov/20000002/",
        ),
    ][: inp.max_results]
    return SpeciesPaperSearchOutput(
        papers=papers,
        total_count=len(papers),
        source_counts={"PubMed": len(papers)},
    )


# Fixture library for the mock ranker: formula/mass verified via RDKit.
_MOCK_RANK_LIBRARY: tuple[tuple[str, str, dict[str, int], float, float], ...] = (
    # (name, smiles, formula counts, exact mass, mock spectral distance)
    ("caffeine", "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
     {"C": 8, "H": 10, "N": 4, "O": 2}, 194.080376, 1.00),
    ("quercetin", "O=c1cc(-c2c(O)cc(O)cc2O)oc2cc(O)cc(O)c12",
     {"C": 15, "H": 10, "O": 7}, 302.042653, 1.10),
    # quercetin-3-O-glucoside: a flavonol GLYCOSIDE one log unit more
    # polar than its aglycone; its fixture distance sits just behind
    # the aglycone's so only the isolation layer can promote it
    # (ADR-0055 integration).
    ("isoquercitrin",
     "C1=CC(=C(C=C1C2=C(C(=O)C3=C(C=C(C=C3O2)O)O)O)OC4C(C(C(C(O4)CO)O)O)O)O",
     {"C": 21, "H": 20, "O": 12}, 464.095477, 1.08),
    ("panduratin A", "CC(C)=CCC[C@@H](C)C(=O)c1c(O)cc(O)c(C)c1",
     {"C": 16, "H": 22, "O": 3}, 262.156895, 1.15),
)


def _mock_rank_candidates(inp: RankCandidatesInput) -> RankCandidatesOutput:
    """Deterministic mock spectral ranking + the REAL taxonomy reranker.

    The mock distance is a fixture (the real ranker lives in the
    elucidation service), but the reranking is genuinely executed when
    the prior is present: the whole point of the identify workflow is
    that ADR-0052's bonus/evidence logic runs end to end.  Without the
    reranker importable the tool still returns the spectral ranking and
    marks ``taxon_prior_applied=False`` explicitly rather than failing --
    the skip is visible, not silent.
    """
    ranks: list[CandidateRank] = []
    for name, smiles, counts, mass, distance in _MOCK_RANK_LIBRARY:
        candidate = Candidate(
            id=name.lower().replace(" ", "-"),
            name=name,
            smiles=smiles,
            formula=MolecularFormula(counts=counts),
            exact_mass=mass,
            provenance="experimental",
            source="orchestrator mock library",
        )
        ranks.append(
            CandidateRank(
                candidate=candidate,
                score=distance,
                ranking_convention="lower_is_better",
                coverage=0.5,
                verdict=ConfidenceVerdict.TENTATIVE,
                evidence_graph=EvidenceGraph(
                    node_id=candidate.id,
                    claim=f"identity: {name}",
                    evidence=[
                        EvidenceItem(
                            source_spectrum="observed:mock",
                            spectral_feature="fixture spectral distance",
                            relation="matched:mock",
                            strength=round(max(0.0, 1.0 - distance / 4.0), 4),
                        )
                    ],
                    confidence=0.5,
                ),
            )
        )
    ranks.sort(key=lambda r: r.score)

    # --- isolation layer (ADR-0055) ---------------------------------
    # Runs after the UV/RT signals carried in the spectral ranking and
    # BEFORE the taxon reranker: bench isolation of THIS sample is more
    # specific than what the species is generally reported to contain.
    isolation_applied = False
    if inp.isolation_record is not None:
        try:
            from elucidation.isolation_reranker import rerank_with_isolation
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "elucidation service not staged in this plane; isolation "
                "reranking skipped"
            )
        else:
            record = inp.isolation_record
            if inp.isolation_compound_id is not None:
                compound = record.compound(inp.isolation_compound_id)
            else:
                # MVP: apply the record's FIRST compound -- the primary
                # isolate.  Selecting among several isolates is deferred
                # with the full isolation graph (ADR-0055).
                compound = record.compounds[0] if record.compounds else None
            if compound is None:
                logging.getLogger(__name__).info(
                    "isolation layer skipped: record has no compounds"
                )
            else:
                ranks = rerank_with_isolation(
                    ranks, compound, record.fraction_registry()
                )
                isolation_applied = True

    if not inp.apply_taxon_prior or inp.taxon_prior is None:
        import logging

        logging.getLogger(__name__).info(
            "taxon prior skipped: no metadata"
        )
        top = ranks[0] if ranks else None
        return RankCandidatesOutput(
            ranks=ranks,
            top_candidate_name=top.candidate.name if top else None,
            top_evidence_graph=top.evidence_graph if top else None,
            taxon_prior_applied=False,
            isolation_applied=isolation_applied,
        )

    try:
        from elucidation.taxonomy_reranker import rerank_with_taxon_prior
    except ImportError:
        import logging

        logging.getLogger(__name__).warning(
            "elucidation service not staged in this plane; spectral ranking "
            "returned without taxon reranking"
        )
        top = ranks[0] if ranks else None
        return RankCandidatesOutput(
            ranks=ranks,
            top_candidate_name=top.candidate.name if top else None,
            top_evidence_graph=top.evidence_graph if top else None,
            taxon_prior_applied=False,
            isolation_applied=isolation_applied,
        )

    reranked = rerank_with_taxon_prior(ranks, inp.taxon_prior, inp.solvent)
    top = reranked[0] if reranked else None
    return RankCandidatesOutput(
        ranks=reranked,
        top_candidate_name=top.candidate.name if top else None,
        top_evidence_graph=top.evidence_graph if top else None,
        taxon_prior_applied=True,
        isolation_applied=isolation_applied,
    )


_MOCK_ISOLATION_RECORDS: dict[str, IsolationRecord] = {}
"""Extract_id -> record, seeded by tests; the local-plane stand-in for
the isolation service's Firestore.  Deterministic and empty by default:
an unknown extract_id is a genuine miss, and the workflow then notes
'isolation layer skipped: no record' (ADR-0055)."""


def _mock_isolation_load(inp: IsolationLoadInput) -> IsolationLoadOutput:
    return IsolationLoadOutput(record=_MOCK_ISOLATION_RECORDS.get(inp.extract_id))


def _isolation_load_output(output: Any) -> dict:
    """Engine-facing output for ``isolation.load_record``: the mock
    executor emits whatever ``serialisable()`` provides, keeping the
    datetime out of the raw-JSON path."""
    return output.serialisable()


def _mock_apply_chromatography_layers(
    inp: ApplyChromatographyLayersInput,
) -> ApplyChromatographyLayersOutput:
    """Run the REAL layer chain (layer_chain.apply_chromatography_layers)
    over the ranks -- the layers are the deliverable, so the local plane
    executes them for real, exactly like the taxon reranker."""
    try:
        from .layer_chain import apply_chromatography_layers
    except ImportError:
        try:
            from services.orchestrator.app.layer_chain import (
                apply_chromatography_layers,
            )
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "layer chain not staged in this plane; ranks returned "
                "untouched with every layer reported skipped"
            )
            skipped = [
                {"layer": tag, "reason": "layer skipped: chain not staged"}
                for tag in (
                    "level1", "rt", "uv", "purity", "pharmacopoeial_markers"
                )
            ]
            return ApplyChromatographyLayersOutput(
                ranks=inp.ranks, layers_applied=[], skipped_layers=skipped
            )
    result = apply_chromatography_layers(
        inp.ranks,
        species=inp.species,
        observed_rt_min=inp.observed_rt_min,
        observed_uv_spectrum=inp.observed_uv_spectrum,
        peak_purity_score=inp.peak_purity_score,
        lc_method=inp.lc_method,
        reference_standards=inp.reference_standards,
        anchor_series=inp.anchor_series,
        predict_rt_fn=None,
    )
    applied = [
        tag
        for tag in ("level1", "rt", "uv", "purity", "pharmacopoeial_markers")
        if tag not in {s["layer"] for s in result.skipped_layers}
    ]
    top = result.ranks[0] if result.ranks else None
    return ApplyChromatographyLayersOutput(
        ranks=result.ranks,
        layers_applied=applied,
        skipped_layers=result.skipped_layers,
        top_candidate_name=top.candidate.name if top else None,
    )


class _PreprocessingResultView:
    """Duck-typed view over the serialised PreprocessingResult dict
    (matrix, ppm, sample_ids, record) that layer_chain consumes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def matrix(self) -> Any:
        import numpy as np

        return np.asarray(self._data["matrix"], dtype=float)

    @property
    def ppm(self) -> Any:
        import numpy as np

        return (
            np.asarray(self._data["ppm"], dtype=float)
            if self._data.get("ppm") is not None
            else None
        )

    @property
    def sample_ids(self) -> list[str]:
        return list(self._data.get("sample_ids", []))

    @property
    def record(self) -> Any:
        rec = self._data.get("record")
        if rec is None:
            return None
        from phyto_shared.metabolomics import PreprocessingRecord

        return PreprocessingRecord.model_validate(rec)


def _mock_metabolomics_preprocess(
    inp: MetabolomicsPreprocessInput,
) -> MetabolomicsPreprocessOutput:
    """Run the REAL preprocessing for the declared input kind.  Anything
    unusable raises Skip{Preprocessing,Adaptation}, which becomes a
    skipped=False-but-empty output carrying the NAMED reason -- the
    orchestrator reports it, never guesses."""
    mi = inp.metabolomics_input
    if mi.kind == "nmr_bruker_dirs":
        try:
            from nmr_preproc.pipeline import (
                SkipPreprocessing,
                preprocess_experiments,
            )
        except ImportError:
            try:
                from services.nmr_preproc.app.pipeline import (  # type: ignore
                    SkipPreprocessing,
                    preprocess_experiments,
                )
            except ImportError:
                return MetabolomicsPreprocessOutput(
                    skipped=True,
                    reason="nmr_preproc service not staged in this plane",
                )
        try:
            result = preprocess_experiments(mi.paths)
        except SkipPreprocessing as skip:
            return MetabolomicsPreprocessOutput(skipped=True, reason=skip.reason)
        return MetabolomicsPreprocessOutput(
            preprocessing_result={
                "matrix": result.matrix.tolist(),
                "ppm": result.ppm.tolist(),
                "sample_ids": result.sample_ids,
                "record": result.record.model_dump(mode="json"),
                "notes": result.notes,
            }
        )
    # feature_table_csv
    try:
        from lcms_preproc.adapter import SkipAdaptation, adapt_csv, build_record
    except ImportError:
        try:
            from services.lcms_preproc.app.adapter import (  # type: ignore
                SkipAdaptation, adapt_csv, build_record,
            )
        except ImportError:
            return MetabolomicsPreprocessOutput(
                skipped=True,
                reason="lcms_preproc service not staged in this plane",
            )
    import numpy as np


    try:
        first = Path(mi.paths[0])
        text = first.read_text() if first.exists() else mi.paths[0]
    except OSError:
        text = mi.paths[0]
    try:
        table = adapt_csv(text)
    except SkipAdaptation as skip:
        return MetabolomicsPreprocessOutput(skipped=True, reason=skip.reason)
    normalisation = "none"
    matrix = np.array(
        [
            [table.intensities[s].get(f, 0.0) for f in table.feature_ids]
            for s in table.sample_ids
        ],
        dtype=float,
    )
    record = build_record(table, normalisation)
    return MetabolomicsPreprocessOutput(
        preprocessing_result={
            "matrix": matrix.tolist(),
            "ppm": None,
            "sample_ids": table.sample_ids,
            "record": record.model_dump(mode="json"),
            "notes": table.notes,
        }
    )


def _mock_apply_metabolomics_layers(
    inp: ApplyMetabolomicsLayersInput,
) -> ApplyMetabolomicsLayersOutput:
    """Run the REAL metabolomics layer chain over the (serialised)
    preprocessing result.  A layer that cannot run is reported skipped
    by the chain itself; this wrapper only translates the boundary."""
    try:
        from layer_chain import apply_metabolomics_layers
    except ImportError:
        try:
            from services.orchestrator.app.layer_chain import (
                apply_metabolomics_layers,
            )
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "metabolomics layer chain not staged in this plane; every "
                "layer reported skipped"
            )
            skipped = [
                {"layer": tag, "reason": "layer skipped: chain not staged"}
                for tag in (
                    "preprocessing", "pca", "supervised_model",
                    "vip_selection", "stocsy", "reporting_checklist",
                )
            ]
            return ApplyMetabolomicsLayersOutput(
                evidence=[], skipped_layers=skipped
            )
    if inp.preprocessing_result is None:
        preprocessing: Any = None
    else:
        preprocessing = _PreprocessingResultView(inp.preprocessing_result)
    sheet = inp.sample_sheet
    sheet_path = inp.metabolomics_input.sample_sheet_path
    if not sheet and sheet_path:
        import csv as _csv

        p = Path(sheet_path)
        text = p.read_text() if p.exists() else sheet_path
        rows = list(_csv.DictReader(text.splitlines()))
        sheet = {
            (row.get("sample_id") or row.get("sample") or ""): {
                k: v for k, v in row.items() if k not in ("sample_id", "sample")
            }
            for row in rows
        }
        sheet = {k: v for k, v in sheet.items() if k}
    result = apply_metabolomics_layers(
        inp.metabolomics_input,
        sheet or None,
        preprocessing,
        pca_min_samples=inp.pca_min_samples,
        vip_threshold=inp.vip_threshold,
        stocsy_r_threshold=inp.stocsy_r_threshold,
        stocsy_fdr_threshold=inp.stocsy_fdr_threshold,
        run_2d_stocsy=inp.run_2d_stocsy,
    )
    return ApplyMetabolomicsLayersOutput(
        evidence=[
            {
                "source_spectrum": e.source_spectrum,
                "spectral_feature": e.spectral_feature,
                "relation": e.relation,
                "strength": e.strength,
            }
            for e in result.evidence
        ],
        skipped_layers=result.skipped_layers,
        pca=result.pca_evidence.model_dump(mode="json") if result.pca_evidence else None,
        model=result.model_evidence.model_dump(mode="json") if result.model_evidence else None,
        vip_features=[f.model_dump(mode="json") for f in result.vip_features],
        stocsy_correlations=[c.model_dump(mode="json") for c in result.stocsy_correlations],
        reporting_checklist=result.checklist.model_dump(mode="json") if result.checklist else None,
        sample_ids=result.sample_ids,
    )


def _mock_qa_verify(inp: QAVerifyInput) -> QAVerifyOutput:
    """The grounding audit IS the real implementation -- no mock needed."""
    from phyto_shared.grounding import GROUNDING_MIN_SCORE, compute_grounding

    report = compute_grounding(inp.text, inp.context_papers)
    passed = (
        report.score >= GROUNDING_MIN_SCORE
        and report.claims_uncited == 0
        and not report.invalid_citations
    )
    checks = [
        {"name": "grounding_score", "passed": report.score >= GROUNDING_MIN_SCORE,
         "detail": f"{report.score:.4f} (minimum {GROUNDING_MIN_SCORE})"},
        {"name": "all_claims_cited", "passed": report.claims_uncited == 0,
         "detail": f"{report.claims_uncited} uncited claim(s)"},
        {"name": "no_fabricated_citations", "passed": not report.invalid_citations,
         "detail": f"invalid: {report.invalid_citations}"},
    ]
    return QAVerifyOutput(
        passed=passed,
        grounding_score=report.score,
        claims_total=report.claims_total,
        claims_uncited=report.claims_uncited,
        invalid_citations=report.invalid_citations,
        checks=checks,
    )


def _mock_deconvolve(inp: DeconvolveSpectrumInput) -> DeconvolveSpectrumOutput:
    """Run the REAL deconvolution (ADR-0073); skips carry named reasons."""
    from dataclasses import asdict

    import numpy as np

    try:
        from nmr_preproc.deconvolution import (
            DeconvolutionParams,
            SkipDeconvolution,
            deconvolve,
        )
    except ImportError:
        try:
            from services.nmr_preproc.app.deconvolution import (  # type: ignore
                DeconvolutionParams,
                SkipDeconvolution,
                deconvolve,
            )
        except ImportError:
            return DeconvolveSpectrumOutput(
                skipped=True,
                reason="nmr_preproc service not staged in this plane",
            )
    params = DeconvolutionParams(
        model=inp.model,
        sf_mhz=inp.sf_mhz,
        snr_threshold=inp.snr_threshold,
        max_components=inp.max_components,
        baseline_degree=inp.baseline_degree,
    )
    try:
        result = deconvolve(
            np.asarray(inp.ppm, dtype=float),
            np.asarray(inp.intensity, dtype=float),
            inp.regions,
            params,
            extra_seeds_ppm=inp.extra_seeds_ppm,
        )
    except SkipDeconvolution as skip:
        return DeconvolveSpectrumOutput(skipped=True, reason=skip.reason)
    return DeconvolveSpectrumOutput(
        fits=[asdict(f) for f in result.fits],
        notes=result.notes,
        model_id=result.model_id,
    )


def _mock_quantify(inp: QuantifySampleInput) -> QuantifySampleOutput:
    """Run the REAL qNMR calculation (ADR-0073); skips carry named reasons."""
    from dataclasses import asdict

    try:
        from nmr_preproc.quantify import (
            AnalyteSpec,
            InternalStandard,
            SkipQuantification,
            qnmr_calculate,
        )
    except ImportError:
        try:
            from services.nmr_preproc.app.quantify import (  # type: ignore
                AnalyteSpec,
                InternalStandard,
                SkipQuantification,
                qnmr_calculate,
            )
        except ImportError:
            return QuantifySampleOutput(
                skipped=True,
                reason="nmr_preproc service not staged in this plane",
            )
    standard = inp.standard
    analytes = inp.analytes
    try:
        result = qnmr_calculate(
            InternalStandard(
                name=standard["name"],
                n_protons=standard["n_protons"],
                integral=standard["integral"],
                moles_nmol=standard.get("moles_nmol"),
                mass_mg=standard.get("mass_mg"),
                molecular_weight=standard.get("molecular_weight"),
                purity_fraction=standard.get("purity_fraction", 1.0),
            ),
            [
                AnalyteSpec(
                    name=a["name"],
                    n_protons=a["n_protons"],
                    integral=a["integral"],
                    molecular_weight=a.get("molecular_weight"),
                )
                for a in analytes
            ],
            sample_mass_mg=inp.sample_mass_mg,
        )
    except (SkipQuantification, KeyError, TypeError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, SkipQuantification) else str(exc)
        return QuantifySampleOutput(skipped=True, reason=reason)
    return QuantifySampleOutput(result=asdict(result))


def _mock_evaluate_equation(inp: EvaluateEquationInput) -> EvaluateEquationOutput:
    """Run the REAL whitelist evaluator (ADR-0074); every rejection names
    the offending construct."""
    from dataclasses import asdict

    try:
        from nmr_preproc.equations import (
            CustomEquation,
            EquationError,
            SpecCheck,
            check_specs,
            evaluate_equation,
        )
    except ImportError:
        try:
            from services.nmr_preproc.app.equations import (  # type: ignore
                CustomEquation,
                EquationError,
                SpecCheck,
                check_specs,
                evaluate_equation,
            )
        except ImportError:
            return EvaluateEquationOutput(
                skipped=True,
                reason="nmr_preproc service not staged in this plane",
            )
    try:
        result = evaluate_equation(
            CustomEquation(name=inp.name, expression=inp.expression),
            dict(inp.values),
        )
        spec_results = [
            asdict(s)
            for s in check_specs(
                [SpecCheck(**s) for s in inp.specs], dict(inp.values)
            )
        ]
    except EquationError as exc:
        return EvaluateEquationOutput(skipped=True, reason=exc.reason)
    except (KeyError, TypeError, ValueError) as exc:
        return EvaluateEquationOutput(
            skipped=True, reason=f"malformed spec entry: {exc}"
        )
    return EvaluateEquationOutput(
        value=result.value,
        substituted=result.substituted,
        notes=result.notes,
        spec_results=spec_results,
    )


_MOCK_IMPLEMENTATIONS: dict[str, Callable[[Any], Any]] = {
    "rdkit.descriptors": _mock_rdkit_descriptors,
    "rdkit.formula": _mock_rdkit_formula,
    "formula.exact_mass": _mock_exact_mass,
    "stats.t_test": _mock_t_test,
    "stats.anova": _mock_anova,
    "stats.fdr_correction": _mock_fdr,
    "io.parse_peak_csv": _mock_parse_csv,
    "literature.search": _mock_literature_search,
    "literature.search_compound_in_species": _mock_species_paper_search,
    "nmr_search.peaks": _mock_nmr_search,
    "taxonomy.resolve_species": _mock_resolve_species,
    "taxon_prior.build": _mock_taxon_prior_build,
    "isolation.load_record": _mock_isolation_load,
    "elucidation.rank_candidates": _mock_rank_candidates,
    "chromatography.apply_layers": _mock_apply_chromatography_layers,
    "metabolomics.preprocess": _mock_metabolomics_preprocess,
    "metabolomics.apply_layers": _mock_apply_metabolomics_layers,
    "nmr_preproc.deconvolve": _mock_deconvolve,
    "nmr_preproc.quantify": _mock_quantify,
    "nmr_preproc.evaluate_equation": _mock_evaluate_equation,
    "gnps.submit_job": _mock_gnps_submit,
    "gnps.fetch_results": _mock_gnps_fetch,
    "sirius.predict_formula": _mock_sirius,
    "shift_mpnn.predict": _mock_shift_mpnn,
    "hungarian.match": _mock_hungarian,
    "nmr_networking.build": _mock_nmr_networking,
    "qa.verify_grounding": _mock_qa_verify,
    "llm.explain": _mock_llm_explain,
    "llm.propose_experiments": _mock_propose_experiments,
    "llm.hypothesize": _mock_hypothesize,
}


class MockToolExecutor:
    """Explicit offline synthetic workflow support; never a real-mode fallback."""

    mode = "mock"
    supported_workflows: frozenset[str] | None = None

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def preflight(self, tool_names: list[str]) -> None:
        for tool_name in tool_names:
            if tool_name not in _MOCK_IMPLEMENTATIONS:
                raise InvalidInputError(
                    "No offline mock implementation for this workflow",
                    context={"tool": tool_name},
                )

    def provenance(self, tool_name: str) -> dict[str, Any]:
        self.registry.get_tool(tool_name)
        return {
            "execution_mode": "mock",
            "executor": type(self).__name__,
            "synthetic_scientific_results": True,
            "scientific_validation": "not_validated",
        }

    def health(self) -> dict[str, Any]:
        return {
            "mode": "mock",
            "configuration_valid": True,
            "dependencies_available": True,
            "supported_workflows": "offline mock templates",
            "requires_network": False,
            "scientific_ready": False,
            "scientific_validation": "not_validated",
        }

    def execute(self, tool_name: str, inputs: dict[str, Any]) -> BaseModel:
        spec = self.registry.get_tool(tool_name)
        if not spec.enabled_in_dry_run:
            raise CloudServiceError(
                "Tool is not available in dry-run mode",
                context={"tool": tool_name},
            )
        try:
            impl = _MOCK_IMPLEMENTATIONS[tool_name]
        except KeyError:
            raise InvalidInputError(
                "No mock implementation registered (yet); real implementation "
                "lands in Steps 4-6",
                context={"tool": tool_name},
            ) from None
        validated_input = spec.input_schema.model_validate(inputs)
        return spec.output_schema.model_validate(
            impl(validated_input).model_dump()
        )
