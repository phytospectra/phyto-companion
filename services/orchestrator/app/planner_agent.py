"""Planner agent and workflow engine.

The planner decomposes a goal into a dependency-ordered task graph whose
tasks reference registry tools and, where the honesty rules demand it,
human checkpoints.  The engine executes the graph: enforces tiering,
delegates to tools (mock executor locally), verifies Tier-3 outputs
before persistence, pauses at unapproved checkpoints, and audit-logs
every step.  Neither of them performs heavy computation -- delegation,
verification, and logging only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phyto_shared.constants import ObservationModality, ToolTier, single_family_gap
from phyto_shared.exceptions import InvalidInputError, ToolTierViolationError
from phyto_shared.literature_models import PaperHit
from phyto_shared.provenance import sha256_dict
from phyto_shared.schemas import SampleMetadata

from .audit_logger import ChainedAuditLogger
from .qa_critic_agent import QACriticAgent
from .tool_registry import ToolRegistry
from .tool_tiering import (
    ExecutionContext,
    HumanCheckpoint,
    enforce_output_persistence,
    enforce_tiering,
)

__all__ = [
    "TaskStatus",
    "Task",
    "TaskGraph",
    "PrunedTask",
    "PlannerAgent",
    "WorkflowEngine",
    "EngineResult",
    "WORKFLOW_MODALITY_REQUIREMENTS",
]

logger = logging.getLogger(__name__)

WorkflowName = Literal[
    "single_compound",
    "compare_extracts",
    "novelty_scan",
    "build_network",
    "mechanism_hypothesis",
    "identify",
    "reference_hsqc_identify",
]


class ToolExecutor(Protocol):
    """Execution boundary; implementations choose real or synthetic science."""

    mode: Literal["mock", "real"]

    def execute(self, tool_name: str, inputs: dict[str, Any]) -> BaseModel: ...

    def preflight(self, tool_names: list[str]) -> None: ...

    def provenance(self, tool_name: str) -> dict[str, Any]: ...


class TaskStatus:
    """String constants (kept plain for JSON friendliness)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class Task(BaseModel):
    """One step in a task graph: a tool call or a checkpoint gate."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str
    tool: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    requires_checkpoint: HumanCheckpoint | None = None
    # Modalities this task can consume: AT LEAST ONE must have been observed.
    # Empty means the task has no modality requirement (a checkpoint, a
    # statistics step, a literature lookup).  Planning prunes tasks whose
    # requirements cannot be met and records why (ADR-0050) -- a step that
    # needs MS2 must never run on NMR-only input and report success.
    requires_modalities: frozenset[ObservationModality] = frozenset()
    # Tasks whose OUTPUT this task genuinely needs.  This is distinct from
    # ``depends_on``, which is often mere ordering (e.g. "predict shifts
    # after formula prediction"); a pruned dependency that is not consumed
    # can simply be rewired away.  A pruned CONSUMED task, by contrast,
    # removes this task too: its input no longer exists (ADR-0050).
    consumes: list[str] = Field(default_factory=list)
    # A JOIN: the task needs at least ONE of these outputs (e.g. "write the
    # network to the knowledge graph" works with whichever network was
    # built).  It is pruned only when ALL of them were pruned.
    consumes_any: list[str] = Field(default_factory=list)
    status: str = TaskStatus.PENDING
    result_summary: str | None = None
    error: str | None = None
    verified: bool | None = None
    # Output of the task, retained so dependent tasks can consume it.  Only
    # the engine writes this; planning leaves it unset.
    output: dict[str, Any] | None = None


class PrunedTask(BaseModel):
    """A planned step that the supplied evidence made impossible."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    reason: str


