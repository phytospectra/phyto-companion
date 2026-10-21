"""In-memory job manager for GNPS networking jobs submitted via the API.

The manager tracks each job's handle, status, and (once complete) parsed
results.  In the local plane the client is the deterministic mock; in
the cloud plane it is the real ``GNPSClient`` -- the manager is agnostic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from phyto_shared.exceptions import InvalidInputError

from .gnps_client import (
    FBMNParams,
    GNPSClient,
    IIMNParams,
    JobHandle,
    JobStatus,
    MNParams,
    MNResults,
    MockGNPSClient,
    get_gnps_client,
)
from .gnps_result_parser import ParsedNetwork, parse_graphml

__all__ = ["JobEntry", "GNPSJobManager"]


@dataclass
class JobEntry:
    handle: JobHandle
    network_type: str
    submitted_payload_sha: str
    status: JobStatus = JobStatus.PENDING
    results: MNResults | None = None
    parsed: ParsedNetwork | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class GNPSJobManager:
    """Registry of submitted GNPS jobs keyed by a local job id."""

    def __init__(self, client: GNPSClient | MockGNPSClient | None = None) -> None:
        self.client = client or get_gnps_client()
        self._jobs: dict[str, JobEntry] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        *,
        job_id: str,
        network_type: str,
        mgf: bytes,
        feature_table: bytes | None,
        supplementary_pairs: bytes | None,
        params: dict[str, Any],
        payload_sha: str,
    ) -> JobEntry:
        async with self._lock:
            if job_id in self._jobs:
                raise InvalidInputError(
                    "job id already exists", context={"job_id": job_id}
                )
        if network_type == "classic":
            handle = await self.client.submit_mn_job(mgf, MNParams(**params))
        elif network_type == "fbmn":
            handle = await self.client.submit_fbmn_job(
                mgf, feature_table or b"", FBMNParams(**params)
            )
        elif network_type == "iimn":
            handle = await self.client.submit_iimn_job(
                mgf, feature_table or b"", supplementary_pairs or b"", IIMNParams(**params)
            )
        else:
            raise InvalidInputError(
                "Unknown network type",
                context={"network_type": network_type, "allowed": ["classic", "fbmn", "iimn"]},
            )
        entry = JobEntry(
            handle=handle,
            network_type=network_type,
            submitted_payload_sha=payload_sha,
            status=JobStatus.RUNNING,
        )
        async with self._lock:
            self._jobs[job_id] = entry
        return entry

    async def refresh(self, job_id: str) -> JobEntry:
        """Poll GNPS for status; fetch and parse results when completed."""
        entry = self._entry(job_id)
        if entry.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return entry
        entry.status = await self.client.get_job_status(entry.handle)
        if entry.status is JobStatus.COMPLETED and entry.results is None:
            entry.results = await self.client.fetch_results(entry.handle)
            entry.parsed = parse_graphml(entry.results.graphml)
        return entry

    async def cancel(self, job_id: str) -> bool:
        entry = self._entry(job_id)
        cancelled = await self.client.cancel_job(entry.handle)
        if cancelled:
            entry.status = JobStatus.CANCELLED
        return cancelled

    def _entry(self, job_id: str) -> JobEntry:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise InvalidInputError(
                "Unknown job id", context={"job_id": job_id, "known": sorted(self._jobs)}
            ) from None
