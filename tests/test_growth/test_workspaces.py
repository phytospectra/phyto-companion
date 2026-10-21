"""Smoke tests for classroom/lab/department workspaces (ADR-0047).

The observable contract: creating a workspace yields an id, members are added
by email, a member draws from the shared pool before their personal balance,
and classroom seats are capped at 30.
"""

from __future__ import annotations

import pytest

from auth.workspaces import (
    ACTIVATING_OPERATION,
    InMemoryWorkspaceStore,
    InsufficientPoolError,
    SeatLimitExceeded,
)


@pytest.fixture()
def store():
    return InMemoryWorkspaceStore()


class TestWorkspaceLifecycle:
    def test_creating_a_workspace_returns_an_id(self, store):
        workspace = store.create(name="CHEM 301", type="classroom", owner_uid="prof")
        assert workspace.workspace_id
        assert workspace.workspace_id.startswith("ws-")
        assert store.get(workspace.workspace_id) is not None

    def test_unknown_workspace_type_is_rejected(self, store):
        with pytest.raises(ValueError):
            store.create(name="X", type="seminar", owner_uid="prof")

    def test_adding_a_member_by_email_returns_a_membership(self, store):
        workspace = store.create(name="Lab", type="lab", owner_uid="pi")
        member = store.add_member(workspace.workspace_id, "student@university.edu")
        assert member.email == "student@university.edu"
        assert member.status == "invited"
        assert member.uid.startswith("invite-")

    def test_adding_the_same_email_twice_does_not_duplicate(self, store):
        workspace = store.create(name="Lab", type="lab", owner_uid="pi")
        first = store.add_member(workspace.workspace_id, "a@b.edu")
        second = store.add_member(workspace.workspace_id, "a@b.edu")
        assert first.uid == second.uid
        assert len(store.get(workspace.workspace_id).members) == 2  # owner + member


class TestPoolDraw:
    def test_member_request_draws_from_the_pool_first(self, store):
        workspace = store.create(name="CHEM 301", type="classroom", owner_uid="prof")
        member = store.add_member(workspace.workspace_id, "student@university.edu")
        pool = store.pool_for_user(member.uid)
        assert pool is not None
        workspace_id, remaining = pool
        assert workspace_id == workspace.workspace_id
        assert remaining == 5_000

        draw_id = store.draw(workspace_id, member.uid, 20, ACTIVATING_OPERATION)
        store.settle_draw(draw_id, confirmed=True, operation=ACTIVATING_OPERATION)

        usage = store.usage(workspace.workspace_id)
        assert usage["token_pool_remaining"] == 4_980
        by_uid = {entry["uid"]: entry for entry in usage["per_member"]}
        assert by_uid[member.uid]["tokens_used"] == 20

    def test_pool_draw_cannot_exceed_the_pool(self, store):
        workspace = store.create(name="Small", type="lab", owner_uid="pi")
        with pytest.raises(InsufficientPoolError):
            store.draw(workspace.workspace_id, "pi", 10_000_000, ACTIVATING_OPERATION)

    def test_refunded_draw_restores_the_pool(self, store):
        workspace = store.create(name="Lab", type="lab", owner_uid="pi")
        draw_id = store.draw(workspace.workspace_id, "pi", 50, ACTIVATING_OPERATION)
        store.settle_draw(draw_id, confirmed=False, operation=ACTIVATING_OPERATION)
        assert store.usage(workspace.workspace_id)["token_pool_remaining"] == 10_000


class TestActivation:
    def test_activation_requires_a_completed_analysis(self, store):
        workspace = store.create(name="CHEM 301", type="classroom", owner_uid="prof")
        member = store.add_member(workspace.workspace_id, "student@university.edu")

        # A cheaper operation confirms usage but must not activate.
        draw_id = store.draw(workspace.workspace_id, member.uid, 3, "POST /nmr_search/peaks")
        store.settle_draw(draw_id, confirmed=True, operation="POST /nmr_search/peaks")
        by_uid = {e["uid"]: e for e in store.usage(workspace.workspace_id)["per_member"]}
        assert by_uid[member.uid]["activated"] is False

        draw_id = store.draw(workspace.workspace_id, member.uid, 20, ACTIVATING_OPERATION)
        store.settle_draw(draw_id, confirmed=True, operation=ACTIVATING_OPERATION)
        by_uid = {e["uid"]: e for e in store.usage(workspace.workspace_id)["per_member"]}
        assert by_uid[member.uid]["activated"] is True


class TestSeatCap:
    def test_classroom_is_capped_at_thirty_seats(self, store):
        workspace = store.create(name="CHEM 301", type="classroom", owner_uid="prof")
        assert workspace.caps.seats == 30
        # The owner occupies the first seat, so 29 invitations fill it.
        for index in range(29):
            store.add_member(workspace.workspace_id, f"student{index}@university.edu")
        assert store.usage(workspace.workspace_id)["seats_used"] == 30
        with pytest.raises(SeatLimitExceeded):
            store.add_member(workspace.workspace_id, "one-too-many@university.edu")
