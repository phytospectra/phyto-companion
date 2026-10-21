"""GNPS2 REST API client (async), deterministic mock, and factory.

The real client talks to the GNPS2 API (https://gnps2.org) and is used in
the CLOUD plane only.  The local plane always uses ``MockGNPSClient``,
which produces deterministic synthetic networks -- no network access, no
GNPS credentials, no Docker.

This module is the ONLY place in the networking service that issues HTTP
calls (design principle 4 of Step 4): everything else goes through
``GNPSClient`` / ``MockGNPSClient``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from phyto_shared.exceptions import CloudServiceError, InvalidInputError

__all__ = [
    "Annotation",
    "Cluster",
    "MNParams",
    "FBMNParams",
    "IIMNParams",
    "JobHandle",
    "JobStatus",
    "MNResults",
    "GNPSClient",
    "MockGNPSClient",
    "RateLimiter",
    "get_gnps_client",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared result models
# ---------------------------------------------------------------------------


class Annotation(BaseModel):
    """One library annotation attached to a network node."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    compound_name: str
    smiles: str | None = None
    inchikey: str | None = None
    library: str
    cosine: float = Field(..., ge=0.0, le=1.0)
    msi_level: int = Field(..., ge=1, le=4)


class Cluster(BaseModel):
    """A spectral cluster (connected component of similar spectra)."""

    model_config = ConfigDict(extra="forbid")

    cluster_id: str
    node_ids: list[str]
    consensus_spectrum_mz: list[float]
    consensus_spectrum_intensity: list[float]


class JobHandle(BaseModel):
    """Handle to a submitted GNPS job, reproducible via params_hash."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    params_hash: str


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MNResults(BaseModel):
    """Everything GNPS returns for one networking job."""

    model_config = ConfigDict(extra="forbid")

    graphml: str
    annotations: list[Annotation] = Field(default_factory=list)
    clusters: list[Cluster] = Field(default_factory=list)
    job_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# GNPS2 parameter models
#
# Field names follow the GNPS2 workflow parameter schema documented at
# https://gnps2.org (Molecular Networking / FBMN / IIMN workflow
# configuration pages).  Every field carries the GNPS2 default so a
# submission is fully reproducible from the params_hash.
# ---------------------------------------------------------------------------


class MNParams(BaseModel):
    """Classical molecular networking parameters (GNPS2 'Molecular Networking')."""

    model_config = ConfigDict(extra="forbid")

    precursor_tolerance: float = 0.02  # GNPS2: PRECURSOR_TOLERANCE (Da)
    fragment_tolerance: float = 0.02  # GNPS2: FRAGMENT_MASS_TOLERANCE (Da)
    min_cosine: float = Field(default=0.7, ge=0.0, le=1.0)  # MIN_COSINE
    minimum_matched_peaks: int = Field(default=6, ge=1)  # MIN_MATCHED_PEAKS
    network_top_k: int = Field(default=10, ge=1)  # TOP_K
    max_shift: float = Field(default=500.0, ge=0.0)  # MAXIMUM_SHIFT
    run_library_search: bool = True  # RUN_LIBRARY_SEARCH


class FBMNParams(MNParams):
    """Feature-based MN parameters (GNPS2 FBMN workflow, Nothias et al. 2020)."""

    feature_source: str = "mzmine3"  # FEATURE_SOURCE: mzmine3/xcms/opensauce/msdial
    annotate_features: bool = True  # ANNOTATE_FEATURES (ION_IDENTITY_NETWORKING-lite)


class IIMNParams(FBMNParams):
    """Ion identity MN parameters (GNPS2 IIMN, Schmid et al. 2021)."""

    max_ion_identity: int = Field(default=2, ge=1)  # MAX_IIN_LAYERS
    min_peak_integrity: float = Field(default=0.6, ge=0.0, le=1.0)  # PEAK_INTEGRITY


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Sliding-window async rate limiter (default 5 requests/second)."""

    def __init__(self, max_per_second: float = 5.0) -> None:
        self.max_per_second = max_per_second
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 1.0:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_per_second:
                    self._timestamps.append(now)
                    return
                wait_for = 1.0 - (now - self._timestamps[0])
            await asyncio.sleep(wait_for)


# ---------------------------------------------------------------------------
# Real client (cloud plane)
# ---------------------------------------------------------------------------


