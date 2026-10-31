"""FastAPI entrypoint for the pharmacology service.

Every response carries the mandated disclaimer and a
provenance_manifest_id.  Fails closed on missing knowledge (principle 5):
honest errors, never plausible-sounding guesses.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import (
    CloudServiceError,
    InsufficientEvidenceError,
    InvalidInputError,
)
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

from auth.config import AuthConfig
from auth.install import install_auth

from . import __version__
from .admet_prediction import ADMETResult, predict_admet
from .knowledge_graph import get_knowledge_graph
from .literature_mining import PubMedHit, query_compound_literature, search_pubmed
from .runtime_config import PharmacologyConfig
from .network_pharmacology import (
    MechanismHypothesis,
    build_network,
    hub_analysis,
    mechanism_hypothesis,
    module_detection,
)
from .synergy_analysis import SynergyResult, combination_index, predict_synergy
from .target_prediction import (
    TargetPrediction,
    docking_target_prediction,
    ml_target_prediction,
    pharmacophore_target_prediction,
    similarity_target_prediction,
)

DISCLAIMER = (
    "These are computational hypotheses, not experimental conclusions. "
    "Experimental validation is required."
)

router = APIRouter()


class _Runtime:
    """Built from the validated config: real mode never reaches networkx.

    Constructing the runtime validates the configuration eagerly
    (``PharmacologyConfig.validate``), so a cloud deployment without
    ``NEO4J_URI`` fails at startup instead of serving the in-memory
    graph (with its mock traditional-use records) on first request
    (ADR-0078 rule). Note the Neo4j backend itself is Step 8 wiring:
    real mode currently fails closed there too.
    """

    def __init__(self, config: PharmacologyConfig | None = None) -> None:
        self.config = (config or PharmacologyConfig.from_env()).validate()
        self.kg = get_knowledge_graph(config=self.config)


@lru_cache(maxsize=1)
def get_runtime() -> _Runtime:
    return _Runtime()


def _manifest(payload: BaseModel, notes: list[str] | None = None) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("pharmacology-service", __version__)
        .with_environment()
        .with_parameters({"pipeline": notes or []})
        .build()
        .manifest_hash
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TargetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_iks: list[str] = Field(..., min_length=1)
    compound_smiles: dict[str, str] = Field(default_factory=dict)
    include_ml: bool = False


class TargetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predictions: list[TargetPrediction]
    gaps: list[str]
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class NetworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_iks: list[str] = Field(..., min_length=1)
    predictions: list[TargetPrediction]
    hub_method: str = "degree"


class NetworkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_nodes: int
    n_edges: int
    hubs: list[dict[str, Any]]
    modules: list[list[str]]
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class HypothesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_iks: list[str] = Field(..., min_length=1)
    predictions: list[TargetPrediction]
    compound: str


class HypothesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: MechanismHypothesis
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class SynergyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_iks: list[str] = Field(..., min_length=2)
    predictions: list[TargetPrediction]
    effects: list[float] | None = None
    single_effects: list[list[float]] | None = None


class SynergyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: SynergyResult
    combination_index: float | None = None
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compound_ik: str = Field(..., min_length=1)
    import_sources: list[str] = Field(default_factory=list)


class KnowledgeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: dict[str, Any]
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class LiteratureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    compound_ik: str | None = None
    context: str = "target"
    max_results: int = 20


class LiteratureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[PubMedHit]
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


class ADMETRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    smiles: str = Field(..., min_length=1)


class ADMETResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: ADMETResult
    disclaimer: str = DISCLAIMER
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/pharmacology/targets", response_model=TargetsResponse)
def targets(request: TargetsRequest) -> TargetsResponse:
    predictions: list[TargetPrediction] = []
    gaps: list[str] = []
    for compound_ik in request.compound_iks:
        predictions.extend(similarity_target_prediction(compound_ik))
    for compound_ik, smiles in request.compound_smiles.items():
        try:
            predictions.extend(
                pharmacophore_target_prediction(
                    smiles,
                    {
                        "P35354": ["Donor", "Aromatic"],
                        "P23219": ["Aromatic"],
                    },
                )
            )
        except CloudServiceError as exc:
            gaps.append(f"pharmacophore for {compound_ik}: {exc}")
        if request.include_ml:
            ml = ml_target_prediction(smiles)
            if not ml:
                gaps.append(
                    "ml target prediction: no pre-trained DTI model is "
                    "deployed in this environment"
                )
        try:
            docking_target_prediction(smiles, [])
        except CloudServiceError as exc:
            gaps.append(f"docking for {compound_ik}: {exc}")
    if not predictions and len(gaps) == 0:
        gaps.append(
            "no similarity knowledge for these compounds; no prediction made"
        )
    return TargetsResponse(
        predictions=predictions,
        gaps=gaps,
        provenance_manifest_id=_manifest(
            request, [p.method for p in predictions]
        ),
    )


@router.post("/pharmacology/network", response_model=NetworkResponse)
def network(request: NetworkRequest) -> NetworkResponse:
    per_compound: dict[str, list[TargetPrediction]] = {
        compound_ik: [p for p in request.predictions if p.compound_ik == compound_ik]
        for compound_ik in request.compound_iks
    }
    graph = build_network(request.compound_iks, per_compound)
    hubs = hub_analysis(graph, method=request.hub_method)  # type: ignore[arg-type]
    modules = module_detection(graph)
    return NetworkResponse(
        n_nodes=graph.number_of_nodes(),
        n_edges=graph.number_of_edges(),
        hubs=hubs.hubs,
        modules=modules.modules,
        provenance_manifest_id=_manifest(request, [request.hub_method]),
    )


@router.post("/pharmacology/hypothesis", response_model=HypothesisResponse)
def hypothesis(request: HypothesisRequest) -> HypothesisResponse:
    per_compound: dict[str, list[TargetPrediction]] = {
        compound_ik: [p for p in request.predictions if p.compound_ik == compound_ik]
        for compound_ik in request.compound_iks
    }
    graph = build_network(request.compound_iks, per_compound)
    try:
        result = mechanism_hypothesis(graph, request.compound)
    except InsufficientEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return HypothesisResponse(
        hypothesis=result, provenance_manifest_id=_manifest(request)
    )


@router.post("/pharmacology/synergy", response_model=SynergyResponse)
def synergy(request: SynergyRequest) -> SynergyResponse:
    per_compound: dict[str, list[TargetPrediction]] = {
        compound_ik: [p for p in request.predictions if p.compound_ik == compound_ik]
        for compound_ik in request.compound_iks
    }
    try:
        result = predict_synergy(request.compound_iks, per_compound)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    index = combination_index(request.effects, request.single_effects)
    return SynergyResponse(
        result=result,
        combination_index=index,
        provenance_manifest_id=_manifest(request),
    )


@router.post("/pharmacology/knowledge", response_model=KnowledgeResponse)
def knowledge(request: KnowledgeRequest) -> KnowledgeResponse:
    runtime = get_runtime()
    imported: dict[str, int] = {}
    for source in request.import_sources:
        if source == "chembl":
            imported[source] = runtime.kg.import_chembl(request.compound_ik)
        elif source == "kegg":
            imported[source] = runtime.kg.import_kegg()
        elif source == "reactome":
            imported[source] = runtime.kg.import_reactome()
        elif source == "lotus":
            imported[source] = runtime.kg.import_lotus(request.compound_ik)
        else:
            raise HTTPException(
                status_code=422, detail=f"unknown import source {source!r}"
            )
    context = runtime.kg.query_compound_context(request.compound_ik)
    context["imported"] = imported
    return KnowledgeResponse(
        context=context, provenance_manifest_id=_manifest(request)
    )


@router.post("/pharmacology/literature", response_model=LiteratureResponse)
def literature(request: LiteratureRequest) -> LiteratureResponse:
    if not request.query and not request.compound_ik:
        raise HTTPException(
            status_code=422, detail="provide query or compound_ik"
        )
    if request.compound_ik:
        hits = query_compound_literature(
            request.compound_ik, request.context
        )
    else:
        hits = search_pubmed(request.query, request.max_results)
    return LiteratureResponse(
        hits=hits, provenance_manifest_id=_manifest(request)
    )


@router.post("/pharmacology/admet", response_model=ADMETResponse)
def admet(request: ADMETRequest) -> ADMETResponse:
    return ADMETResponse(
        result=predict_admet(request.smiles),
        provenance_manifest_id=_manifest(request),
    )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config: AuthConfig | None = None) -> FastAPI:
    """Build the service app with the token layer installed.

    Middleware is installed before routes are added so no endpoint can be
    registered outside the metering boundary.
    """
    application = FastAPI(
        title="PhytoCompanion Pharmacology Service",
        version=__version__,
        description="Targets, pathways, network pharmacology, synergy, knowledge graph, literature, ADMET.",
    )
    cfg = install_auth(application, config)
    application.include_router(router)

    # Fail fast: a real-mode deployment without NEO4J_URI must refuse to
    # start (this runs at module import for the container entrypoint),
    # not serve the networkx mock on the first request. Dry-run (local
    # plane) skips validation by design.
    get_runtime()

    @application.get("/health")
    def health() -> dict[str, Any]:
        runtime = get_runtime()
        return {
            "status": "ok",
            "service": "pharmacology",
            "version": __version__,
            "knowledge_graph": type(runtime.kg).__name__,
            "kg_mode": runtime.config.mode,
            "dry_run": cfg.dry_run,
        }

    return application


app = create_app()