class TaskGraph(BaseModel):
    """A run's task graph; validated for unique ids and acyclicity."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow: WorkflowName
    dry_run: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tasks: list[Task] = Field(..., min_length=1)
    modalities: list[str] = Field(
        default_factory=list,
        description="Observation modalities supplied for this run; empty when "
        "the caller did not specify any",
    )
    pruned: list[PrunedTask] = Field(
        default_factory=list,
        description="Planned steps dropped because the supplied modalities "
        "cannot support them",
    )
    evidence_gap: str | None = Field(
        default=None,
        description="What the supplied modality set cannot establish, stated "
        "once for the whole run",
    )

    @model_validator(mode="after")
    def _validate_topology(self) -> "TaskGraph":
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        known = set(ids)
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"task '{task.id}' depends on unknown task '{dep}'"
                    )
        # Kahn's algorithm: every task must be schedulable
        pending = {task.id: set(task.depends_on) for task in self.tasks}
        ready = [tid for tid, deps in pending.items() if not deps]
        scheduled = 0
        while ready:
            current = ready.pop()
            scheduled += 1
            for tid, deps in pending.items():
                if current in deps:
                    deps.remove(current)
                    if not deps:
                        ready.append(tid)
        if scheduled != len(self.tasks):
            raise ValueError("task graph contains a dependency cycle")
        return self

    def task_by_id(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

# Fixture-grade placeholder inputs: schema-valid stand-ins for the real
# dataflow that arrives when the Step 4-6 tool services are integrated.
_QUERCETIN_SMILES = "Oc1cc(O)c(c(c1)O)-c2oc3cc(O)cc(O)c3c(=O)c2"
_HSQC_PEAK = {
    "h_ppm": 4.52,
    "c_ppm": 102.3,
    "intensity": 1.0e6,
    "experiment_type": "HSQC",
}
_MS2_PEAK = {"mz": 303.0499, "intensity": 9.5e5, "adduct": "[M+H]+", "ms_level": 2}
_EVIDENCE_GRAPH = {
    "node_id": "node-1",
    "claim": "identity: quercetin (to be confirmed by a human)",
    "evidence": [
        {
            "source_spectrum": "fixture",
            "spectral_feature": "HSQC cross-peak 4.52/102.3 ppm",
            "relation": "anomeric carbon consistent with flavonol C-ring",
            "strength": 0.8,
        }
    ],
    "confidence": 0.8,
    "gaps": ["no authentic-standard comparison yet"],
}


def _single_compound_tasks() -> list[Task]:
    return [
        Task(
            id="parse-peaks",
            description="Parse the uploaded peak list",
            tool="io.parse_peak_csv",
            inputs={"path": "fixtures/sample_peaks.csv"},
        ),
        Task(
            id="predict-formula",
            description="Predict molecular formula from MS/MS",
            tool="sirius.predict_formula",
            inputs={"peaks": [_MS2_PEAK]},
            depends_on=["parse-peaks"],
            requires_modalities=frozenset({ObservationModality.MS2}),
        ),
        Task(
            id="predict-shifts",
            description="Predict 1H/13C shifts for the top candidate",
            tool="shift_mpnn.predict",
            inputs={"smiles": _QUERCETIN_SMILES, "solvent": "DMSO-d6"},
            depends_on=["predict-formula"],
        ),
        Task(
            id="match-peaks",
            description="Match observed HSQC peaks against predictions",
            tool="hungarian.match",
            inputs={
                "observed": [_HSQC_PEAK],
                "predicted_h": [4.5],
                "predicted_c": [102.3],
            },
            depends_on=["parse-peaks", "predict-shifts"],
            requires_modalities=frozenset({ObservationModality.HSQC}),
        ),
        Task(
            id="approve-claim",
            description=(
                "HUMAN CHECKPOINT: review evidence before any CONFIRMED "
                "identification claim"
            ),
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_CONFIRMED_CLAIM,
            depends_on=["match-peaks"],
            consumes=["match-peaks"],
        ),
        Task(
            id="confirm-identity",
            description="Record the identification claim after approval",
            tool=None,
            depends_on=["approve-claim"],
            consumes=["approve-claim"],
        ),
        Task(
            id="explain-result",
            description="Generate a natural-language explanation of the evidence",
            tool="llm.explain",
            inputs={"evidence_graph": _EVIDENCE_GRAPH},
            depends_on=["confirm-identity"],
            consumes=["confirm-identity"],
        ),
    ]


def _compare_extracts_tasks() -> list[Task]:
    return [
        Task(
            id="parse-peaks",
            description="Parse the feature table",
            tool="io.parse_peak_csv",
            inputs={"path": "fixtures/extract_features.csv"},
        ),
        Task(
            id="anova",
            description="One-way ANOVA across extract groups",
            tool="stats.anova",
            inputs={
                "groups": {
                    "control": [1.0, 1.2, 0.9],
                    "treated": [2.1, 2.3, 2.0],
                }
            },
            depends_on=["parse-peaks"],
        ),
        Task(
            id="fdr",
            description="Benjamini-Hochberg correction over all features",
            tool="stats.fdr_correction",
            inputs={"p_values": [0.01, 0.04, 0.03, 0.20], "alpha": 0.05},
            depends_on=["anova"],
        ),
    ]


def _novelty_scan_tasks() -> list[Task]:
    return [
        Task(
            id="parse-peaks",
            description="Parse the fraction's peak list",
            tool="io.parse_peak_csv",
            inputs={"path": "fixtures/fraction_peaks.csv"},
        ),
        Task(
            id="predict-formula",
            description="Predict molecular formula from MS/MS",
            tool="sirius.predict_formula",
            inputs={"peaks": [_MS2_PEAK]},
            depends_on=["parse-peaks"],
            requires_modalities=frozenset({ObservationModality.MS2}),
        ),
        Task(
            id="predict-shifts",
            description="Predict 1H/13C shifts for the closest known analogue",
            tool="shift_mpnn.predict",
            inputs={"smiles": _QUERCETIN_SMILES, "solvent": "DMSO-d6"},
            depends_on=["predict-formula"],
        ),
        Task(
            id="approve-novel-generation",
            description=(
                "HUMAN CHECKPOINT: authorize novel structure candidate "
                "generation (chemical graph editing)"
            ),
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_NOVEL_STRUCTURE_GENERATION,
            depends_on=["predict-shifts"],
            consumes=["predict-shifts"],
        ),
        Task(
            id="generate-novel-candidates",
            description="Enumerate novel structure candidates (Step 5 service)",
            tool=None,
            depends_on=["approve-novel-generation"],
            consumes=["approve-novel-generation"],
        ),
        Task(
            id="approve-experiments",
            description=(
                "HUMAN CHECKPOINT: authorize follow-up experiment recommendations"
            ),
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_EXPERIMENT_RECOMMENDATION,
            depends_on=["generate-novel-candidates"],
            consumes=["generate-novel-candidates"],
        ),
        Task(
            id="propose-experiments",
            description="Recommend follow-up experiments for the novel candidate",
            tool="llm.propose_experiments",
            inputs={
                "candidate": {
                    "id": "cand-novel-1",
                    "name": "putative novel flavonoid",
                    "smiles": _QUERCETIN_SMILES,
                    "formula": {"counts": {"C": 15, "H": 10, "O": 7}},
                    "exact_mass": 302.042653,
                    "provenance": "predicted",
                    "source": "novelty scan",
                }
            },
            depends_on=["approve-experiments"],
        ),
    ]


def _build_network_tasks() -> list[Task]:
    return [
        Task(
            id="submit-gnps",
            description="Submit the FBMN job to GNPS2",
            tool="gnps.submit_job",
            inputs={"mgf_content": "BEGIN IONS\nEND IONS\n", "title": "sample A"},
            requires_modalities=frozenset({ObservationModality.MS2}),
        ),
        Task(
            id="fetch-gnps",
            description="Retrieve the GNPS network and annotations",
            tool="gnps.fetch_results",
            inputs={"job_id": "MOCK-GNPS-0000000000"},
            depends_on=["submit-gnps"],
            requires_modalities=frozenset({ObservationModality.MS2}),
        ),
        Task(
            id="hsqc-network",
            description="Build the HSQC molecular network",
            tool="nmr_networking.build",
            inputs={"spectra": {"sample_a": [_HSQC_PEAK]}},
            depends_on=["fetch-gnps"],
            requires_modalities=frozenset({ObservationModality.HSQC}),
        ),
        Task(
            id="approve-kg-write",
            description="HUMAN CHECKPOINT: authorize knowledge-graph writes",
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE,
            depends_on=["hsqc-network"],
            consumes_any=["fetch-gnps", "hsqc-network"],
        ),
        Task(
            id="write-knowledge-graph",
            description="Persist network annotations to the knowledge graph",
            tool=None,
            depends_on=["approve-kg-write"],
            consumes=["approve-kg-write"],
        ),
    ]


def _mechanism_hypothesis_tasks() -> list[Task]:
    """NMR search -> literature -> grounded hypothesis -> QA -> KG checkpoint.

    The tiers are deliberate: deterministic retrieval (Tier 1) supplies the
    evidence, the LLM only interprets it (Tier 3), and the QA/Critic's
    grounding audit is an independent deterministic re-check (Tier 2).  The
    final gate is a human checkpoint before anything reaches the knowledge
    graph, so a plausible-but-unverified mechanism cannot become "knowledge".
    """
    return [
        Task(
            id="nmr-search",
            description="Search spectral databases for structures matching the observed HSQC peaks",
            tool="nmr_search.peaks",
            inputs={
                "peaks": [
                    [1.575, 38.23],
                    [1.8, 29.07],
                    [3.41, 81.02],
                    [2.235, 39.0],
                    [6.85, 149.29],
                ],
                "max_results": 5,
            },
            requires_modalities=frozenset({ObservationModality.HSQC}),
        ),
        Task(
            id="literature-search",
            description="Retrieve literature for the top candidate structure",
            tool="literature.search",
            inputs={
                "query": "andrographolide anti-inflammatory mechanism",
                "max_results": 5,
            },
            depends_on=["nmr-search"],
            # The query names the top spectral hit, so the search result is
            # genuinely needed -- not mere ordering.
            consumes=["nmr-search"],
        ),
        Task(
            id="approve-mechanism",
            description=(
                "HUMAN CHECKPOINT: authorize the Tier-3 LLM call and the "
                "evidence set it will reason over"
            ),
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_MECHANISM_HYPOTHESIS,
            depends_on=["literature-search"],
            consumes=["literature-search"],
        ),
        Task(
            id="hypothesize-mechanism",
            description="Generate an evidence-grounded mechanism hypothesis",
            tool="llm.hypothesize",
            inputs={
                "compound_name": "andrographolide",
                "phenotype": "anti-inflammatory activity",
            },
            # context_papers is injected from literature-search at run time.
            depends_on=["approve-mechanism", "literature-search"],
            consumes=["approve-mechanism", "literature-search"],
        ),
        Task(
            id="verify-grounding",
            description="QA/Critic: verify grounding score, citation coverage and citation validity",
            tool="qa.verify_grounding",
            inputs={},
            # text and context_papers are injected from the hypothesis and
            # literature tasks at run time.
            depends_on=["hypothesize-mechanism", "literature-search"],
            consumes=["hypothesize-mechanism", "literature-search"],
        ),
        Task(
            id="approve-kg-write",
            description="HUMAN CHECKPOINT: authorize knowledge-graph write of the verified mechanism",
            tool=None,
            requires_checkpoint=HumanCheckpoint.BEFORE_KNOWLEDGE_GRAPH_WRITE,
            depends_on=["verify-grounding"],
            consumes=["verify-grounding"],
        ),
        Task(
            id="write-knowledge-graph",
            description="Persist the verified mechanism hypothesis to the knowledge graph",
            tool=None,
            depends_on=["approve-kg-write"],
            consumes=["approve-kg-write"],
        ),
    ]


_TEMPLATES: dict[WorkflowName, Any] = {
    "single_compound": _single_compound_tasks,
    "compare_extracts": _compare_extracts_tasks,
    "novelty_scan": _novelty_scan_tasks,
    "build_network": _build_network_tasks,
    "mechanism_hypothesis": _mechanism_hypothesis_tasks,
}


# ---------------------------------------------------------------------------
# Identify workflow (taxonomy-aware, ADR-0052)
# ---------------------------------------------------------------------------


def _identify_tasks(sample_metadata: SampleMetadata | None) -> list[Task]:
    """Ingest -> resolve taxonomy -> build prior -> rank+rerank -> explain.

    The two taxonomy tasks exist ONLY when the user supplied a species or
    family: a prior built on a guessed organism would be fabrication, so
    planning drops both steps (and the rerank flag) when the metadata is
    absent, and the skip is logged and recorded on the graph instead of
    passing silently (ADR-0052).  Task outputs -- the resolution, the
    prior, the ranked candidates -- are the run's artifacts and are what
    dependent tasks and the share view consume.
    """
    metadata = sample_metadata
    has_taxon = bool(metadata and metadata.has_taxon_hint())
    solvent = metadata.extraction_solvent if metadata else None
    extract_id = metadata.extract_id if metadata else None
    # The isolation layer runs only when the user names an extract; a
    # guessed extract_id would attach somebody else's bench evidence to
    # this sample (ADR-0055).
    has_extract_hint = bool(extract_id)
    # Chromatography layers (ADR-0061..0066): each runs only when its
    # metadata was supplied; the missing ones are reported as skipped
    # ("layer skipped: no metadata") rather than silently absent.
    has_lc = bool(metadata and metadata.lc_method is not None)
    has_standards = bool(metadata and metadata.reference_standards)
    has_anchors = bool(metadata and metadata.anchor_series)
    has_chrom_layers = has_lc or has_standards or has_anchors
    # Metabolomics layers (ADR-0068..ADR-0072): the whole chain exists only
    # when a dataset reference was supplied; otherwise every layer is
    # reported skipped with its reason, never silently.
    has_metab = bool(metadata and metadata.metabolomics is not None)

    tasks: list[Task] = [
        Task(
            id="ingest-peaks",
            description="Parse the uploaded peak list (ingestion)",
            tool="io.parse_peak_csv",
            inputs={"path": "fixtures/sample_peaks.csv"},
        ),
    ]
    if has_extract_hint:
        tasks.append(
            Task(
                id="load-isolation-record",
                description=(
                    "Load the bench isolation record (fractions, TLC, "
                    "yield) for the supplied extract_id (ADR-0055)"
                ),
                tool="isolation.load_record",
                inputs={"extract_id": extract_id},
                depends_on=["ingest-peaks"],
                consumes=["ingest-peaks"],
            )
        )
    else:
        logger.info("isolation layer skipped: no record")
    if has_taxon:
        tasks.extend(
            [
                Task(
                    id="resolve-taxonomy",
                    description="Resolve the sample's species via the taxonomy service (GBIF, ITIS fallback)",
                    tool="taxonomy.resolve_species",
                    inputs={"name": metadata.species or ""},
                    depends_on=["ingest-peaks"],
                ),
                Task(
                    id="build-taxon-prior",
                    description="Query LOTUS/KNApSAcK/NPASS for compounds reported from the taxon",
                    tool="taxon_prior.build",
                    inputs={
                        "species": metadata.species,
                        "genus": metadata.genus,
                        "family": metadata.family,
                    },
                    depends_on=["resolve-taxonomy"],
                    consumes=["resolve-taxonomy"],
                ),
            ]
        )
    else:
        logger.info("taxon prior skipped: no metadata")
    isolation_deps = ["load-isolation-record"] if has_extract_hint else []
    if has_chrom_layers:
        tasks.append(
            Task(
                id="apply-chromatography-layers",
                description=(
                    "Run the chromatographic evidence layers (Level-1 home "
                    "library match, RT/RI reranking, UV chromophore, peak "
                    "purity, pharmacopoeial markers) over the ranked "
                    "candidates (ADR-0061..ADR-0066)"
                ),
                tool="chromatography.apply_layers",
                inputs={
                    "species": metadata.species if metadata else None,
                    "observed_rt_min": metadata.observed_rt_min
                    if metadata
                    else None,
                    "observed_uv_spectrum": metadata.observed_uv_spectrum
                    if metadata
                    else None,
                    "lc_method": metadata.lc_method.model_dump()
                    if has_lc
                    else None,
                    "reference_standards": [
                        s.model_dump()
                        for s in (metadata.reference_standards or [])
                    ],
                    "anchor_series": [
                        a.model_dump() for a in (metadata.anchor_series or [])
                    ],
                },
                depends_on=["elucidate"],
                consumes=["elucidate"],
            )
        )
    else:
        logger.info("chromatography layers skipped: no metadata")
    if has_metab:
        tasks.append(
            Task(
                id="metabolomics-preprocess",
                description=(
                    "Run the metabolomics preprocessing (raw Bruker FID -> "
                    "bucketed matrix, or MZmine-format CSV adaptation) "
                    "(ADR-0068, ADR-0069)"
                ),
                tool="metabolomics.preprocess",
                inputs={
                    "metabolomics_input": metadata.metabolomics.model_dump(),
                },
            )
        )
        tasks.append(
            Task(
                id="apply-metabolomics-layers",
                description=(
                    "Run the metabolomics evidence layers (PCA, supervised "
                    "model PLS-DA / O-PLS regression, VIP selection, STOCSY, "
                    "reporting checklist) over the preprocessed matrix "
                    "(ADR-0068..ADR-0072)"
                ),
                tool="metabolomics.apply_layers",
                inputs={
                    "metabolomics_input": metadata.metabolomics.model_dump(),
                    "sample_sheet": {},  # filled by the executor from the preprocess output
                },
                depends_on=["metabolomics-preprocess"],
                consumes=["metabolomics-preprocess"],
            )
        )
    else:
        logger.info("metabolomics layers skipped: no dataset reference")
    tasks.append(
        Task(
            id="elucidate",
            description=(
                "Rank structural candidates against the spectra, then rerank "
                "with the chromatography layers (ADR-0061..0066), the "
                "isolation evidence (ADR-0055), the taxon prior and "
                "solvent compatibility (ADR-0052)"
                if has_taxon and (has_extract_hint or has_chrom_layers)
                else (
                    "Rank structural candidates against the spectra, then "
                    "rerank with the isolation evidence (ADR-0055)"
                    if has_extract_hint
                    else (
                        "Rank structural candidates against the spectra, then "
                        "rerank with the chromatography layers "
                        "(ADR-0061..0066)"
                        if has_chrom_layers
                        else (
                            "Rank structural candidates against the spectra, "
                            "then rerank with the taxon prior and solvent "
                            "compatibility (ADR-0052)"
                            if has_taxon
                            else "Rank structural candidates against the spectra"
                        )
                    )
                )
            ),
            tool="elucidation.rank_candidates",
            inputs={
                "h_1d": [7.4, 6.9],
                "c_1d": [130.0, 115.0],
                "hsqc": [[6.9, 115.0]],
                "formula": "C16H22O3",
                "solvent": solvent,
                "apply_taxon_prior": has_taxon,
            },
            depends_on=(
                ["ingest-peaks", "build-taxon-prior", *isolation_deps]
                if has_taxon
                else ["ingest-peaks", *isolation_deps]
            ),
            consumes=["ingest-peaks"]
            + (["build-taxon-prior"] if has_taxon else [])
            + isolation_deps,
        )
    )
    if metadata and metadata.species:
        tasks.append(
            Task(
                id="literature-ground",
                description=(
                    "Search PubMed for the top candidate reported from the "
                    "sample species (compound AND species)"
                ),
                tool="literature.search_compound_in_species",
                inputs={"species_name": metadata.species, "max_results": 5},
                depends_on=["elucidate"],
                consumes=["elucidate"],
            )
        )
    tasks.extend(
        [
            Task(
                id="approve-claim",
                description=(
                    "HUMAN CHECKPOINT: review evidence (including taxon prior) "
                    "before any CONFIRMED identification claim"
                ),
                tool=None,
                requires_checkpoint=HumanCheckpoint.BEFORE_CONFIRMED_CLAIM,
                depends_on=(
                    [
                        "elucidate",
                        "literature-ground",
                        *(["apply-chromatography-layers"] if has_chrom_layers else []),
                    ]
                    if metadata and metadata.species
                    else ["elucidate", *(["apply-chromatography-layers"] if has_chrom_layers else [])]
                ),
                consumes=["elucidate"],
            ),
            Task(
                id="confirm-identity",
                description="Record the identification claim after approval",
                tool=None,
                depends_on=["approve-claim"],
                consumes=["approve-claim"],
            ),
            Task(
                id="explain-result",
                description="Generate a natural-language explanation of the evidence",
                tool="llm.explain",
                inputs={"evidence_graph": _EVIDENCE_GRAPH},
                depends_on=["confirm-identity"],
                consumes=["confirm-identity"],
            ),
        ]
    )
    return tasks

# Minimum evidence a workflow needs to be worth running at all: AT LEAST ONE
# of these modalities must have been observed.  Per-task pruning alone is not
# enough, because a template can keep a path that reaches no defensible
# conclusion -- a novelty scan on MS1-only data would still propose structures
# from an analogue's predicted shifts, with nothing observed to test them
# against.  A precursor m/z is deliberately absent everywhere: it filters
# candidates, it never identifies one (ADR-0014).
WORKFLOW_MODALITY_REQUIREMENTS: dict[WorkflowName, frozenset[ObservationModality]] = {
    "single_compound": frozenset(
        {ObservationModality.H_1D, ObservationModality.C_1D,
         ObservationModality.HSQC, ObservationModality.MS2}
    ),
    "compare_extracts": frozenset(),  # feature statistics; instrument-agnostic
    "novelty_scan": frozenset(
        {ObservationModality.H_1D, ObservationModality.C_1D,
         ObservationModality.HSQC, ObservationModality.MS2}
    ),
    "build_network": frozenset(
        {ObservationModality.HSQC, ObservationModality.MS2}
    ),
    "mechanism_hypothesis": frozenset({ObservationModality.HSQC}),
    "identify": frozenset(
        {ObservationModality.H_1D, ObservationModality.C_1D,
         ObservationModality.HSQC, ObservationModality.MS2}
    ),
    "reference_hsqc_identify": frozenset({ObservationModality.HSQC}),
}


# Tasks whose inputs are completed from a dependency's output before execution.
# Each entry maps an input key to (source_task_id, output_field).  Keeping this
# declarative means the engine has one auditable place where dataflow happens,
# rather than per-template glue code.  A source task that the graph does not
# contain (the identify workflow drops the taxonomy pair when the user supplied
# no species/family) leaves the input key absent on purpose.
_INPUT_WIRING: dict[str, dict[str, tuple[str, str]]] = {
    "hypothesize-mechanism": {"context_papers": ("literature-search", "papers")},
    "verify-grounding": {
        "text": ("hypothesize-mechanism", "hypothesis"),
        "context_papers": ("literature-search", "papers"),
    },
    "elucidate": {
        "taxon_prior": ("build-taxon-prior", "prior"),
        # ADR-0055: the isolation record arrives from load-isolation-record.
        # A source task the graph does not contain (no extract_id supplied)
        # leaves the input key absent on purpose -- the skip is visible.
        "isolation_record": ("load-isolation-record", "record"),
    },
    "literature-ground": {"compound_name": ("elucidate", "top_candidate_name")},
    "explain-result": {"evidence_graph": ("elucidate", "top_evidence_graph")},
}


def _species_aware_literature(tasks: list[Task], species: str) -> list[Task]:
    """Make a mechanism-hypothesis literature step species-aware (ADR-0052).

    ``search_compound_in_species`` narrows PubMed to (compound AND
    species), so the papers handed to ``llm.hypothesize`` as
    ``context_papers`` become "reported from THIS organism" evidence --
    exactly the context a mechanism hypothesis for a natural product
    needs, instead of a generic compound query.
    """
    for task in tasks:
        if task.id == "literature-search":
            task.tool = "literature.search_compound_in_species"
            task.inputs = {
                "compound_name": "andrographolide",  # the workflow's top candidate
                "species_name": species,
                "max_results": 5,
            }
            task.description = (
                "Retrieve literature for the top candidate reported from "
                f"{species} (PubMed compound AND species query)"
            )
    return tasks


def _missing_modality_requirement(
    task: Task, available: frozenset[ObservationModality]
) -> bool:
    """True when a task cannot run because none of its modalities was seen.

    A task with NO requirement is always runnable: checkpoints, statistics,
    and literature lookups do not depend on the instrument.
    """
    if not task.requires_modalities:
        return False
    return not (task.requires_modalities & available)


def _prune_by_modality(
    tasks: list[Task], available: frozenset[ObservationModality]
) -> tuple[list[Task], list[PrunedTask]]:
    """Drop tasks the supplied modalities cannot support, then rewire.

    Dependents of a pruned task inherit its surviving ancestors, so the
    graph stays connected and topologically valid.  A dependent that
    depended on a pruned task ONLY is itself pruned, because its input no
    longer exists.

    This is a planning decision, not a runtime failure: the caller can show
    the user exactly which steps were skipped and why, before anything runs.
    """
    original_graph = {task.id: list(task.depends_on) for task in tasks}
    removed: dict[str, PrunedTask] = {}
    kept: list[Task] = []
    for task in tasks:
        if _missing_modality_requirement(task, available):
            provided = ", ".join(
                sorted(m.value for m in task.requires_modalities)
            )
            seen = ", ".join(sorted(m.value for m in available)) or "none"
            removed[task.id] = PrunedTask(
                id=task.id,
                description=task.description,
                reason=(
                    f"requires {provided}, which was not observed "
                    f"(supplied: {seen}); this step cannot be run on the "
                    f"available evidence"
                ),
            )
        else:
            kept.append(task)

    if not removed:
        return kept, []

    # A task that CONSUMES a removed task's output cannot run: the input is
    # gone.  This cascades, so iterate to a fixpoint before rewiring.
    changed = True
    while changed:
        changed = False
        for task in list(kept):
            lost = [dep for dep in task.consumes if dep in removed]
            if lost:
                removed[task.id] = PrunedTask(
                    id=task.id,
                    description=task.description,
                    reason=(
                        f"its input comes from {', '.join(sorted(lost))}, "
                        "which the supplied modalities made impossible"
                    ),
                )
                kept.remove(task)
                changed = True
                continue
            if task.consumes_any and all(
                dep in removed for dep in task.consumes_any
            ):
                removed[task.id] = PrunedTask(
                    id=task.id,
                    description=task.description,
                    reason=(
                        "it needs at least one of "
                        f"{', '.join(sorted(task.consumes_any))}, and the "
                        "supplied modalities ruled out all of them"
                    ),
                )
                kept.remove(task)
                changed = True

    if not kept:
        raise InvalidInputError(
            "the supplied modalities cannot support any step of this workflow",
            context={
                "modalities": sorted(m.value for m in available),
                "pruned": sorted(removed),
            },
        )

    # Rewire: a dependency that SURVIVES stays a dependency.  Only a removed
    # dependency is replaced by its own surviving ancestors, transitively, so
    # ordering is preserved through the gap.
    ancestors_cache: dict[str, list[str]] = {}

    def surviving_ancestors(task_id: str, seen: set[str] | None = None) -> list[str]:
        """Surviving tasks that the removed ``task_id`` depended on."""
        if task_id in ancestors_cache:
            return ancestors_cache[task_id]
        seen = seen or set()
        if task_id in seen:  # pragma: no cover - templates are acyclic
            return []
        seen.add(task_id)
        result: list[str] = []
        for dep in original_graph[task_id]:
            if dep in removed:
                result.extend(surviving_ancestors(dep, seen))
            elif dep not in result:
                result.append(dep)
        ancestors_cache[task_id] = result
        return result

    for task in kept:
        rewired: list[str] = []
        for dep in original_graph[task.id]:
            if dep in removed:
                rewired.extend(surviving_ancestors(dep))
            else:
                rewired.append(dep)
        task.depends_on = list(dict.fromkeys(rewired))
    return kept, list(removed.values())


class PlannerAgent:
    """Decomposes goals into validated task graphs (deterministic; no LLM)."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def plan(
        self,
        *,
        run_id: str,
        workflow: WorkflowName,
        dry_run: bool,
        modalities: frozenset[ObservationModality] | None = None,
        sample_metadata: SampleMetadata | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> TaskGraph:
        """Build the task graph for one workflow.

        ``modalities`` names what the user actually observed.  When it is
        omitted or empty, planning is unconstrained: every template step is
        kept, matching the historical behaviour for callers that have not
        declared their inputs.  When it IS supplied, steps whose required
        modality is absent are pruned, dependents are rewired onto the
        nearest surviving ancestor, and each pruning is recorded on the
        graph with its reason (ADR-0050) -- so a user with only NMR data
        gets an NMR workflow that says what it could not do, instead of a
        fused workflow that fails at the SIRIUS step or, worse, silently
        reports success without the mass constraint.

        ``sample_metadata`` carries the biological provenance (species/
        genus/family, extraction solvent) that drives the identify
        workflow's taxonomy steps (ADR-0052).  Without a species or
        family the taxonomy tasks are simply not planned and the skip is
        logged -- an unknown organism is not an error, but a fabricated
        lineage would be.
        """
        if workflow == "reference_hsqc_identify":
            spec = self.registry.get_tool("reference_hsqc.rank")
            validated = spec.input_schema.model_validate(parameters or {})
            tasks = [
                Task(
                    id="rank-reference-hsqc",
                    description="Rank formula-compatible structures against measured HSQC references",
                    tool="reference_hsqc.rank",
                    inputs=validated.model_dump(mode="json"),
                    requires_modalities=frozenset({ObservationModality.HSQC}),
                ),
                Task(
                    id="review-reference-hsqc",
                    description="HUMAN CHECKPOINT: review reference ranking; no confirmed identity",
                    depends_on=["rank-reference-hsqc"],
                    consumes=["rank-reference-hsqc"],
                    requires_checkpoint=HumanCheckpoint.BEFORE_CONFIRMED_CLAIM,
                ),
            ]
        elif workflow == "identify":
            tasks = _identify_tasks(sample_metadata)
        else:
            tasks = _TEMPLATES[workflow]()
            if workflow == "mechanism_hypothesis" and sample_metadata and sample_metadata.species:
                tasks = _species_aware_literature(tasks, sample_metadata.species)
        # Planning-time validation: every referenced tool must exist.
        for task in tasks:
            if task.tool is not None:
                self.registry.get_tool(task.tool)

        pruned: list[PrunedTask] = []
        if modalities:
            required = WORKFLOW_MODALITY_REQUIREMENTS[workflow]
            if required and not (required & modalities):
                raise InvalidInputError(
                    f"workflow '{workflow}' needs at least one of "
                    f"{', '.join(sorted(m.value for m in required))}, none of "
                    f"which was supplied (supplied: "
                    f"{', '.join(sorted(m.value for m in modalities))})",
                    context={
                        "workflow": workflow,
                        "required_any_of": sorted(m.value for m in required),
                        "supplied": sorted(m.value for m in modalities),
                    },
                )
            tasks, pruned = _prune_by_modality(tasks, modalities)

        return TaskGraph(
            run_id=run_id,
            workflow=workflow,
            dry_run=dry_run,
            tasks=tasks,
            modalities=sorted(m.value for m in (modalities or frozenset())),
            pruned=pruned,
            evidence_gap=single_family_gap(modalities) if modalities else None,
        )

    def replan_after_failure(
        self, graph: TaskGraph, failed_task_id: str
    ) -> TaskGraph:
        """Mark the failed task FAILED and its transitive dependents SKIPPED.

        A new graph is returned; the original is untouched (the caller can
        audit both).  Re-planning with alternative tools is the response of
        later steps; for now failure is explicit, never silent.
        """
        task = graph.task_by_id(failed_task_id)
        task.status = TaskStatus.FAILED
        changed = True
        while changed:
            changed = False
            for candidate in graph.tasks:
                if candidate.status != TaskStatus.PENDING:
                    continue
                if any(
                    graph.task_by_id(dep).status
                    in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                    for dep in candidate.depends_on
                ):
                    candidate.status = TaskStatus.SKIPPED
                    changed = True
        return graph


