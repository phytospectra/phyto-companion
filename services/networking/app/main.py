"""FastAPI entrypoint for the networking service.

Local plane: mock GNPS client, synchronous HSQC computation, no cloud
calls, no credentials.  Cloud plane: same endpoints behind the API
gateway with the real GNPS2 client.  Every response carries a
``provenance_manifest_id`` hashing the submitted payloads.
"""

from __future__ import annotations

import base64
import io
import uuid
from functools import lru_cache
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.provenance import ProvenanceBuilder, sha256_bytes

from . import __version__
from .bioactivity_networking import BioactivityNetwork, correlate_network
from .fused_network import FusedNetwork, cross_modal_annotation, fuse_networks
from .gnps_client import get_gnps_client
from .gnps_job_manager import GNPSJobManager
from .network_visualization import network_summary, to_cytoscape_json, to_graphml
from .nmr_networking import HSQCNetwork, HSQCSpectrum, build_hsqc_network

app = FastAPI(
    title="PhytoCompanion Networking Service",
    version=__version__,
    description="GNPS MS2 networking, HSQC networking, and their fusion.",
)


class _Runtime:
    def __init__(self) -> None:
        self.client = get_gnps_client()
        self.jobs = GNPSJobManager(client=self.client)


@lru_cache(maxsize=1)
def get_runtime() -> _Runtime:
    return _Runtime()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class MS2SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mgf_b64: str = Field(..., min_length=1)
    feature_table_b64: str | None = None
    supplementary_pairs_b64: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    network_type: Literal["classic", "fbmn", "iimn"] = "classic"


class MS2SubmitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_handle_id: str
    gnps_task_id: str
    params_hash: str
    status: str
    provenance_manifest_id: str


class MS2StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_handle_id: str
    gnps_task_id: str
    status: str
    n_nodes: int | None = None
    n_edges: int | None = None
    n_annotations: int | None = None
    network: dict[str, Any] | None = None
    provenance_manifest_id: str


class HSQCRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spectra: list[HSQCSpectrum] = Field(..., min_length=1)
    distance_threshold: float = Field(default=0.5, gt=0.0, le=1.0)


class HSQCResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: HSQCNetwork
    provenance_manifest_id: str


class FusedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ms2_job_id: str = Field(..., min_length=1)
    hsqc_network_b64: str = Field(..., min_length=1)
    compound_links: dict[str, str] = Field(default_factory=dict)
    propagate_cross_modal: bool = True


class FusedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: FusedNetwork
    overlap_report: dict[str, int]
    provenance_manifest_id: str


class BioactivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network_b64: str = Field(..., min_length=1)
    feature_abundances_b64: str = Field(..., min_length=1)
    bioactivities_b64: str = Field(..., min_length=1)


class BioactivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: BioactivityNetwork
    prioritized_nodes: list[str]
    provenance_manifest_id: str


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network_b64: str = Field(..., min_length=1)
    network_type: Literal["fused", "ms2", "bioactivity"] = "fused"


class ExportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: Any
    provenance_manifest_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode(b64: str) -> bytes:
    try:
        return base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid base64: {exc}") from exc


def _manifest(payload_label: str, *payloads: bytes) -> str:
    builder = ProvenanceBuilder()
    for label, payload in zip(payload_label.split("|"), payloads, strict=True):
        builder.with_input_hash(label, sha256_bytes(payload))
    return (
        builder.with_model_version("networking-service", __version__)
        .with_environment()
        .build()
        .manifest_hash
    )