class GNPSClient:
    """Async GNPS2 REST client with retry, rate limiting, and provenance hashes.

    ``transport`` is injectable for tests (``httpx.MockTransport``); in
    production it stays ``None`` and httpx uses its default transport.
    """

    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 0.25

    def __init__(
        self,
        base_url: str = "https://gnps2.org/api",
        api_key: str | None = None,
        rate_limit_per_second: float = 5.0,
        timeout_seconds: float = 30.0,
        backoff_base_seconds: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.backoff_base_seconds = backoff_base_seconds
        self._api_key = api_key or os.environ.get("GNPS_API_KEY")
        if not self._api_key:
            logger.warning(
                "GNPSClient: no GNPS_API_KEY set; only public submissions "
                "are allowed and private-job endpoints will fail"
            )
        self.limiter = RateLimiter(rate_limit_per_second)
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    # -- plumbing -----------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """One request with 3-attempt exponential backoff on 5xx errors."""
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            await self.limiter.acquire()
            response = await self._client.request(method, path, **kwargs)
            if response.status_code < 500:
                if response.status_code >= 400:
                    raise CloudServiceError(
                        f"GNPS2 returned HTTP {response.status_code} for {path}",
                        context={"path": path, "status": response.status_code},
                    )
                return response
            last_error = CloudServiceError(
                f"GNPS2 server error HTTP {response.status_code} for {path}",
                context={"path": path, "status": response.status_code, "attempt": attempt},
            )
            if attempt < self.MAX_ATTEMPTS:
                await asyncio.sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))
        raise CloudServiceError(
            f"GNPS2 unreachable after {self.MAX_ATTEMPTS} attempts",
            context={"path": path, "last_error": str(last_error)},
        )

    @staticmethod
    def _params_hash(params: BaseModel, *payloads: bytes) -> str:
        digest = hashlib.sha256()
        digest.update(params.model_dump_json().encode())
        for payload in payloads:
            digest.update(payload)
        return digest.hexdigest()

    def _handle(self, task_id: str, params_hash: str) -> JobHandle:
        return JobHandle(
            task_id=task_id,
            submitted_at=datetime.now(timezone.utc),
            params_hash=params_hash,
        )

    # -- job lifecycle --------------------------------------------------------

    async def submit_mn_job(self, mgf: bytes, params: MNParams) -> JobHandle:
        params_hash = self._params_hash(params, mgf)
        response = await self._request(
            "POST",
            "/molecularnetworking/submit",
            files={"mgf": ("spectra.mgf", mgf, "text/plain")},
            data={"parameters": params.model_dump_json()},
        )
        return self._handle(response.json()["task_id"], params_hash)

    async def submit_fbmn_job(
        self, mgf: bytes, feature_table: bytes, params: FBMNParams
    ) -> JobHandle:
        params_hash = self._params_hash(params, mgf, feature_table)
        response = await self._request(
            "POST",
            "/featurebasedmolecularnetworking/submit",
            files={
                "mgf": ("spectra.mgf", mgf, "text/plain"),
                "feature_table": ("features.csv", feature_table, "text/csv"),
            },
            data={"parameters": params.model_dump_json()},
        )
        return self._handle(response.json()["task_id"], params_hash)

    async def submit_iimn_job(
        self,
        mgf: bytes,
        feature_table: bytes,
        supplementary_pairs: bytes,
        params: IIMNParams,
    ) -> JobHandle:
        params_hash = self._params_hash(params, mgf, feature_table, supplementary_pairs)
        response = await self._request(
            "POST",
            "/ionidentitynetworking/submit",
            files={
                "mgf": ("spectra.mgf", mgf, "text/plain"),
                "feature_table": ("features.csv", feature_table, "text/csv"),
                "supplementary_pairs": ("pairs.tsv", supplementary_pairs, "text/tab-separated-values"),
            },
            data={"parameters": params.model_dump_json()},
        )
        return self._handle(response.json()["task_id"], params_hash)

    async def get_job_status(self, handle: JobHandle) -> JobStatus:
        response = await self._request("GET", f"/jobs/{handle.task_id}/status")
        return JobStatus(response.json()["status"])

    async def fetch_results(self, handle: JobHandle) -> MNResults:
        response = await self._request("GET", f"/jobs/{handle.task_id}/results")
        payload = response.json()
        return MNResults(
            graphml=payload["graphml"],
            annotations=[Annotation(**a) for a in payload.get("annotations", [])],
            clusters=[Cluster(**c) for c in payload.get("clusters", [])],
            job_metadata=payload.get("job_metadata", {}),
        )

    async def cancel_job(self, handle: JobHandle) -> bool:
        response = await self._request(
            "POST", f"/jobs/{handle.task_id}/cancel"
        )
        return bool(response.json().get("cancelled", True))

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Deterministic mock (local plane)
# ---------------------------------------------------------------------------