# ---------------------------------------------------------------------------
# Workflow engine
# ---------------------------------------------------------------------------


def _papers_from_task_outputs(graph: TaskGraph, task: Task) -> list[PaperHit]:
    """Retrieve the papers a Tier-3 task was given, for the grounding audit.

    Deliberately reads the graph's own recorded outputs (not the model's), so
    the gate checks the text against the evidence that was actually supplied.
    """
    papers: list[PaperHit] = []
    source_id = _INPUT_WIRING.get(task.id, {}).get("context_papers", (None, None))[0]
    if source_id is None:
        # Fall back to any completed dependency that produced papers.
        candidates = [
            graph.task_by_id(dep)
            for dep in task.depends_on
            if graph.task_by_id(dep).output is not None
        ]
    else:
        candidates = [graph.task_by_id(source_id)]

    for source in candidates:
        for raw in (source.output or {}).get("papers", []) or []:
            try:
                papers.append(PaperHit.model_validate(raw))
            except Exception:
                continue
    return papers


@dataclass
class EngineResult:
    """Outcome of one engine pass over a task graph.

    ``running`` is used only for the durable pre-execution record persisted
    before the engine starts; the engine itself returns a terminal status.
    """
    status: Literal["running", "complete", "failed", "awaiting_review"]
    awaiting_task_id: str | None = None
    failed_task_id: str | None = None
    audit_entries: int = 0
    cost_estimate_usd: float = 0.0
    wall_seconds: float = 0.0


