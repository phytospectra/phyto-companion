"""Tamper-evident audit trail: hash-chained JSONL.

Every tool call, checkpoint decision, and run event lands here.  Each
record embeds the previous record's hash, so any edit, deletion, or
reordering of history breaks the chain and ``verify_chain`` raises --
the audit trail is append-only evidence, not a log file you can tidy.

The class lives in the shared library (not only the orchestrator) because
cohort analytics events are appended to the audit chain from every service
that raises them (ADR-0048): the orchestrator for run events, the auth
service for signups and plan changes.  Two implementations could drift;
one implementation cannot disagree with itself.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import ToolTier
from .exceptions import ProvenanceError
from .provenance import sha256_dict

__all__ = ["GENESIS_HASH", "ChainedAuditLogger"]

GENESIS_HASH = "0" * 64
_HASH_FIELDS = ("prev_hash", "record_hash")


class ChainedAuditLogger:
    """Append-only, hash-chained JSONL audit trail."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[dict[str, Any]] = self._load_existing()
        self._last_hash = (
            self._records[-1]["record_hash"] if self._records else GENESIS_HASH
        )

    # -- loading ----------------------------------------------------------

    def _load_existing(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ProvenanceError(
                        "Audit log contains a corrupted line",
                        context={"path": str(self.path), "line": line_no},
                    ) from exc
        return records

    # -- writing ----------------------------------------------------------

    def _append(self, content: dict[str, Any]) -> dict[str, Any]:
        record_hash = sha256_dict(
            {**content, "prev_hash": self._last_hash}
        )
        record = {
            **content,
            "prev_hash": self._last_hash,
            "record_hash": record_hash,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
        self._records.append(record)
        self._last_hash = record_hash
        return record

    def log_tool_call(
        self,
        *,
        run_id: str,
        agent: str,
        tool: str,
        tier: ToolTier,
        input_hash: str,
        output_hash: str,
        verified: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one delegated tool call with its input/output hashes."""
        return self._append(
            {
                "seq": len(self._records) + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": "tool_call",
                "run_id": run_id,
                "agent": agent,
                "tool": tool,
                "tier": int(tier),
                "input_hash": input_hash,
                "output_hash": output_hash,
                "verified": verified,
                "metadata": metadata or {},
            }
        )

    def log_event(
        self,
        *,
        run_id: str,
        event: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a workflow event (run started, checkpoint requested, ...)."""
        return self._append(
            {
                "seq": len(self._records) + 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "kind": "event",
                "run_id": run_id,
                "event": event,
                "detail": detail or {},
            }
        )

    def log_analytics(
        self,
        *,
        event: str,
        user_id: str,
        cohort_month: str | None = None,
        run_id: str | None = None,
        properties: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        """Append a cohort-analytics event (ADR-0048).

        Analytics events are chain records, not a separate system: they get
        the same hash chaining and tamper evidence as tool calls.  The
        ``kind`` distinguishes them from workflow events so the cohort job
        can filter without guessing which "run_started" record is which.
        """
        occurred = timestamp or datetime.now(timezone.utc)
        return self._append(
            {
                "seq": len(self._records) + 1,
                "timestamp": occurred.isoformat(),
                "kind": "analytics_event",
                "run_id": run_id or "",
                "event": event,
                "user_id": user_id,
                "cohort_month": cohort_month or _cohort_month(occurred),
                "detail": properties or {},
            }
        )

    # -- reading / verification -------------------------------------------

    def records(self) -> list[dict[str, Any]]:
        """All records, in order (copy)."""
        return [dict(record) for record in self._records]

    def to_dataframe(self) -> list[dict[str, Any]]:
        """Rows for analysis; same convention as ``phyto_shared.AuditLog``."""
        return self.records()

    def verify_chain(self) -> bool:
        """Recompute every hash in order; raise ``ProvenanceError`` on any break.

        Detects edits (record_hash mismatch), insertions, deletions, and
        reordering.  Returns True only for an intact chain.
        """
        previous = GENESIS_HASH
        for position, record in enumerate(self._records, start=1):
            content = {k: v for k, v in record.items() if k not in _HASH_FIELDS}
            expected = sha256_dict({**content, "prev_hash": previous})
            if record.get("prev_hash") != previous:
                raise ProvenanceError(
                    "Audit chain broken: record does not link to its predecessor",
                    context={"position": position, "record_seq": record.get("seq")},
                )
            if record.get("record_hash") != expected:
                raise ProvenanceError(
                    "Audit chain broken: record content was modified after "
                    "it was written",
                    context={
                        "position": position,
                        "record_seq": record.get("seq"),
                        "expected": expected,
                        "actual": record.get("record_hash"),
                    },
                )
            previous = record["record_hash"]
        return True


def _cohort_month(moment: datetime) -> str:
    """``YYYY-MM`` in UTC; the cohort key for analytics events."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m")