# Mass differences (Da) that connect "structurally similar" spectra in the
# mock's peptide heuristic: common amino-acid residue masses plus water.
_MOCK_RESIDUE_DELTAS = (18.0106, 57.0215, 71.0371, 87.0320, 97.0528, 99.0684, 113.0841, 128.0950, 156.1011)
_MOCK_TOL_DA = 0.1

# Library compounds the mock hands out deterministically at mixed MSI
# levels and cosines, so tests exercise annotated/unannotated nodes and
# library hits at different confidences (design principle 5).
_MOCK_LIBRARY = (
    ("Cyclo(Pro-Val)", "CC(C)C(C(=O)N1CCCC1C(=O))NC(=O)C(N)Cc1ccccc1", "GNPS-PSEUDO-PEPTIDES", 0.95, 2),
    ("Leu-Leu", "CC(C)CC(C(=O)O)NC(=O)CC(C)C", "GNPS-PSEUDO-PEPTIDES", 0.82, 2),
    ("Phe-Gly", "NCC(=O)NC(Cc1ccccc1)C(=O)O", "GNPS-PSEUDO-PEPTIDES", 0.74, 3),
    ("Ala-Pro-Gly", "CC(=O)N1CCC[C@H]1C(=O)NC(C)C(=O)O", "GNPS-PSEUDO-PEPTIDES", 0.91, 2),
)


def _parse_mgf(mgf: bytes) -> list[dict[str, Any]]:
    spectra: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in mgf.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line == "BEGIN IONS":
            current = {"peaks": []}
        elif line == "END IONS":
            if current is not None:
                spectra.append(current)
            current = None
        elif current is not None:
            if line.startswith("PEPMASS="):
                current["precursor_mz"] = float(line.split("=", 1)[1].split()[0])
            elif line.startswith("SCANS") or line.startswith("SCAN="):
                current["scan"] = line.split("=", 1)[1].strip()
            elif line and line[0].isdigit():
                parts = line.split()
                current["peaks"].append((float(parts[0]), float(parts[1])))
    if not spectra:
        raise InvalidInputError("MGF contains no spectra", context={})
    for i, spectrum in enumerate(spectra):
        spectrum.setdefault("scan", str(i + 1))
        spectrum.setdefault("precursor_mz", 500.0)
    return spectra


