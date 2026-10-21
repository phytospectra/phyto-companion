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
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.constants import ToolTier
from phyto_shared.exceptions import CloudServiceError, InvalidInputError
from phyto_shared.literature_models import PaperHit
from phyto_shared.models import (
    Candidate,
    EvidenceGraph,
    FusedNetwork,
    MolecularFormula,
    MSPeak,
    Peak1D,
    Peak2D,
)
from phyto_shared.nmr_search_models import (
    NMRSpectrumHit,
    NMRSpectrumSearchRequest,
)

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
)
TIER2_TOOL_NAMES = (
    "gnps.submit_job",
    "gnps.fetch_results",
    "sirius.predict_formula",
    "shift_mpnn.predict",
    "hungarian.match",
    "nmr_networking.build",
    "qa.verify_grounding",
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


_MOCK_IMPLEMENTATIONS: dict[str, Callable[[Any], Any]] = {
    "rdkit.descriptors": _mock_rdkit_descriptors,
    "rdkit.formula": _mock_rdkit_formula,
    "formula.exact_mass": _mock_exact_mass,
    "stats.t_test": _mock_t_test,
    "stats.anova": _mock_anova,
    "stats.fdr_correction": _mock_fdr,
    "io.parse_peak_csv": _mock_parse_csv,
    "literature.search": _mock_literature_search,
    "nmr_search.peaks": _mock_nmr_search,
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
    """Deterministic in-process executor for ``--dry-run`` and local tests.

    Validates inputs against each tool's input schema and returns canned
    (or genuinely computed, for trivial Tier 1 arithmetic) outputs --
    always validated against the output schema.  No network, no GPU.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

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
