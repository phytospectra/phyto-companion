"""Persistence for isolation records (ADR-0055).

Two stores behind one protocol, mirroring ``auth.workspaces``:

* ``InMemoryIsolationStore`` -- the local plane (tests, dry-run);
* ``FirestoreIsolationStore`` -- the cloud plane, one document per
  record in the ``isolation_records`` collection.

Ownership is part of every document: every read filters on ``user_id``
in the store itself, so a handler cannot accidentally serve another
user's bench data even if it forgets to check (defence in depth under
the auth middleware, which sets ``request.state.user``).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from phyto_shared.exceptions import PhytoCompanionError
from phyto_shared.isolation import IsolationRecord

__all__ = [
    "IsolationRecordNotFound",
    "IsolationStore",
    "InMemoryIsolationStore",
    "FirestoreIsolationStore",
]


class IsolationRecordNotFound(PhytoCompanionError):
    """Raised when a record does not exist OR belongs to another user;
    handlers must map both to 404 so existence is never disclosed."""


class IsolationStore(Protocol):
    def create(self, record: IsolationRecord) -> IsolationRecord: ...
    def get(self, record_id: str, *, user_id: str) -> IsolationRecord: ...
    def list_for_user(self, user_id: str) -> list[IsolationRecord]: ...
    def update(
        self, record_id: str, record: IsolationRecord, *, user_id: str
    ) -> IsolationRecord: ...
    def find_by_extract(self, extract_id: str, *, user_id: str) -> IsolationRecord | None: ...


def _not_found(record_id: str) -> IsolationRecordNotFound:
    return IsolationRecordNotFound(
        "isolation record not found", context={"record_id": record_id}
    )


class InMemoryIsolationStore:
    """Process-local store (tests, dry-run, single-instance local plane)."""

    def __init__(self) -> None:
        self._records: dict[str, IsolationRecord] = {}

    def create(self, record: IsolationRecord) -> IsolationRecord:
        record_id = record.record_id or f"iso-{uuid.uuid4().hex[:12]}"
        stored = record.model_copy(update={"record_id": record_id})
        self._records[record_id] = stored
        return stored

    def get(self, record_id: str, *, user_id: str) -> IsolationRecord:
        record = self._records.get(record_id)
        if record is None or record.user_id != user_id:
            # Same response for missing and foreign: existence is not disclosed.
            raise _not_found(record_id)
        return record

    def list_for_user(self, user_id: str) -> list[IsolationRecord]:
        return sorted(
            (r for r in self._records.values() if r.user_id == user_id),
            key=lambda r: r.created_at,
        )

    def update(
        self, record_id: str, record: IsolationRecord, *, user_id: str
    ) -> IsolationRecord:
        self.get(record_id, user_id=user_id)  # ownership check
        stored = record.model_copy(update={"record_id": record_id})
        self._records[record_id] = stored
        return stored

    def find_by_extract(self, extract_id: str, *, user_id: str) -> IsolationRecord | None:
        return next(
            (
                r
                for r in self._records.values()
                if r.user_id == user_id and r.extract_id == extract_id
            ),
            None,
        )


class FirestoreIsolationStore:
    """Production store; one document per record, ``user_id`` on every query.

    Document id is the server-assigned ``record_id``; the collection is
    ``isolation_records``.  All reads filter on ``user_id`` so per-user
    isolation holds even without Firestore security rules.
    """

    COLLECTION = "isolation_records"

    def __init__(self, project_id: str, client=None):
        try:
            from google.cloud import firestore
        except ImportError as e:
            raise RuntimeError(
                "google-cloud-firestore not installed. "
                "Install with: pip install google-cloud-firestore"
            ) from e
        self._firestore = firestore
        self._db = client if client is not None else firestore.Client(project=project_id)

    def _ref(self, record_id: str):
        return self._db.collection(self.COLLECTION).document(record_id)

    @staticmethod
    def _to_record(data: dict) -> IsolationRecord:
        return IsolationRecord.model_validate(data)

    def create(self, record: IsolationRecord) -> IsolationRecord:
        record_id = record.record_id or f"iso-{uuid.uuid4().hex[:12]}"
        stored = record.model_copy(update={"record_id": record_id})
        self._ref(record_id).set(stored.model_dump(mode="json"))
        return stored

    def get(self, record_id: str, *, user_id: str) -> IsolationRecord:
        snap = self._ref(record_id).get()
        if not snap.exists:
            raise _not_found(record_id)
        record = self._to_record(snap.to_dict())
        if record.user_id != user_id:
            raise _not_found(record_id)
        return record

    def list_for_user(self, user_id: str) -> list[IsolationRecord]:
        docs = (
            self._db.collection(self.COLLECTION)
            .where("user_id", "==", user_id)
            .stream()
        )
        records = [self._to_record(doc.to_dict()) for doc in docs]
        return sorted(records, key=lambda r: r.created_at)

    def update(
        self, record_id: str, record: IsolationRecord, *, user_id: str
    ) -> IsolationRecord:
        self.get(record_id, user_id=user_id)  # ownership check
        stored = record.model_copy(update={"record_id": record_id})
        self._ref(record_id).set(stored.model_dump(mode="json"))
        return stored

    def find_by_extract(self, extract_id: str, *, user_id: str) -> IsolationRecord | None:
        docs = (
            self._db.collection(self.COLLECTION)
            .where("user_id", "==", user_id)
            .where("extract_id", "==", extract_id)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return self._to_record(doc.to_dict())
        return None