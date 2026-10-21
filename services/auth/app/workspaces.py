"""Workspaces: shared token pools for classrooms, labs, departments.

A workspace has a shared pool; members draw from the pool before their
personal balance (ADR-0047).  Classroom workspaces are capped at 30 seats
and 5,000 tokens/month; the caps come from ``WORKSPACE_CAPS`` so the
pricing page, the API and the middleware cannot disagree.

Activation (the teaching-loop metric): a member counts as activated only
after completing one full analysis -- a confirmed metered
``POST /analysis`` -- not after signing in or merely being invited.
"""

from __future__ import annotations

import secrets
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from phyto_shared.plans import WorkspaceCaps, workspace_caps

__all__ = [
    "Workspace",
    "WorkspaceMember",
    "WorkspaceError",
    "SeatLimitExceeded",
    "InsufficientPoolError",
    "ACTIVATING_OPERATION",
    "WorkspaceStore",
    "InMemoryWorkspaceStore",
    "FirestoreWorkspaceStore",
]


ACTIVATING_OPERATION = "POST /analysis"
"""The operation that marks a workspace member as activated: one full
orchestrated analysis, confirmed through the metering boundary."""


class WorkspaceError(Exception):
    """Base workspace failure."""


class SeatLimitExceeded(WorkspaceError):
    def __init__(self, workspace: "Workspace"):
        super().__init__(
            f"workspace {workspace.workspace_id} is full: "
            f"{len(workspace.members)}/{workspace.caps.seats} seats used"
        )


class InsufficientPoolError(WorkspaceError):
    def __init__(self, workspace_id: str, requested: int, available: int):
        self.requested = requested
        self.available = available
        super().__init__(
            f"workspace pool {workspace_id} has {available} tokens, "
            f"{requested} requested"
        )


@dataclass
class WorkspaceMember:
    uid: str
    email: str
    role: Literal["owner", "member"] = "member"
    status: Literal["invited", "active"] = "invited"
    tokens_used: int = 0
    activated: bool = False
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_active_at: datetime | None = None


@dataclass
class Workspace:
    workspace_id: str
    name: str
    type: Literal["classroom", "lab", "department"]
    owner_uid: str
    token_pool: int
    token_pool_used: int
    seats: int
    caps: WorkspaceCaps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    members: dict[str, WorkspaceMember] = field(default_factory=dict)

    def snapshot(self) -> dict:
        """JSON-friendly state for API responses."""
        return {
            "workspace_id": self.workspace_id,
            "name": self.name,
            "type": self.type,
            "owner_uid": self.owner_uid,
            "token_pool": self.token_pool,
            "token_pool_used": self.token_pool_used,
            "token_pool_remaining": self.token_pool - self.token_pool_used,
            "seats": self.seats,
            "seat_limit": self.caps.seats,
            "monthly_pool_tokens": self.caps.monthly_pool_tokens,
            "members": [
                {
                    "uid": member.uid,
                    "email": member.email,
                    "role": member.role,
                    "status": member.status,
                    "tokens_used": member.tokens_used,
                    "activated": member.activated,
                }
                for member in self.members.values()
            ],
            "created_at": self.created_at.isoformat(),
        }


class WorkspaceStore(Protocol):
    def create(self, *, name: str, type: str, owner_uid: str) -> Workspace: ...
    def get(self, workspace_id: str) -> Workspace | None: ...
    def add_member(self, workspace_id: str, email: str) -> WorkspaceMember: ...
    def remove_member(self, workspace_id: str, uid: str) -> bool: ...
    def pool_for_user(self, uid: str) -> tuple[str, int] | None:
        """The (workspace_id, remaining pool) a member should draw from, or
        None when the user belongs to no workspace with a positive pool."""
    def draw(self, workspace_id: str, uid: str, tokens: int, operation: str) -> str: ...
    def settle_draw(self, draw_id: str, *, confirmed: bool, operation: str) -> None: ...
    def topup_pool(self, workspace_id: str, tokens: int) -> int: ...
    def usage(self, workspace_id: str) -> dict: ...