class WorkflowEngine:
    """Executes task graphs under the platform's honesty rules.

    Per task, in dependency order:

    1. Skip if already COMPLETE; skip (subtree) if a dependency FAILED
       or was SKIPPED.
    2. If the task gates a checkpoint that is not approved, mark it
       AWAITING_APPROVAL and pause the run (status ``awaiting_review``).
    3. Enforce tool tiering against the execution context; a violation
       fails the task (and skips its subtree) -- it is never bypassed.
    4. Delegate to the executor (mock locally, real service client in the
       cloud plane); audit-log the call with input/output hashes.
    5. Tier-3 outputs must pass QA/Critic verification BEFORE they are
       persisted (``enforce_output_persistence``).
    """

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        audit: ChainedAuditLogger,
        qa_critic: QACriticAgent | None = None,
    ) -> None:
        self.registry = registry
        self.executor = executor
        self.audit = audit
        self.qa_critic = qa_critic

    def _resolve_inputs(self, graph: TaskGraph, task: Task) -> dict[str, Any]:
        """Fill an task's declared inputs from completed dependencies.

        Only the keys in ``_INPUT_WIRING`` are injected; everything else is
        left exactly as planned.  A dependency that did not produce the
        expected field leaves the key absent, and the tool's input schema then
        rejects the call -- failing loudly rather than running on stale or
        fabricated evidence.  A wiring SOURCE the graph does not contain
        (the identify workflow plans no taxonomy tasks without metadata)
        is treated the same way: the key stays absent and the tool decides
        what an absent prior means.
        """
        inputs = dict(task.inputs)
        known_ids = {known.id for known in graph.tasks}
        for key, (source_task_id, field) in _INPUT_WIRING.get(task.id, {}).items():
            if source_task_id not in known_ids:
                continue
            source = graph.task_by_id(source_task_id)
            if source.output is not None and field in source.output:
                inputs[key] = source.output[field]
        return inputs

    def run(
        self,
        graph: TaskGraph,
        context: ExecutionContext,
    ) -> EngineResult:
        self.executor.preflight([t.tool for t in graph.tasks if t.tool is not None])
        started = time.monotonic()
        self.audit.log_event(
            run_id=graph.run_id,
            event="run_started",
            detail={
                "workflow": graph.workflow,
                "dry_run": graph.dry_run,
                "actor": context.actor,
            },
        )

        awaiting_task: str | None = None
        failed_task: str | None = None
        cost_usd = 0.0
        audit_start = len(self.audit.records())
        budget = context.execution_budget_seconds

        for task in graph.tasks:
            # Cooperative execution budget: stop between tasks when the
            # wall-clock bound is exceeded. A single long tool call can
            # overrun it; that remainder is documented, not silent.
            if budget is not None and time.monotonic() - started > budget:
                task.status = TaskStatus.FAILED
                task.error = "execution budget exceeded"
                task.verified = False
                failed_task = task.id
                self.audit.log_event(
                    run_id=graph.run_id,
                    event="execution_budget_exceeded",
                    detail={"task_id": task.id, "budget_seconds": budget},
                )
                break
            if task.status == TaskStatus.COMPLETE:
                continue
            if task.status in (TaskStatus.SKIPPED, TaskStatus.FAILED):
                continue

            # Subtree propagation from an earlier failure in this pass
            if any(
                graph.task_by_id(dep).status
                in (TaskStatus.FAILED, TaskStatus.SKIPPED)
                for dep in task.depends_on
            ):
                task.status = TaskStatus.SKIPPED
                continue

            # 2. Human-in-the-loop checkpoint gate
            if (
                task.requires_checkpoint is not None
                and not context.checkpoint_approved(task.requires_checkpoint)
            ):
                task.status = TaskStatus.AWAITING_APPROVAL
                awaiting_task = task.id
                self.audit.log_event(
                    run_id=graph.run_id,
                    event="checkpoint_requested",
                    detail={
                        "task_id": task.id,
                        "checkpoint": str(task.requires_checkpoint),
                    },
                )
                break

            # Checkpoint gate that IS approved: record the approval
            if task.requires_checkpoint is not None:
                self.audit.log_event(
                    run_id=graph.run_id,
                    event="checkpoint_approved",
                    detail={
                        "task_id": task.id,
                        "checkpoint": str(task.requires_checkpoint),
                        "actor": context.actor,
                    },
                )
                task.status = TaskStatus.COMPLETE
                task.result_summary = (
                    f"checkpoint {task.requires_checkpoint} approved by "
                    f"{context.actor}"
                )
                continue

            # Gate/no-tool bookkeeping task: completes once its deps are done
            if task.tool is None:
                task.status = TaskStatus.COMPLETE
                task.result_summary = "bookkeeping step recorded"
                continue

            # 3-5. Tool task: tiering, delegation, verification, audit
            try:
                spec = self.registry.get_tool(task.tool)
                enforce_tiering(spec, context)
                resolved_inputs = self._resolve_inputs(graph, task)
                output = self.executor.execute(task.tool, resolved_inputs)
            except ToolTierViolationError as exc:
                task.status = TaskStatus.FAILED
                task.error = str(exc)
                task.verified = False
                failed_task = task.id
                self.audit.log_event(
                    run_id=graph.run_id,
                    event="tier_violation",
                    detail={"task_id": task.id, "tool": task.tool, "error": str(exc)},
                )
                break
            except Exception as exc:  # tool failure: explicit, never silent
                task.status = TaskStatus.FAILED
                task.error = f"{type(exc).__name__}: {exc}"
                failed_task = task.id
                self.audit.log_event(
                    run_id=graph.run_id,
                    event="task_failed",
                    detail={"task_id": task.id, "tool": task.tool, "error": str(exc)},
                )
                break

            input_hash = sha256_dict(resolved_inputs)
            # mode="json": outputs may carry datetimes (isolation records,
            # ADR-0055) that the raw dump cannot canonicalise.
            output_hash = sha256_dict(output.model_dump(mode="json"))
            verified: bool | None = None
            if spec.tier is ToolTier.TIER_3_LLM_GENERATED:
                if self.qa_critic is None:
                    task.status = TaskStatus.FAILED
                    task.error = (
                        "Tier-3 output produced with no QA/Critic agent "
                        "registered; refusing to persist"
                    )
                    failed_task = task.id
                    self.audit.log_event(
                        run_id=graph.run_id,
                        event="tier_violation",
                        detail={"task_id": task.id, "tool": task.tool},
                    )
                    break
                # The grounding gate needs the SAME evidence the model saw.
                evidence = _papers_from_task_outputs(graph, task)
                verification = self.qa_critic.verify(
                    task.tool, output, context_papers=evidence
                )
                try:
                    enforce_output_persistence(spec, verified=verification.passed)
                except ToolTierViolationError as exc:
                    task.status = TaskStatus.FAILED
                    task.error = str(exc)
                    task.verified = False
                    failed_task = task.id
                    self.audit.log_event(
                        run_id=graph.run_id,
                        event="verification_rejected",
                        detail={
                            "task_id": task.id,
                            "tool": task.tool,
                            "checks": [
                                c.to_dict() for c in verification.checks
                            ],
                        },
                    )
                    break
                verified = True

            self.audit.log_tool_call(
                run_id=graph.run_id,
                agent="orchestrator",
                tool=task.tool,
                tier=spec.tier,
                input_hash=input_hash,
                output_hash=output_hash,
                verified=verified,
                metadata={
                    "task_id": task.id,
                    "dry_run": graph.dry_run,
                    **self.executor.provenance(task.tool),
                },
            )
            task.status = TaskStatus.COMPLETE
            task.verified = verified
            task.output = output.model_dump(mode="json")
            task.result_summary = f"{spec.output_schema.__name__} produced"
            cost_usd += spec.cost_estimate_usd()

        audit_entries = len(self.audit.records()) - audit_start

        if awaiting_task is not None:
            status: Literal["complete", "failed", "awaiting_review"] = (
                "awaiting_review"
            )
        elif failed_task is not None or any(
            t.status == TaskStatus.FAILED for t in graph.tasks
        ):
            status = "failed"
            failed_task = failed_task or next(
                t.id for t in graph.tasks if t.status == TaskStatus.FAILED
            )
        else:
            status = "complete"

        self.audit.log_event(
            run_id=graph.run_id,
            event="run_finished",
            detail={
                "status": status,
                "task_counts": graph.counts(),
                "cost_estimate_usd": round(cost_usd, 8),
            },
        )
        return EngineResult(
            status=status,
            awaiting_task_id=awaiting_task,
            failed_task_id=failed_task,
            audit_entries=audit_entries + 1,  # + run_finished event
            cost_estimate_usd=round(cost_usd, 8),
            wall_seconds=round(time.monotonic() - started, 4),
        )
