"""Pydantic v2 domain models for PhytoCompanion.

Conventions
-----------
* Every model inherits ``StrictModel`` (``extra="forbid"``) unless a class
  docstring documents a deviation.  Unknown fields are rejected so typos
  surface at the boundary instead of silently dropping data.
* ``schemas.py`` holds the separate, thinner API I/O models; this module
  holds the domain.
* Numeric ranges encode instrument reality (ppm windows, m/z > 0); anything
  outside them is bad data, not a corner case to coerce.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import ADDUCT_MASSES, ConfidenceVerdict, ELEMENT_MASSES, ELEMENT_VALENCES
from .exceptions import InvalidInputError
from .taxonomy_models import ReportedCompound

__all__ = [
    "StrictModel",
    "ReviewStatus",
    "RankingConvention",
    "Peak1D",
    "Peak2D",
    "MSPeak",
    "IRPeak",
    "Peak",
    "Spectrum",
    "MolecularFormula",
    "Adduct",
    "EvidenceItem",
    "EvidenceGraph",
    "Candidate",
    "CandidateRank",
    "CandidateEvidenceProfile",
    "NetworkNode",
    "NetworkEdge",
    "FusedNetwork",
    "AnalysisRun",
    "ProvenanceManifest",
]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class StrictModel(BaseModel):
    """Base config for all domain models: no extra fields, mutation allowed."""

    model_config = ConfigDict(frozen=False, extra="forbid")


ReviewStatus = Literal["unreviewed", "approved", "flagged", "rejected"]

RankingConvention = Literal["higher_is_better", "lower_is_better"]
"""Which comparison produced a score/rank: similarity-style scores use
``higher_is_better`` (cosine, coverage); distance-style scores use
``lower_is_better`` (SUMMIT combined score, HSQC Modified Hungarian
distances).  See ADR-0007/ADR-0008."""


# ---------------------------------------------------------------------------
# Peaks
# ---------------------------------------------------------------------------


class Peak1D(StrictModel):
    """A peak in a 1D spectrum (1H or 13C)."""

    peak_type: Literal["1d"] = "1d"
    ppm: float = Field(..., ge=-50.0, le=300.0, description="Chemical shift in ppm")
    intensity: float = Field(..., ge=0.0)
    snr: float | None = Field(default=None, ge=0.0, description="Signal-to-noise ratio")
    solvent_flag: bool = Field(default=False, description="Marked as a solvent peak")
    review_status: ReviewStatus = "unreviewed"


class Peak2D(StrictModel):
    """A cross-peak in a 2D correlation experiment.

    Note: for homonuclear experiments (COSY/TOCSY/NOESY) ``c_ppm`` carries
    the second 1H axis; the field layout is shared with heteronuclear
    experiments by design (ADR-0004, point 3).
    """

    peak_type: Literal["2d"] = "2d"
    h_ppm: float = Field(..., ge=-5.0, le=15.0, description="1H axis position (ppm)")
    c_ppm: float = Field(..., ge=-50.0, le=300.0, description="13C axis position (ppm)")
    intensity: float = Field(..., ge=0.0)
    experiment_type: Literal["HSQC", "HMBC", "COSY", "TOCSY", "NOESY"]
    solvent_flag: bool = False
    review_status: ReviewStatus = "unreviewed"


class MSPeak(StrictModel):
    """A peak in a mass spectrum."""

    peak_type: Literal["ms"] = "ms"
    mz: float = Field(..., gt=0.0, description="Mass-to-charge ratio (Da/e)")
    intensity: float = Field(..., ge=0.0)
    adduct: str | None = Field(
        default=None, description="Adduct name; must exist in ADDUCT_MASSES if given"
    )
    ms_level: int = Field(default=1, ge=1, le=10)

    @field_validator("adduct")
    @classmethod
    def _known_adduct(cls, v: str | None) -> str | None:
        if v is not None and v not in ADDUCT_MASSES:
            raise ValueError(
                f"Unknown adduct {v!r}; known adducts: {sorted(ADDUCT_MASSES)}"
            )
        return v


class IRPeak(StrictModel):
    """A band in an infrared spectrum."""

    peak_type: Literal["ir"] = "ir"
    wavenumber: float = Field(..., gt=0.0, le=20_000.0, description="cm^-1")
    intensity: float = Field(..., ge=0.0)
    functional_group_hint: str | None = Field(
        default=None, description="e.g. 'C=O stretch', 'broad O-H'"
    )


Peak = Annotated[
    Union[Peak1D, Peak2D, MSPeak, IRPeak],
    Field(discriminator="peak_type"),
]


# ---------------------------------------------------------------------------
# Spectra
# ---------------------------------------------------------------------------

SpectrumKind = Literal[
    "1H", "13C", "HSQC", "HMBC", "COSY", "TOCSY", "NOESY", "MS1", "MS2", "IR"
]

_KIND_TO_PEAK: dict[str, type[BaseModel]] = {
    "1H": Peak1D,
    "13C": Peak1D,
    "HSQC": Peak2D,
    "HMBC": Peak2D,
    "COSY": Peak2D,
    "TOCSY": Peak2D,
    "NOESY": Peak2D,
    "MS1": MSPeak,
    "MS2": MSPeak,
    "IR": IRPeak,
}


class Spectrum(StrictModel):
    """A validated spectrum of any supported kind with its provenance hash."""

    kind: SpectrumKind
    data: list[Peak] = Field(default_factory=list)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    provenance_hash: str | None = Field(
        default=None, pattern=_SHA256_PATTERN, description="SHA256 of the raw source file"
    )

    @model_validator(mode="after")
    def _kind_matches_data(self) -> "Spectrum":
        expected = _KIND_TO_PEAK[self.kind]
        for i, peak in enumerate(self.data):
            if not isinstance(peak, expected):
                raise ValueError(
                    f"Spectrum kind {self.kind!r} requires {expected.__name__} "
                    f"peaks, but data[{i}] is a {type(peak).__name__}"
                )
        return self


# ---------------------------------------------------------------------------
# Chemistry
# ---------------------------------------------------------------------------


class MolecularFormula(StrictModel):
    """An elemental composition with exact-mass and RDBE arithmetic.

    Element symbols are normalized (``"c"`` -> ``"C"``) and must exist in
    ``ELEMENT_MASSES``.  Counts must be integers >= 1; omit an element
    rather than passing 0.
    """

    counts: dict[str, int] = Field(..., min_length=1)

    @field_validator("counts")
    @classmethod
    def _validate_elements(cls, v: dict[str, int]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        for element, count in v.items():
            symbol = element.strip().capitalize()
            if symbol not in ELEMENT_MASSES:
                raise ValueError(
                    f"Unknown element {element!r}; supported: {sorted(ELEMENT_MASSES)}"
                )
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(
                    f"Count for {symbol} must be an integer >= 1 (got {count!r}); "
                    "omit the element instead of using 0"
                )
            normalized[symbol] = count
        return normalized

    def exact_mass(self) -> float:
        """Neutral monoisotopic mass in daltons."""
        return sum(
            ELEMENT_MASSES[symbol].monoisotopic_mass * n for symbol, n in self.counts.items()
        )

    def rdbe(self) -> float:
        """Ring/double-bond equivalent: 1 + 0.5 * sum(n_i * (v_i - 2)).

        Heuristic (see ``ELEMENT_VALENCES`` for the valence conventions).
        Non-integer values hint at odd-electron species or wrong formulas.
        """
        total = 1.0
        for symbol, n in self.counts.items():
            if symbol not in ELEMENT_VALENCES:
                raise InvalidInputError(
                    "RDBE undefined for element without a stored valence",
                    context={"element": symbol, "formula": str(self)},
                )
            total += 0.5 * n * (ELEMENT_VALENCES[symbol] - 2)
        return total

    def __str__(self) -> str:
        """Hill notation: C first, H second, remaining alphabetical
        (fully alphabetical when no carbon is present); counts of 1 are
        omitted, so water renders as ``H2O``."""
        keys = list(self.counts)
        if "C" in self.counts:
            order = ["C"] + (["H"] if "H" in self.counts else []) + sorted(
                k for k in keys if k not in ("C", "H")
            )
        else:
            order = sorted(keys)
        return "".join(
            el if self.counts[el] == 1 else f"{el}{self.counts[el]}" for el in order
        )


class Adduct(StrictModel):
    """An ionization adduct; mirrors ``constants.AdductInfo`` as a model."""

    name: str
    mass_delta: float = Field(
        ...,
        description="Added to the neutral exact mass (Da); negative for losses",
    )
    charge: int = Field(..., ge=-2, le=2, description="0 is invalid")
    polarity: Literal["positive", "negative"]

    @model_validator(mode="after")
    def _charge_matches_polarity(self) -> "Adduct":
        if self.charge == 0:
            raise ValueError("charge must be non-zero")
        if (self.charge > 0) != (self.polarity == "positive"):
            raise ValueError(
                f"polarity {self.polarity!r} is inconsistent with charge {self.charge}"
            )
        return self

    @classmethod
    def from_name(cls, name: str) -> "Adduct":
        """Build from a ``ADDUCT_MASSES`` key such as ``"[M+H]+"``."""
        try:
            info = ADDUCT_MASSES[name]
        except KeyError:
            raise InvalidInputError(
                "Unknown adduct name", context={"name": name, "known": sorted(ADDUCT_MASSES)}
            ) from None
        return cls(
            name=info.name,
            mass_delta=info.mass_delta,
            charge=info.charge,
            polarity=info.polarity,  # type: ignore[arg-type]
        )

    def ion_mz(self, neutral_exact_mass: float) -> float:
        """m/z of this adduct's singly/multiply charged ion of the molecule."""
        return (neutral_exact_mass + self.mass_delta) / abs(self.charge)


