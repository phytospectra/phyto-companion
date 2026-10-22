"""Curation reviewer roles (ADR-0056).

A user document carries ``role: "user" | "curator" | "admin"``.  Only
curators and admins may promote data out of ``training_only`` into the
``public`` or ``proprietary`` tiers.  The role is stored server-side (a
Firestore document per user in the cloud plane, an in-process map on the
local plane) and read from the auth layer -- never from a request payload,
which would let any user promote their own data.
"""

from __future__ import annotations

import os
from typing import Protocol

__all__ = [
    "UserRole",
    "USER_ROLES",
    "RoleStore",
    "InMemoryRoleStore",
    "FirestoreRoleStore",
    "get_role_store",
    "role_for",
    "can_promote",
]

UserRole = str
"""One of ``USER_ROLES``: ``"user"``, ``"curator"`` or ``"admin"``."""

USER_ROLES = ("user", "curator", "admin")

DEFAULT_ROLE: UserRole = "user"


class RoleStore(Protocol):
    def get_role(self, user_id: str) -> UserRole: ...
    def set_role(self, user_id: str, role: UserRole) -> None: ...


class InMemoryRoleStore:
    """Process-local roles (tests, dry-run, single-instance local plane).

    Unknown users resolve to ``user`` -- the least-privileged default, so
    a missing record can never confer promotion rights.
    """

    def __init__(self, roles: dict[str, UserRole] | None = None) -> None:
        self._roles: dict[str, UserRole] = dict(roles or {})

    def get_role(self, user_id: str) -> UserRole:
        return self._roles.get(user_id, DEFAULT_ROLE)

    def set_role(self, user_id: str, role: UserRole) -> None:
        if role not in USER_ROLES:
            raise ValueError(f"unknown role {role!r}; must be one of {USER_ROLES}")
        self._roles[user_id] = role


class FirestoreRoleStore:
    """Production roles; one document per user in the ``user_roles`` collection."""

    COLLECTION = "user_roles"

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

    def get_role(self, user_id: str) -> UserRole:
        snap = self._db.collection(self.COLLECTION).document(user_id).get()
        if not snap.exists:
            return DEFAULT_ROLE
        role = snap.to_dict().get("role", DEFAULT_ROLE)
        return role if role in USER_ROLES else DEFAULT_ROLE

    def set_role(self, user_id: str, role: UserRole) -> None:
        if role not in USER_ROLES:
            raise ValueError(f"unknown role {role!r}; must be one of {USER_ROLES}")
        self._db.collection(self.COLLECTION).document(user_id).set({"role": role})


def get_role_store(project_id: str | None = None) -> RoleStore:
    """Firestore in the cloud plane; in-process locally."""
    if os.environ.get("PHYTO_ENV") == "cloud":
        return FirestoreRoleStore(
            project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "phyto-companion-prod")
        )
    return InMemoryRoleStore()


def can_promote(role: UserRole) -> bool:
    """Whether ``role`` may promote data out of ``training_only``."""
    return role in ("curator", "admin")


def role_for(store: RoleStore, user_id: str) -> UserRole:
    """Resolve a user's role, defensively defaulting to ``user``."""
    try:
        role = store.get_role(user_id)
    except Exception:
        return DEFAULT_ROLE
    return role if role in USER_ROLES else DEFAULT_ROLE