def _synthetic_graphml(nodes: list[dict[str, Any]], edges: list[tuple[str, str, float]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<graphml xmlns=\"http://graphml.graphdrawing.org/xmlns\"",
        '         xmlns:cy="http://www.cytoscape.org/">',
        '  <key id="d0" for="node" attr.name="label" attr.type="string"/>',
        '  <key id="d1" for="node" attr.name="precursor mass" attr.type="double"/>',
        '  <key id="d2" for="edge" attr.name="cosine score" attr.type="double"/>',
        '  <key id="d3" for="edge" attr.name="matched peaks" attr.type="int"/>',
        "  <graph edgedefault=\"undirected\">",
    ]
    for node in nodes:
        lines.append(
            f'    <node id="{node["id"]}">'
            f'<data key="d0">{node["id"]}</data>'
            f'<data key="d1">{node["precursor_mz"]:.4f}</data>'
            "</node>"
        )
    for index, (source, target, cosine) in enumerate(edges):
        lines.append(
            f'    <edge id="e{index}" source="{source}" target="{target}">'
            f'<data key="d2">{cosine:.4f}</data>'
            f'<data key="d3">{6 if cosine > 0.8 else 4}</data>'
            "</edge>"
        )
    lines.append("  </graph>")
    lines.append("</graphml>")
    return "\n".join(lines)


class MockGNPSClient:
    """Deterministic, offline stand-in for ``GNPSClient``.

    For a given MGF, spectra whose precursor masses differ by one of the
    common amino-acid residue masses (within 0.1 Da) are connected -- a
    simple "structurally similar peptide" heuristic.  Annotations are
    handed out deterministically so every code path (annotated nodes,
    unannotated nodes, mixed confidence library hits) is exercised.
    """

    async def submit_mn_job(self, mgf: bytes, params: MNParams) -> JobHandle:
        return self._submit(mgf, params)

    async def submit_fbmn_job(
        self, mgf: bytes, feature_table: bytes, params: FBMNParams
    ) -> JobHandle:
        return self._submit(mgf, params)

    async def submit_iimn_job(
        self,
        mgf: bytes,
        feature_table: bytes,
        supplementary_pairs: bytes,
        params: IIMNParams,
    ) -> JobHandle:
        return self._submit(mgf, params)

    def _submit(self, mgf: bytes, params: MNParams) -> JobHandle:
        # Keep the parsed spectra so fetch_results can re-derive the same
        # deterministic network for this handle.
        self._last_spectra = _parse_mgf(mgf)
        digest = hashlib.sha256(mgf + params.model_dump_json().encode()).hexdigest()
        return JobHandle(
            task_id=f"MOCKTASK-{digest[:12]}",
            params_hash=digest,
        )

    async def get_job_status(self, handle: JobHandle) -> JobStatus:
        # Mock jobs complete instantly; polling still works for API flow.
        return JobStatus.COMPLETED

    async def fetch_results(self, handle: JobHandle) -> MNResults:
        spectra = getattr(self, "_last_spectra", None)
        if spectra is None:  # pragma: no cover - submit is always called first
            spectra = _parse_mgf(b"BEGIN IONS\nPEPMASS=500.0\n100.0 10.0\nEND IONS\n")
        nodes = [
            {"id": f"scan={spectrum['scan']}", "precursor_mz": spectrum["precursor_mz"]}
            for spectrum in spectra
        ]
        edges: list[tuple[str, str, float]] = []
        for i in range(len(spectra)):
            for j in range(i + 1, len(spectra)):
                delta = abs(
                    spectra[i]["precursor_mz"] - spectra[j]["precursor_mz"]
                )
                if any(abs(delta - residue) <= _MOCK_TOL_DA for residue in _MOCK_RESIDUE_DELTAS):
                    # deterministic pseudo-cosine in [0.6, 0.99]
                    seed = int(hashlib.sha256(f"{i}:{j}".encode()).hexdigest()[:6], 16)
                    cosine = 0.6 + (seed % 40) / 100.0
                    edges.append((nodes[i]["id"], nodes[j]["id"], cosine))

        annotations = []
        for index, node in enumerate(nodes):
            if index % 3 == 0:  # annotate every third node deterministically
                name, smiles, library, cosine, msi = _MOCK_LIBRARY[index % len(_MOCK_LIBRARY)]
                annotations.append(
                    Annotation(
                        node_id=node["id"],
                        compound_name=name,
                        smiles=smiles,
                        library=library,
                        cosine=cosine,
                        msi_level=msi,
                    )
                )

        clusters = self._clusters(nodes, edges, spectra)

        return MNResults(
            graphml=_synthetic_graphml(nodes, edges),
            annotations=annotations,
            clusters=clusters,
            job_metadata={
                "gnps_task_url": f"https://gnps2.org/task/{handle.task_id}",
                "parameters_hash": handle.params_hash,
                "engine": "MockGNPSClient (deterministic, synthetic data)",
            },
        )

    @staticmethod
    def _clusters(
        nodes: list[dict[str, Any]],
        edges: list[tuple[str, str, float]],
        spectra: list[dict[str, Any]],
    ) -> list[Cluster]:
        adjacency: dict[str, set[str]] = {node["id"]: set() for node in nodes}
        for source, target, _ in edges:
            adjacency[source].add(target)
            adjacency[target].add(source)
        by_id = {node["id"]: node for node in nodes}
        seen: set[str] = set()
        clusters: list[Cluster] = []
        for node in nodes:
            if node["id"] in seen:
                continue
            component: list[str] = []
            stack = [node["id"]]
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                component.append(current)
                stack.extend(adjacency[current] - seen)
            # consensus spectrum: merged precursor masses of the component
            masses = sorted(by_id[cid]["precursor_mz"] for cid in component)
            intensities = [1000.0 * (1 + i) for i in range(len(masses))]
            clusters.append(
                Cluster(
                    cluster_id=f"cluster-{len(clusters) + 1}",
                    node_ids=component,
                    consensus_spectrum_mz=[round(m, 4) for m in masses],
                    consensus_spectrum_intensity=intensities,
                )
            )
        return clusters

    async def cancel_job(self, handle: JobHandle) -> bool:
        return True

    async def aclose(self) -> None:  # interface parity with GNPSClient
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_gnps_client(**kwargs: Any) -> GNPSClient | MockGNPSClient:
    """Mock when PHYTO_DRY_RUN=1, or when no API key is set and public GNPS
    is not explicitly allowed (PHYTO_ALLOW_PUBLIC_GNPS != 1).

    Note: a developer who exports a real GNPS_API_KEY (and clears
    PHYTO_DRY_RUN) gets the real client even locally -- that is their
    explicit choice; the split-plane default is the mock, since local
    development never carries credentials.
    """
    dry_run = os.environ.get("PHYTO_DRY_RUN") == "1"
    has_key = bool(os.environ.get("GNPS_API_KEY"))
    public_allowed = os.environ.get("PHYTO_ALLOW_PUBLIC_GNPS") == "1"
    if dry_run or (not has_key and not public_allowed):
        return MockGNPSClient()
    return GNPSClient(**kwargs)
