"""Provenance: hashing, manifest building/verification, and audit logging.

Provenance is mandatory, not a feature.  Every input file is hashed, every
manifest self-hashes, and verification raises rather than returning a soft
``False`` -- a silent verification failure is worse than no verification.

Every manifest also carries the analysis's governance disposition (data
tier, ABS status, source datasets, ADR-0056) and :meth:`ProvenanceBuilder.build`
enforces the tier/source licence rules -- a proprietary-tier analysis that
used a non-commercial source cannot be built at all.

Standard library + pydantic only (no external hashing libraries; pandas is
deliberately absent so the local plane stays lightweight, ADR-0004).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .data_governance import (
    ABSStatus,
    DataTier,
    DEFAULT_ABS_STATUS,
    DEFAULT_DATA_TIER,
    is_source_allowed_in_tier,
)
from .exceptions import ProvenanceError
from .models import ProvenanceManifest

__all__ = [
    "sha256_file",
    "sha256_bytes",
    "sha256_dict",
    "ProvenanceBuilder",
    "verify_manifest",
    "AuditLog",
]

logger = logging.getLogger("phyto.provenance")

_HASH_CHUNK_BYTES = 1 << 20  # 1 MiB streaming chunks


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    """Streaming SHA256 of a file on disk (constant memory for any size)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA256 of an in-memory byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_dict(d: dict[str, Any]) -> str:
    """SHA256 of a dict's canonical JSON.

    Canonical form: ``json.dumps`` with sorted keys, compact separators,
    UTF-8, and ``allow_nan=False`` so NaN/Infinity raise ``ValueError``
    instead of producing a non-standard JSON constant.  Key order never
    affects the hash; float formatting follows the JSON module.
    """
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def _jsonify(obj: Any) -> Any:
    """Best-effort conversion to JSON-clean primitives.

    Converts Path/datetime/Enum values; everything else passes through to
    ``json.dumps``, which raises TypeError on anything still unserializable
    (surfacing bad parameters early rather than at verification time).
    """
    if isinstance(obj, dict):
        return {str(k): _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


class ProvenanceBuilder:
    """Fluent builder producing an immutable ``ProvenanceManifest``.

    Example::

        manifest = (
            ProvenanceBuilder()
            .with_input("sample_1.mzML", path)
            .with_model_version("shiftmpnn", "4.0.1")
            .with_database_version("mini_coconut", "v1", downloaded="2026-10-01")
            .with_parameters({"ppm_tolerance": 5.0})
            .with_environment()
            .with_data_governance(
                data_tier="training_only", source_datasets=["user_upload"]
            )
            .build()
        )
    """

    def __init__(self) -> None:
        self._input_hashes: dict[str, str] = {}
        self._model_versions: dict[str, str] = {}
        self._database_versions: dict[str, str] = {}
        self._parameters: dict[str, Any] = {}
        self._environment: dict[str, str] = {}
        self._data_tier: DataTier = DEFAULT_DATA_TIER
        self._abs_status: ABSStatus = DEFAULT_ABS_STATUS
        self._source_datasets: list[str] = []

    def with_input(self, label: str, path: str | Path) -> "ProvenanceBuilder":
        """Hash the file at ``path`` and record it under ``label``."""
        self._input_hashes[label] = sha256_file(path)
        return self

    def with_input_hash(self, label: str, sha256: str) -> "ProvenanceBuilder":
        """Record a pre-computed hash (must be 64 lowercase hex chars)."""
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ProvenanceError(
                "Not a valid SHA256 hex digest", context={"label": label, "value": sha256}
            )
        self._input_hashes[label] = sha256
        return self

    def with_model_version(self, name: str, version: str) -> "ProvenanceBuilder":
        self._model_versions[name] = version
        return self

    def with_database_version(
        self, name: str, version: str, downloaded: str | None = None
    ) -> "ProvenanceBuilder":
        """Record a database version and (recommended) its download date."""
        suffix = f" (downloaded {downloaded})" if downloaded else ""
        self._database_versions[name] = f"{version}{suffix}"
        return self

    def with_parameters(self, params: dict[str, Any]) -> "ProvenanceBuilder":
        """Merge workflow parameters into the record (later keys win)."""
        self._parameters.update(_jsonify(params))
        return self

    def with_environment(self, extra: dict[str, str] | None = None) -> "ProvenanceBuilder":
        """Capture the execution environment (Python, OS, pydantic version)."""
        env: dict[str, str] = {
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "os": platform.system(),
        }
        try:  # pydantic is the one dependency whose version affects validation
            from pydantic import VERSION as _pydantic_version

            env["pydantic_version"] = _pydantic_version
        except ImportError:  # pragma: no cover - pydantic is a hard dependency
            env["pydantic_version"] = "absent"
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        self._environment = env
        return self

    def with_data_governance(
        self,
        *,
        data_tier: DataTier = DEFAULT_DATA_TIER,
        abs_status: ABSStatus = DEFAULT_ABS_STATUS,
        source_datasets: list[str] | tuple[str, ...] | None = None,
    ) -> "ProvenanceBuilder":
        """Record the governance disposition of this analysis (ADR-0056).

        ``data_tier`` defaults to the most restrictive tier and
        ``abs_status`` to ``unknown``; a manifest is never more permissive
        than what the caller declares.
        """
        self._data_tier = data_tier
        self._abs_status = abs_status
        self._source_datasets = list(source_datasets or [])
        return self

    def build(self) -> ProvenanceManifest:
        """Produce the frozen manifest with its top-level ``manifest_hash``.

        Enforcement point (ADR-0056): a proprietary-tier manifest -- or any
        manifest built on the cloud plane with the commercial tier enabled
        (``PHYTO_ENV=cloud`` + ``PHYTO_COMMERCIAL_TIER=1``) -- is REJECTED
        when any source in ``source_datasets`` is not allowed in that tier
        (NP-MRD's CC-BY-NC terms, in particular).  The rejection is logged
        with the offending source and tier before raising, so the audit
        trail records the attempted promotion.
        """
        self._enforce_tier_licences()
        timestamp = datetime.now(timezone.utc)
        content = {
            "input_hashes": dict(self._input_hashes),
            "model_versions": dict(self._model_versions),
            "database_versions": dict(self._database_versions),
            "parameters": _jsonify(self._parameters),
            "environment": dict(self._environment),
            "timestamp": timestamp.isoformat(),
            "data_tier": self._data_tier,
            "abs_status": self._abs_status,
            "source_datasets": list(self._source_datasets),
        }
        return ProvenanceManifest(
            manifest_hash=sha256_dict(content),
            timestamp=timestamp,
            **{k: v for k, v in content.items() if k != "timestamp"},
        )

    def _enforce_tier_licences(self) -> None:
        """Reject analyses whose source mix is incompatible with the tier."""
        commercial_plane = (
            os.environ.get("PHYTO_ENV") == "cloud"
            and os.environ.get("PHYTO_COMMERCIAL_TIER") == "1"
        )
        if self._data_tier == "proprietary":
            benchmark = "proprietary"
        elif commercial_plane and self._data_tier != "public":
            # On the commercial plane the default tier feeds the commercial
            # corpus or a sold model, so the proprietary rules apply unless
            # the analysis is explicitly in the free academic tier.
            benchmark = "proprietary"
        else:
            return
        offending = [
            source
            for source in self._source_datasets
            if not is_source_allowed_in_tier(source, benchmark)
        ]
        if offending:
            logger.warning(
                "provenance governance rejection: tier=%s benchmark=%s "
                "sources=%s offending=%s",
                self._data_tier,
                benchmark,
                list(self._source_datasets),
                offending,
            )
            raise ProvenanceError(
                f"Analysis cannot be built at tier {self._data_tier!r}: "
                f"source(s) {offending} are not allowed in that tier "
                "(non-commercial licence; see docs/DATA_SOURCES.md and "
                "ADR-0056)",
                context={
                    "tier": self._data_tier,
                    "offending_sources": offending,
                    "source_datasets": list(self._source_datasets),
                },
            )


def verify_manifest(
    manifest: ProvenanceManifest, files: dict[str, str | Path]
) -> bool:
    """Recompute and compare every hash; raise ``ProvenanceError`` on any mismatch.

    Checks, in order: (1) the manifest's own ``manifest_hash`` still covers
    its content; (2) ``files`` covers exactly the manifest's input labels;
    (3) every file on disk still hashes to its recorded value.  Returns
    ``True`` only when everything agrees -- callers never need to check a
    soft ``False``.
    """
    recomputed = sha256_dict(manifest.content_dict())
    if recomputed != manifest.manifest_hash:
        raise ProvenanceError(
            "Manifest content does not match its manifest_hash (tampered or "
            "corrupted record)",
            context={
                "expected": manifest.manifest_hash,
                "actual": recomputed,
                "check": "manifest_hash",
            },
        )

    missing = sorted(set(manifest.input_hashes) - set(files))
    extra = sorted(set(files) - set(manifest.input_hashes))
    if missing or extra:
        raise ProvenanceError(
            "Provided files do not cover the manifest inputs exactly",
            context={"missing_labels": missing, "extra_labels": extra},
        )

    for label, path in files.items():
        path = Path(path)
        if not path.is_file():
            raise ProvenanceError(
                "Input file missing during verification",
                context={"label": label, "path": str(path)},
            )
        actual = sha256_file(path)
        if actual != manifest.input_hashes[label]:
            raise ProvenanceError(
                "Input file hash mismatch (file modified after manifest was built)",
                context={
                    "label": label,
                    "path": str(path),
                    "expected": manifest.input_hashes[label],
                    "actual": actual,
                },
            )
    return True


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLog:
    """Append-only JSONL audit trail for agent and tool actions.

    Each line is one JSON object; lines are numbered by ``seq`` which
    continues across re-opens, making truncation or reordering detectable.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._count_existing_lines()

    def _count_existing_lines(self) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def log_action(
        self,
        agent: str,
        tool: str,
        input_hash: str,
        output_hash: str,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one audit record and return it."""
        record: dict[str, Any] = {
            "seq": self._seq + 1,
            "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
            "agent": agent,
            "tool": tool,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "metadata": metadata or {},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        self._seq += 1
        return record

    def to_dataframe(self) -> list[dict[str, Any]]:
        """Read all records as a row list (one dict per action).

        Named ``to_dataframe`` for API compatibility; it returns plain rows
        so this library stays pandas-free.  Cloud-plane callers wrap the
        result: ``pandas.DataFrame(audit_log.to_dataframe())`` (ADR-0004).
        """
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
        return rows