# ---------------------------------------------------------------------------
# Evidence and candidates
# ---------------------------------------------------------------------------


class EvidenceItem(StrictModel):
    """One piece of spectral evidence linked to a claim."""

    source_spectrum: str = Field(..., description="Spectrum id or provenance hash")
    spectral_feature: str = Field(
        ..., description="e.g. 'HSQC cross-peak 4.52/102.3 ppm'"
    )
    relation: str = Field(
        ..., description="How the feature supports (or contradicts) the claim"
    )
    strength: float = Field(..., ge=0.0, le=1.0)


class EvidenceGraph(StrictModel):
    """The evidence behind one structural or biological claim.

    Every claim the platform outputs must carry one of these: what supports
    it, what is missing (``gaps``), and an honest ``confidence``.
    """

    node_id: str
    claim: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list)


class Candidate(StrictModel):
    """A molecular structure under consideration."""

    id: str = Field(..., min_length=1)
    name: str
    smiles: str = Field(..., min_length=1)
    formula: MolecularFormula
    exact_mass: float = Field(
        ..., ge=0.0, description="Neutral monoisotopic mass (Da)"
    )
    provenance: Literal["experimental", "predicted", "synthetic", "structure_only"]
    source: str = Field(..., description="Database, enumeration run, or lab origin")
    evidence_graph: EvidenceGraph | None = None