def _decode_network(b64: str, network_type: str):
    import json

    raw = _decode(b64)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"network JSON invalid: {exc}") from exc
    try:
        if network_type == "fused":
            return FusedNetwork.model_validate(data)
        if network_type == "bioactivity":
            return BioactivityNetwork.model_validate(data)
        from .gnps_result_parser import ParsedNetwork

        return ParsedNetwork.model_validate(data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"network payload invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, Any]:
    runtime = get_runtime()
    return {
        "status": "ok",
        "service": "networking",
        "version": __version__,
        "gnps_client": type(runtime.client).__name__,
    }


@app.post("/networks/ms2", response_model=MS2SubmitResponse)
async def submit_ms2(request: MS2SubmitRequest) -> MS2SubmitResponse:
    runtime = get_runtime()
    mgf = _decode(request.mgf_b64)
    feature_table = _decode(request.feature_table_b64) if request.feature_table_b64 else None
    pairs = _decode(request.supplementary_pairs_b64) if request.supplementary_pairs_b64 else None
    if request.network_type != "classic" and not feature_table:
        raise HTTPException(
            status_code=422,
            detail=f"{request.network_type} networking requires feature_table_b64",
        )
    job_id = f"netjob-{uuid.uuid4().hex[:12]}"
    manifest_id = _manifest(
        "mgf|feature_table|supplementary_pairs",
        mgf,
        feature_table or b"",
        pairs or b"",
    )
    try:
        entry = await runtime.jobs.submit(
            job_id=job_id,
            network_type=request.network_type,
            mgf=mgf,
            feature_table=feature_table,
            supplementary_pairs=pairs,
            params=request.params,
            payload_sha=manifest_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Mock jobs complete immediately; refresh once so status is accurate.
    entry = await runtime.jobs.refresh(job_id)
    return MS2SubmitResponse(
        job_handle_id=job_id,
        gnps_task_id=entry.handle.task_id,
        params_hash=entry.handle.params_hash,
        status=entry.status.value,
        provenance_manifest_id=manifest_id,
    )


@app.get("/networks/ms2/{job_handle_id}", response_model=MS2StatusResponse)
async def ms2_status(job_handle_id: str) -> MS2StatusResponse:
    runtime = get_runtime()
    try:
        entry = await runtime.jobs.refresh(job_handle_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload: dict[str, Any] | None = None
    if entry.parsed is not None:
        payload = {
            "nodes": [node.model_dump() for node in entry.parsed.nodes],
            "edges": [edge.model_dump() for edge in entry.parsed.edges],
            "annotations": [
                annotation.model_dump() for annotation in (entry.results.annotations if entry.results else [])
            ],
        }
    return MS2StatusResponse(
        job_handle_id=job_handle_id,
        gnps_task_id=entry.handle.task_id,
        status=entry.status.value,
        n_nodes=len(entry.parsed.nodes) if entry.parsed else None,
        n_edges=len(entry.parsed.edges) if entry.parsed else None,
        n_annotations=len(entry.results.annotations) if entry.results else None,
        network=payload,
        provenance_manifest_id=entry.submitted_payload_sha,
    )


@app.post("/networks/hsqc", response_model=HSQCResponse)
def hsqc_network(request: HSQCRequest) -> HSQCResponse:
    network = build_hsqc_network(request.spectra, request.distance_threshold)
    payload = request.model_dump_json().encode()
    return HSQCResponse(
        network=network,
        provenance_manifest_id=_manifest("hsqc_request", payload),
    )


@app.post("/networks/fused", response_model=FusedResponse)
async def fused_network(request: FusedRequest) -> FusedResponse:
    runtime = get_runtime()
    try:
        entry = await runtime.jobs.refresh(request.ms2_job_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if entry.parsed is None:
        raise HTTPException(
            status_code=409,
            detail=f"MS2 job {request.ms2_job_id} has no results yet "
            f"(status {entry.status.value})",
        )
    try:
        hsqc = HSQCNetwork.model_validate_json(_decode(request.hsqc_network_b64))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid HSQC network: {exc}") from exc
    try:
        fused = fuse_networks(entry.parsed, hsqc, request.compound_links)
        if request.propagate_cross_modal:
            fused = cross_modal_annotation(fused)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from .fused_network import modality_overlap_report

    return FusedResponse(
        network=fused,
        overlap_report=modality_overlap_report(fused),
        provenance_manifest_id=_manifest(
            "ms2_graphml|hsqc_network|compound_links",
            entry.results.graphml.encode() if entry.results else b"",
            _decode(request.hsqc_network_b64),
            str(sorted(request.compound_links.items())).encode(),
        ),
    )


@app.post("/networks/bioactivity", response_model=BioactivityResponse)
def bioactivity(request: BioactivityRequest) -> BioactivityResponse:
    network = _decode_network(request.network_b64, "ms2")
    try:
        abundances = pd.read_csv(io.BytesIO(_decode(request.feature_abundances_b64)), index_col=0)
        activity_df = pd.read_csv(io.BytesIO(_decode(request.bioactivities_b64)), index_col=0)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid CSV: {exc}") from exc
    bioactivities = activity_df.iloc[:, 0]
    from phyto_shared.exceptions import InvalidInputError

    try:
        bio_network = correlate_network(network, abundances, bioactivities)
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    from .bioactivity_networking import prioritize_bioactive_nodes

    return BioactivityResponse(
        network=bio_network,
        prioritized_nodes=prioritize_bioactive_nodes(bio_network),
        provenance_manifest_id=_manifest(
            "network|feature_abundances|bioactivities",
            _decode(request.network_b64),
            _decode(request.feature_abundances_b64),
            _decode(request.bioactivities_b64),
        ),
    )


@app.post("/networks/export/cytoscape", response_model=ExportResponse)
def export_cytoscape(request: ExportRequest) -> ExportResponse:
    network = _decode_network(request.network_b64, request.network_type)
    return ExportResponse(
        payload=to_cytoscape_json(network),
        provenance_manifest_id=_manifest("network", _decode(request.network_b64)),
    )


@app.post("/networks/export/graphml", response_model=ExportResponse)
def export_graphml(request: ExportRequest) -> ExportResponse:
    network = _decode_network(request.network_b64, request.network_type)
    return ExportResponse(
        payload=to_graphml(network),
        provenance_manifest_id=_manifest("network", _decode(request.network_b64)),
    )


@app.post("/networks/summary", response_model=ExportResponse)
def export_summary(request: ExportRequest) -> ExportResponse:
    network = _decode_network(request.network_b64, request.network_type)
    return ExportResponse(
        payload=network_summary(network),
        provenance_manifest_id=_manifest("network", _decode(request.network_b64)),
    )