class InMemoryWorkspaceStore:
    """Process-local store (tests, dry-run, single-instance local plane)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._workspaces: dict[str, Workspace] = {}
        self._membership: dict[str, list[str]] = {}  # uid -> workspace_ids
        self._draws: dict[str, dict] = {}

    # -- lifecycle ------------------------------------------------------------
    def create(self, *, name: str, type: str, owner_uid: str) -> Workspace:
        caps = workspace_caps(type)  # raises for unknown types
        workspace = Workspace(
            workspace_id=f"ws-{uuid.uuid4().hex[:12]}",
            name=name,
            type=type,  # type: ignore[arg-type]
            owner_uid=owner_uid,
            token_pool=caps.monthly_pool_tokens,
            token_pool_used=0,
            seats=caps.seats,
            caps=caps,
        )
        owner = WorkspaceMember(
            uid=owner_uid, email=f"{owner_uid}@workspace.local", role="owner",
            status="active",
        )
        workspace.members[owner_uid] = owner
        with self._lock:
            self._workspaces[workspace.workspace_id] = workspace
            self._membership.setdefault(owner_uid, []).append(workspace.workspace_id)
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        with self._lock:
            workspace = self._workspaces.get(workspace_id)
            return workspace

    def add_member(self, workspace_id: str, email: str) -> WorkspaceMember:
        # Email invites resolve to a placeholder uid until the invitee signs
        # in; a real deployment sends a Firebase invite email and swaps the
        # placeholder for the claimed uid (documented in ADR-0047).
        uid = f"invite-{secrets.sha256(email.lower().encode()).hexdigest()[:12]}"
        with self._lock:
            workspace = self._require(workspace_id)
            if len(workspace.members) >= workspace.caps.seats:
                raise SeatLimitExceeded(workspace)
            if uid in workspace.members:
                return workspace.members[uid]
            member = WorkspaceMember(uid=uid, email=email, status="invited")
            workspace.members[uid] = member
            self._membership.setdefault(uid, []).append(workspace_id)
            return member

    def remove_member(self, workspace_id: str, uid: str) -> bool:
        with self._lock:
            workspace = self._require(workspace_id)
            member = workspace.members.pop(uid, None)
            if member is None:
                return False
            ids = self._membership.get(uid, [])
            if workspace_id in ids:
                ids.remove(workspace_id)
            return True

    def _require(self, workspace_id: str) -> Workspace:
        workspace = self._workspaces.get(workspace_id)
        if workspace is None:
            raise WorkspaceError(f"unknown workspace {workspace_id!r}")
        return workspace

    # -- pool draw (mirror of the personal ledger's reserve/confirm/refund) ----
    def pool_for_user(self, uid: str) -> tuple[str, int] | None:
        with self._lock:
            for workspace_id in self._membership.get(uid, []):
                workspace = self._workspaces.get(workspace_id)
                if workspace is None:
                    continue
                if workspace.token_pool - workspace.token_pool_used > 0:
                    return workspace_id, workspace.token_pool - workspace.token_pool_used
        return None

    def draw(self, workspace_id: str, uid: str, tokens: int, operation: str) -> str:
        with self._lock:
            workspace = self._require(workspace_id)
            remaining = workspace.token_pool - workspace.token_pool_used
            if remaining < tokens:
                raise InsufficientPoolError(workspace_id, tokens, remaining)
            workspace.token_pool_used += tokens
            draw_id = f"wsdraw-{uuid.uuid4().hex}"
            self._draws[draw_id] = {
                "workspace_id": workspace_id,
                "uid": uid,
                "tokens": tokens,
                "operation": operation,
                "status": "reserved",
            }
            return draw_id

    def settle_draw(self, draw_id: str, *, confirmed: bool, operation: str) -> None:
        with self._lock:
            draw = self._draws.get(draw_id)
            if draw is None or draw["status"] != "reserved":
                return
            workspace = self._workspaces.get(draw["workspace_id"])
            if workspace is None:
                return
            draw["status"] = "confirmed" if confirmed else "refunded"
            member = workspace.members.get(draw["uid"])
            if not confirmed:
                workspace.token_pool_used -= draw["tokens"]
                return
            if member is not None:
                member.tokens_used += draw["tokens"]
                member.last_active_at = datetime.now(timezone.utc)
                member.status = "active"
                if draw["operation"] == ACTIVATING_OPERATION:
                    # Activation: one full analysis completed, not a sign-in.
                    member.activated = True

    def topup_pool(self, workspace_id: str, tokens: int) -> int:
        if tokens < 0:
            raise ValueError("topup tokens must be non-negative")
        with self._lock:
            workspace = self._require(workspace_id)
            workspace.token_pool += tokens
            return workspace.token_pool

    def usage(self, workspace_id: str) -> dict:
        """Instructor dashboard: per-member usage, activation, retention."""
        with self._lock:
            workspace = self._require(workspace_id)
            members = list(workspace.members.values())
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            active_last_30d = sum(
                1
                for member in members
                if member.last_active_at and member.last_active_at >= cutoff
            )
            return {
                "workspace_id": workspace_id,
                "per_member": [
                    {
                        "uid": member.uid,
                        "email": member.email,
                        "tokens_used": member.tokens_used,
                        "activated": member.activated,
                        "status": member.status,
                        "last_active_at": (
                            member.last_active_at.isoformat()
                            if member.last_active_at
                            else None
                        ),
                    }
                    for member in members
                ],
                "activated": sum(1 for member in members if member.activated),
                "seats_used": len(members),
                "seat_limit": workspace.caps.seats,
                "activation_rate": (
                    sum(1 for member in members if member.activated) / len(members)
                    if members
                    else 0.0
                ),
                "retention_30d": (
                    active_last_30d / len(members) if members else 0.0
                ),
                "token_pool_remaining": workspace.token_pool
                - workspace.token_pool_used,
            }


class FirestoreWorkspaceStore:
    """Production store; mirrors the in-memory schema document-per-workspace.

    Collection ``workspaces/{workspace_id}``: the ``Workspace.snapshot``
    fields plus a ``members`` map.  Pool draws are transactions over the
    workspace document; the seat check reads members inside the same
    transaction so two racing invites cannot both take the last seat.
    """

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

    # Reads and writes are transactional where they must be atomic (seats,
    # pool draws); simple reads are not wrapped to keep them cheap.
    def _run(self, operation, *args):
        step = self._firestore.transactional(
            lambda transaction: operation(transaction, *args)
        )
        return step(self._db.transaction())

    def _ref(self, workspace_id: str):
        return self._db.collection("workspaces").document(workspace_id)

    @staticmethod
    def _to_workspace(data: dict) -> Workspace:
        caps = workspace_caps(data["type"])
        members = {
            uid: WorkspaceMember(**member)
            for uid, member in (data.get("members") or {}).items()
        }
        return Workspace(
            workspace_id=data["workspace_id"],
            name=data["name"],
            type=data["type"],
            owner_uid=data["owner_uid"],
            token_pool=int(data["token_pool"]),
            token_pool_used=int(data["token_pool_used"]),
            seats=int(data["seats"]),
            caps=caps,
            members=members,
        )

    def create(self, *, name: str, type: str, owner_uid: str) -> Workspace:
        caps = workspace_caps(type)
        workspace = Workspace(
            workspace_id=f"ws-{uuid.uuid4().hex[:12]}",
            name=name,
            type=type,  # type: ignore[arg-type]
            owner_uid=owner_uid,
            token_pool=caps.monthly_pool_tokens,
            token_pool_used=0,
            seats=caps.seats,
            caps=caps,
        )
        workspace.members[owner_uid] = WorkspaceMember(
            uid=owner_uid, email=f"{owner_uid}@workspace.local", role="owner",
            status="active",
        )
        self._ref(workspace.workspace_id).set(
            {**workspace.snapshot(), "created_at": workspace.created_at}
        )
        self._db.collection("workspace_members").document(owner_uid).set(
            {workspace.workspace_id: True}, merge=True
        )
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        snap = self._ref(workspace_id).get()
        if not snap.exists:
            return None
        return self._to_workspace(snap.to_dict())

    def add_member(self, workspace_id: str, email: str) -> WorkspaceMember:
        import hashlib

        uid = f"invite-{hashlib.sha256(email.lower().encode()).hexdigest()[:12]}"
        member = WorkspaceMember(uid=uid, email=email, status="invited")

        def _add(transaction):
            snap = self._ref(workspace_id).get(transaction=transaction)
            if not snap.exists:
                raise WorkspaceError(f"unknown workspace {workspace_id!r}")
            data = snap.to_dict()
            members = data.get("members") or {}
            if len(members) >= int(data["seat_limit"]):
                raise SeatLimitExceeded(self._to_workspace(data))
            members[uid] = {
                "uid": uid,
                "email": email,
                "role": "member",
                "status": "invited",
                "tokens_used": 0,
                "activated": False,
                "added_at": datetime.now(timezone.utc),
                "last_active_at": None,
            }
            transaction.set(self._ref(workspace_id), {"members": members}, merge=True)

        self._run(_add)
        self._db.collection("workspace_members").document(uid).set(
            {workspace_id: True}, merge=True
        )
        return member

    def remove_member(self, workspace_id: str, uid: str) -> bool:
        def _remove(transaction) -> bool:
            snap = self._ref(workspace_id).get(transaction=transaction)
            if not snap.exists:
                return False
            members = snap.to_dict().get("members") or {}
            if uid not in members:
                return False
            del members[uid]
            transaction.set(self._ref(workspace_id), {"members": members}, merge=True)
            return True

        removed = self._run(_remove)
        if removed:
            self._db.collection("workspace_members").document(uid).set(
                {workspace_id: self._firestore.DELETE_FIELD}, merge=True
            )
        return bool(removed)

    def pool_for_user(self, uid: str) -> tuple[str, int] | None:
        snap = self._db.collection("workspace_members").document(uid).get()
        if not snap.exists:
            return None
        for workspace_id in (snap.to_dict() or {}):
            workspace = self.get(workspace_id)
            if workspace and workspace.token_pool - workspace.token_pool_used > 0:
                return workspace_id, workspace.token_pool - workspace.token_pool_used
        return None

    def draw(self, workspace_id: str, uid: str, tokens: int, operation: str) -> str:
        draw_id = f"wsdraw-{uuid.uuid4().hex}"

        def _draw(transaction):
            snap = self._ref(workspace_id).get(transaction=transaction)
            if not snap.exists:
                raise WorkspaceError(f"unknown workspace {workspace_id!r}")
            data = snap.to_dict()
            remaining = int(data["token_pool"]) - int(data["token_pool_used"])
            if remaining < tokens:
                raise InsufficientPoolError(workspace_id, tokens, remaining)
            transaction.set(
                self._ref(workspace_id),
                {"token_pool_used": int(data["token_pool_used"]) + tokens},
                merge=True,
            )
            transaction.set(
                self._db.collection("workspace_draws").document(draw_id),
                {
                    "workspace_id": workspace_id,
                    "uid": uid,
                    "tokens": tokens,
                    "operation": operation,
                    "status": "reserved",
                },
            )

        self._run(_draw)
        return draw_id

    def settle_draw(self, draw_id: str, *, confirmed: bool, operation: str) -> None:
        def _settle(transaction):
            ref = self._db.collection("workspace_draws").document(draw_id)
            snap = ref.get(transaction=transaction)
            if not snap.exists or snap.to_dict().get("status") != "reserved":
                return
            draw = snap.to_dict()
            transaction.set(
                ref, {"status": "confirmed" if confirmed else "refunded"}, merge=True
            )
            ws_snap = self._ref(draw["workspace_id"]).get(transaction=transaction)
            data = ws_snap.to_dict()
            if not confirmed:
                transaction.set(
                    self._ref(draw["workspace_id"]),
                    {"token_pool_used": int(data["token_pool_used"]) - int(draw["tokens"])},
                    merge=True,
                )
                return
            members = data.get("members") or {}
            member = dict(members.get(draw["uid"]) or {})
            member["tokens_used"] = int(member.get("tokens_used", 0)) + int(draw["tokens"])
            member["last_active_at"] = datetime.now(timezone.utc)
            member["status"] = "active"
            if draw["operation"] == ACTIVATING_OPERATION:
                member["activated"] = True
            members[draw["uid"]] = member
            transaction.set(
                self._ref(draw["workspace_id"]), {"members": members}, merge=True
            )

        self._run(_settle)

    def topup_pool(self, workspace_id: str, tokens: int) -> int:
        if tokens < 0:
            raise ValueError("topup tokens must be non-negative")

        def _topup(transaction) -> int:
            snap = self._ref(workspace_id).get(transaction=transaction)
            data = snap.to_dict()
            after = int(data["token_pool"]) + tokens
            transaction.set(self._ref(workspace_id), {"token_pool": after}, merge=True)
            return after

        return self._run(_topup)

    def usage(self, workspace_id: str) -> dict:
        workspace = self.get(workspace_id)
        if workspace is None:
            raise WorkspaceError(f"unknown workspace {workspace_id!r}")
        members = list(workspace.members.values())
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        active_last_30d = sum(
            1 for m in members if m.last_active_at and m.last_active_at >= cutoff
        )
        return {
            "workspace_id": workspace_id,
            "per_member": [
                {
                    "uid": m.uid,
                    "email": m.email,
                    "tokens_used": m.tokens_used,
                    "activated": m.activated,
                    "status": m.status,
                    "last_active_at": (
                        m.last_active_at.isoformat() if m.last_active_at else None
                    ),
                }
                for m in members
            ],
            "activated": sum(1 for m in members if m.activated),
            "seats_used": len(members),
            "seat_limit": workspace.caps.seats,
            "activation_rate": (
                sum(1 for m in members if m.activated) / len(members) if members else 0.0
            ),
            "retention_30d": active_last_30d / len(members) if members else 0.0,
            "token_pool_remaining": workspace.token_pool - workspace.token_pool_used,
        }