class CandidateEvidenceProfile(StrictModel):
    """Which modalities were actually scored for one candidate, and what
    was NOT checkable.

    Attached to every rank so a single-modality analysis cannot be mistaken
    for a fused one: ``evaluated`` lists the modalities that produced a
    score, ``unavailable`` those the user supplied but that could not be
    used (e.g. MS2 without a reference spectrum), and ``families`` names
    the instrument families covered.
    """

    evaluated: list[str] = Field(
        default_factory=list,
        description="Modalities that contributed a score, e.g. ['h_1d', 'hsqc']",
    )
    unavailable: list[str] = Field(
        default_factory=list,
        description="Modalities supplied but unusable, e.g. ['ms2'] with no "
        "reference spectrum",
    )
    families: list[str] = Field(
        default_factory=list, description="Instrument families covered: 'NMR', 'MS'"
    )
    is_single_family: bool = Field(
        default=True,
        description="True when only one instrument family contributed evidence",
    )
    gap: str | None = Field(
        default=None,
        description="Honest statement of what cross-modal support is missing, "
        "or None when the evidence is genuinely multi-modal",
    )


class CandidateRank(StrictModel):
    """A candidate with its score and honest ranking statistics."""

    candidate: Candidate
    score: float
    ranking_convention: RankingConvention = Field(
        ...,
        description="Score semantics used to compute the rank/margin; the "
        "Step 5 ranker sets 'lower_is_better' for the SUMMIT combined "
        "distance and 'higher_is_better' for similarity scores (ADR-0008). "
        "Required: persisted ranks must never be convention-ambiguous.",
    )
    coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    margin: float | None = Field(
        default=None, description="Score gap to best distinct competitor (may be inf)"
    )
    ranking_interval: tuple[int, int] | None = None
    tied_candidates: list[str] = Field(
        default_factory=list, description="Candidate ids statistically tied with this one"
    )
    verdict: ConfidenceVerdict
    evidence_graph: EvidenceGraph | None = Field(
        default=None,
        description="Evidence supporting this rank (attached by the ranker; "
        "structured per-claim graphs are built by the elucidation service)",
    )
    evidence_profile: CandidateEvidenceProfile | None = Field(
        default=None,
        description="Modalities scored for this rank and the cross-modal gap; "
        "required reading before treating a rank as fused evidence (ADR-0050)",
    )
    taxon_prior_match: Literal["species", "genus", "family", "none"] = Field(
        default="none",
        description="Strongest taxonomic level at which this candidate is "
        "reported from the sample's taxon; set by the taxonomy reranker "
        "(ADR-0052). 'none' means no prior evidence OR the reranker did "
        "not run -- check taxon_prior_provenance to distinguish.",
    )
    taxon_prior_provenance: list[ReportedCompound] = Field(
        default_factory=list,
        description="The database reports behind taxon_prior_match: which "
        "database reported the compound, from which organism, at which "
        "taxonomic depth, and under which reference (ADR-0054)",
    )

    @model_validator(mode="after")
    def _interval_ordered(self) -> "CandidateRank":
        if self.ranking_interval is not None:
            lo, hi = self.ranking_interval
            if lo < 1 or hi < lo:
                raise ValueError(
                    f"ranking_interval must be (best, worst) with best >= 1 and "
                    f"worst >= best; got ({lo}, {hi})"
                )
        return self


