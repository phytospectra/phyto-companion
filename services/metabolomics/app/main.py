"""FastAPI entrypoint for the metabolomics service.

Local plane: mock feature detection, mock pathway database, no external
calls.  Every response carries a provenance_manifest_id recording the
pipeline applied.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import InvalidInputError
from phyto_shared.provenance import ProvenanceBuilder, sha256_dict

from . import __version__
from .differential_analysis import compare_groups, dose_response_analysis, time_course_analysis, volcano_plot_data
from .feature_detection import DetectParams, FeatureTable, get_detector
from .multi_block_fusion import mfa, consensus_pca, permutation_test_fusion, pls_da_concat
from .normalization import flag_low_quality, normalize, qc_correction
from .pathway_enrichment import enrich_kegg, map_to_compounds
from .qc_monitor import qc_alerts, qc_report, suggest_rerun
from .statistics import (
    biomarker_candidates,
    fdr_correction,
    pca,
    pls_da,
    random_forest_classifier,
    univariate_test,
)

app = FastAPI(
    title="PhytoCompanion Metabolomics Service",
    version=__version__,
    description="Feature detection, normalization, statistics, fusion, enrichment, QC.",
)


class _Runtime:
    def __init__(self) -> None:
        self.detector = get_detector()


@lru_cache(maxsize=1)
def get_runtime() -> _Runtime:
    return _Runtime()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DetectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mzml_filenames: list[str] = Field(..., min_length=1)
    params: DetectParams | None = None


class DetectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    provenance_manifest_id: str


class NormalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class TableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    provenance_manifest_id: str


class QCRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    qc_samples: list[str]


class StatisticsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    labels: dict[str, str]
    methods: list[str] = Field(default_factory=lambda: ["pca"])
    n_components: int = 3
    n_permutations: int = 100


class DifferentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: FeatureTable
    groups: dict[str, list[str]]
    reference_group: str
    method: str = "welch"


class BlocksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocks: dict[str, list[list[float]]]
    labels: list[str]
    n_components: int = 3
    test: str = "mfa"


class EnrichRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_ids: list[str]
    organism: str = "hsa"
    background: list[str] | None = None
    compound_map: dict[str, str] | None = None


def _manifest(payload: BaseModel, notes: list[str]) -> str:
    builder = (
        ProvenanceBuilder()
        .with_input_hash("request", sha256_dict(payload.model_dump()))
        .with_model_version("metabolomics-service", __version__)
        .with_environment()
    )
    return builder.with_parameters({"pipeline": notes}).build().manifest_hash


def _labels_for(table: FeatureTable, labels: dict[str, str]) -> dict[str, str]:
    known = {sample.sample_id for sample in table.samples}
    missing = [s for s in labels if s not in known]
    if missing:
        raise HTTPException(
            status_code=422, detail=f"labels reference unknown samples: {missing}"
        )
    unlabeled = known - set(labels)
    if unlabeled:
        raise HTTPException(
            status_code=422, detail=f"unlabeled samples present: {sorted(unlabeled)}"
        )
    return labels


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "metabolomics",
        "version": __version__,
        "detector": type(get_runtime().detector).__name__,
    }


@app.post("/metabolomics/detect", response_model=DetectResponse)
def detect(request: DetectRequest) -> DetectResponse:
    from pathlib import Path

    runtime = get_runtime()
    paths = [Path(name) for name in request.mzml_filenames]
    try:
        table = runtime.detector.detect(paths, request.params or DetectParams())
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DetectResponse(
        table=table,
        provenance_manifest_id=_manifest(request, table.provenance_notes),
    )


@app.post("/metabolomics/normalize", response_model=TableResponse)
def normalize_endpoint(request: NormalizeRequest) -> TableResponse:
    try:
        table = normalize(request.table, request.method, request.params)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TableResponse(
        table=table,
        provenance_manifest_id=_manifest(request, table.provenance_notes),
    )


@app.post("/metabolomics/qc", response_model=TableResponse | dict)
def qc_endpoint(request: QCRequest) -> dict[str, Any]:
    try:
        corrected = qc_correction(request.table, request.qc_samples)
        report = qc_report(corrected, request.qc_samples)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "table": corrected,
        "report": report.model_dump(),
        "alerts": [alert.model_dump() for alert in qc_alerts(report)],
        "flagged_features": flag_low_quality(corrected),
        "suggest_rerun": suggest_rerun(report),
        "provenance_manifest_id": _manifest(request, corrected.provenance_notes),
    }


@app.post("/metabolomics/statistics")
def statistics_endpoint(request: StatisticsRequest) -> dict[str, Any]:
    labels = _labels_for(request.table, request.labels)
    result: dict[str, Any] = {}
    if "pca" in request.methods:
        result["pca"] = pca(request.table, request.n_components).model_dump()
    try:
        if "pls_da" in request.methods:
            result["pls_da"] = pls_da(
                request.table, labels, request.n_components, request.n_permutations
            ).model_dump()
        if "random_forest" in request.methods:
            result["random_forest"] = random_forest_classifier(
                request.table, labels
            ).model_dump()
        if "univariate" in request.methods:
            univariate = univariate_test(request.table, labels)
            adjusted = [float(v) for v in fdr_correction(univariate.p_value)]
            result["univariate"] = univariate.model_dump()
            result["univariate_fdr"] = adjusted
            if "pls_da" in result:
                result["biomarkers"] = {
                    key: [rank.model_dump() for rank in ranks]
                    for key, ranks in biomarker_candidates(
                        univariate,
                        pls_da(request.table, labels, request.n_components,
                               min(request.n_permutations, 20)),
                    ).items()
                }
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["provenance_manifest_id"] = _manifest(request, request.methods)
    return result


@app.post("/metabolomics/differential")
def differential_endpoint(request: DifferentialRequest) -> dict[str, Any]:
    try:
        result = compare_groups(
            request.table, request.groups, request.reference_group, request.method
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "result": result.model_dump(),
        "volcano": volcano_plot_data(result),
        "provenance_manifest_id": _manifest(request, [request.method]),
    }


@app.post("/metabolomics/fusion")
def fusion_endpoint(request: BlocksRequest) -> dict[str, Any]:
    blocks = {
        name: np.asarray(block, dtype=float)
        for name, block in request.blocks.items()
    }
    labels = np.asarray(request.labels)
    result: dict[str, Any] = {}
    try:
        if request.test in ("mfa", "all"):
            result["mfa"] = mfa(blocks, request.n_components).model_dump()
        if request.test in ("consensus_pca", "all"):
            result["consensus_pca"] = consensus_pca(blocks, request.n_components).model_dump()
        if request.test in ("pls_da_concat", "all"):
            result["pls_da_concat"] = pls_da_concat(blocks, labels, request.n_components)
        if request.test in ("permutation", "all"):
            result["permutation"] = permutation_test_fusion(
                blocks, labels, n_permutations=50
            ).model_dump()
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["provenance_manifest_id"] = _manifest(request, [request.test])
    return result


@app.post("/metabolomics/enrich")
def enrich_endpoint(request: EnrichRequest) -> dict[str, Any]:
    try:
        enrichment = enrich_kegg(
            request.feature_ids, request.organism, request.background
        )
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.compound_map:
        enrichment = map_to_compounds(enrichment, request.compound_map)
    return {
        "enrichment": enrichment.model_dump(),
        "provenance_manifest_id": _manifest(request, [enrichment.test]),
    }
