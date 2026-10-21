"""FastAPI entrypoint for the elucidation service.

Local plane: mock ShiftMPNN predictor, mock structure database, mock
GNPS-free ranking -- no GPU, no credentials.  Cloud plane: Vertex AI
inference and COCONUT/NPAtlas/ChemSpider lookups behind the same
endpoints.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from rdkit import Chem

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.models import Candidate, CandidateRank, EvidenceGraph
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

from . import __version__
from .candidate_ranker import (
    ObservedEvidence,
    RankingConfig,
    parse_formula_string,
    rank_candidates,
)
from .evidence_graph import RichEvidenceGraph, build_evidence_graph
from .novel_structure_generator import generate_novel_candidates
from .shift_predictor import get_shift_predictor
from .site_localization import SiteLocalization, localize_site
from .structure_db import MockStructureDB

app = FastAPI(
    title="PhytoCompanion Elucidation Service",
    version=__version__,
    description="Shift prediction, matching, ranking, and novel structure generation.",
)


class _Runtime:
    def __init__(self) -> None:
        self.predictor = get_shift_predictor()
        self.db = MockStructureDB()


@lru_cache(maxsize=1)
def get_runtime() -> _Runtime:
    return _Runtime()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed: ObservedEvidence
    formula: str
    config: RankingConfig | None = None


class CandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranks: list[CandidateRank]
    provenance_manifest_id: str


class NovelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_formula: str
    parent_ik: str
    observed: ObservedEvidence
    enabled_edits: list[str] | None = None


class NovelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    novel_candidates: list[CandidateRank]
    provenance_manifest_id: str


class LocalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_ik: str
    novel_smiles: str
    observed: ObservedEvidence
    true_edit_site: int | None = None


class LocalizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    localization: SiteLocalization
    provenance_manifest_id: str


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: CandidateRank
    observed: ObservedEvidence | None = None


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_graph: RichEvidenceGraph
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(payload: BaseModel) -> str:
    return (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("elucidation-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    runtime = get_runtime()
    return {
        "status": "ok",
        "service": "elucidation",
        "version": __version__,
        "predictor": type(runtime.predictor).__name__,
        "db_size": len(runtime.db._candidates),
    }


@app.post("/elucidate/candidates", response_model=CandidatesResponse)
def elucidate_candidates(request: CandidatesRequest) -> CandidatesResponse:
    runtime = get_runtime()
    try:
        counts = parse_formula_string(request.formula)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    candidates = runtime.db.candidates_for_formula(counts)
    if not candidates:
        return CandidatesResponse(
            ranks=[], provenance_manifest_id=_manifest(request)
        )
    ranks = rank_candidates(
        request.observed, candidates, request.config, runtime.predictor
    )
    return CandidatesResponse(
        ranks=ranks, provenance_manifest_id=_manifest(request)
    )


@app.post("/elucidate/novel", response_model=NovelResponse)
def elucidate_novel(request: NovelRequest) -> NovelResponse:
    runtime = get_runtime()
    parent = runtime.db.candidate_by_inchikey(request.parent_ik)
    if parent is None:
        raise HTTPException(
            status_code=404, detail=f"unknown parent InChIKey {request.parent_ik}"
        )
    try:
        novel = generate_novel_candidates(
            request.target_formula,
            [parent],
            enabled_edits=request.enabled_edits,
            known_inchikeys=runtime.db.known_inchikeys(),
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ranks = (
        rank_candidates(request.observed, list(novel), None, runtime.predictor)
        if novel
        else []
    )
    return NovelResponse(
        novel_candidates=ranks, provenance_manifest_id=_manifest(request)
    )


@app.post("/elucidate/localize", response_model=LocalizeResponse)
def elucidate_localize(request: LocalizeRequest) -> LocalizeResponse:
    runtime = get_runtime()
    parent = runtime.db.candidate_by_inchikey(request.parent_ik)
    if parent is None:
        raise HTTPException(
            status_code=404, detail=f"unknown parent InChIKey {request.parent_ik}"
        )
    mol = Chem.MolFromSmiles(request.novel_smiles)
    if mol is None:
        raise HTTPException(status_code=422, detail="novel_smiles is not parseable")
    from .candidate_ranker import score_molecule
    from .hungarian_matcher import MatchResult

    baseline_score, _ = score_molecule(
        request.observed, parent.smiles, RankingConfig(), runtime.predictor,
        exact_mass=parent.exact_mass,
    )
    parent_match = MatchResult(
        score=baseline_score,
        coverage=0.0,
        n_observed=0,
        n_predicted=0,
        ranking_convention="lower_is_better",
    )
    from .novel_structure_generator import NovelCandidate
    from phyto_shared.models import MolecularFormula
    from .candidate_ranker import parse_formula_string
    from rdkit.Chem import rdMolDescriptors

    formula_text = rdMolDescriptors.CalcMolFormula(mol)
    formula = MolecularFormula(counts=parse_formula_string(formula_text))
    novel = NovelCandidate(
        id="novel-request",
        name="requested novel structure",
        smiles=request.novel_smiles,
        formula=formula,
        exact_mass=formula.exact_mass(),
        provenance="predicted",
        source="api request",
        parent_ik=request.parent_ik,
        edit_type="hydroxylation",  # inferred upstream; localization re-derives sites
        edit_site=None,
        generated_smiles=request.novel_smiles,
    )
    localization = localize_site(
        request.observed,
        parent,
        parent_match,
        novel,
        predictor=runtime.predictor,
        true_edit_site=request.true_edit_site,
    )
    return LocalizeResponse(
        localization=localization, provenance_manifest_id=_manifest(request)
    )


@app.post("/elucidate/evidence", response_model=EvidenceResponse)
def elucidate_evidence(request: EvidenceRequest) -> EvidenceResponse:
    graph = build_evidence_graph(request.rank, request.observed)
    return EvidenceResponse(
        evidence_graph=graph, provenance_manifest_id=_manifest(request)
    )