# ---------------------------------------------------------------------------
# Networks
# ---------------------------------------------------------------------------

NetworkModality = Literal["MS2", "HSQC", "both"]

EdgeEvidenceType = Literal[
    "ms2_cosine",
    "hsqc_modified_hungarian",
    "fused",
    "bioactivity_correlation",
    "building_block",
    "annotation_propagation",
]


class NetworkNode(StrictModel):
    """A node in a (fused) molecular network.

    ``candidate_or_feature`` references either a ``Candidate.id`` or a
    feature identifier from the metabolomics service.
    """

    id: str = Field(..., min_length=1)
    candidate_or_feature: str
    modality: NetworkModality


class NetworkEdge(StrictModel):
    """An edge: MS2 similarity, HSQC similarity, or both (fused)."""

    source: str
    target: str
    modality: NetworkModality
    score: float = Field(..., ge=0.0, le=1.0)
    evidence_type: EdgeEvidenceType

    @model_validator(mode="after")
    def _no_self_loop(self) -> "NetworkEdge":
        if self.source == self.target:
            raise ValueError("self-loops are not allowed in molecular networks")
        return self


class FusedNetwork(StrictModel):
    """The fused MS2-HSQC molecular network (the platform's core artifact)."""

    nodes: list[NetworkNode] = Field(default_factory=list)
    edges: list[NetworkEdge] = Field(default_factory=list)
    modality_overlap_stats: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _topology_consistent(self) -> "FusedNetwork":
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        known = set(ids)
        for edge in self.edges:
            for endpoint in (edge.source, edge.target):
                if endpoint not in known:
                    raise ValueError(
                        f"edge {edge.source}->{edge.target} references unknown "
                        f"node {endpoint!r}"
                    )
        return self

    def compute_overlap_stats(self) -> dict[str, int]:
        """Counts summarizing how the two modalities overlap.

        ``edges_cross_modal`` counts edges whose endpoint node modalities
        differ (e.g. an MS2-only node linked to a both-modality node).
        """
        modality = {node.id: node.modality for node in self.nodes}
        stats = {
            "nodes_ms2_only": sum(1 for n in self.nodes if n.modality == "MS2"),
            "nodes_hsqc_only": sum(1 for n in self.nodes if n.modality == "HSQC"),
            "nodes_both": sum(1 for n in self.nodes if n.modality == "both"),
            "edges_ms2": sum(1 for e in self.edges if e.modality == "MS2"),
            "edges_hsqc": sum(1 for e in self.edges if e.modality == "HSQC"),
            "edges_both": sum(1 for e in self.edges if e.modality == "both"),
            "edges_cross_modal": sum(
                1 for e in self.edges if modality[e.source] != modality[e.target]
            ),
        }
        return stats


# ---------------------------------------------------------------------------
# Runs and provenance
# ---------------------------------------------------------------------------


class AnalysisRun(StrictModel):
    """One execution of an analysis workflow on one sample."""

    id: str = Field(..., min_length=1)
    sample_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    config: dict[str, Any] = Field(default_factory=dict)
    provenance_hash: str = Field(..., pattern=_SHA256_PATTERN)
    results: dict[str, Any] = Field(default_factory=dict)


class ProvenanceManifest(StrictModel):
    """The provenance record for one analysis.

    FROZEN by design (deviation from the mutable default): a provenance
    record is append-only evidence.  Build a new manifest rather than
    editing one; ``manifest_hash`` is computed over everything else, so any
    mutation invalidates it and ``verify_manifest`` will raise.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_hashes: dict[str, str] = Field(
        ..., description="label -> SHA256 of each raw input file"
    )
    model_versions: dict[str, str] = Field(default_factory=dict)
    database_versions: dict[str, str] = Field(
        default_factory=dict,
        description="value format: '<version> (downloaded <ISO date>)'",
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime
    manifest_hash: str = Field(
        default="",
        pattern=r"^([0-9a-f]{64})?$",
        description="SHA256 of the canonical JSON of all other fields",
    )

    def content_dict(self) -> dict[str, Any]:
        """JSON-clean view of everything except ``manifest_hash``.

        The canonical form hashed by ``sha256_dict``: identical here and in
        ``ProvenanceBuilder.build`` so verification is symmetric.
        """
        return {
            "input_hashes": self.input_hashes,
            "model_versions": self.model_versions,
            "database_versions": self.database_versions,
            "parameters": self.parameters,
            "environment": self.environment,
            "timestamp": self.timestamp.isoformat(),
        }
