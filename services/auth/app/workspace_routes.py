"""Workspace routes: classrooms, labs, departments (ADR-0047).

* ``POST /workspaces`` -- owner must be on a lab or department plan
  (classrooms are taught by paying instructors; the seat itself is free
  for students, which is the point of the teaching loop).
* ``GET /workspaces/{id}`` -- member only.
* ``POST /workspaces/{id}/members`` -- owner only; adds by email.
* ``DELETE /workspaces/{id}/members/{uid}`` -- owner only.
* ``GET /workspaces/{id}/usage`` -- owner only; the instructor dashboard
  (per-member usage, activation, retention).
* ``POST /workspaces/{id}/pool/topup`` -- buy more pool tokens via Stripe.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .billing_routes import StripeClient, require_plan
from .workspaces import WorkspaceError, WorkspaceStore

__all__ = ["install_workspace_routes"]


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., description="classroom | lab | department")


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=320)


class PoolTopupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int = Field(..., gt=0, le=1_000_000)


def install_workspace_routes(
    app,
    *,
    workspaces: WorkspaceStore,
    stripe: StripeClient,
) -> None:
    def _user(request: Request):
        user = getattr(request.state, "user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        return user

    def _workspace_or_404(workspace_id: str):
        workspace = workspaces.get(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail=f"unknown workspace '{workspace_id}'")
        return workspace

    def _require_owner(workspace, uid: str) -> None:
        if workspace.owner_uid != uid:
            raise HTTPException(
                status_code=403, detail="only the workspace owner may do this"
            )

    def _require_member(workspace, uid: str) -> None:
        if uid != workspace.owner_uid and uid not in workspace.members:
            raise HTTPException(status_code=403, detail="not a member of this workspace")

    @app.post("/workspaces")
    def create_workspace(request: CreateWorkspaceRequest, http_request: Request) -> dict:
        user = _user(http_request)
        require_plan(http_request, "lab")
        try:
            workspace = workspaces.create(
                name=request.name, type=request.type, owner_uid=user.uid
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return workspace.snapshot()

    @app.get("/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str, http_request: Request) -> dict:
        user = _user(http_request)
        workspace = _workspace_or_404(workspace_id)
        _require_member(workspace, user.uid)
        return workspace.snapshot()

    @app.post("/workspaces/{workspace_id}/members")
    def add_member(workspace_id: str, request: AddMemberRequest, http_request: Request) -> dict:
        user = _user(http_request)
        workspace = _workspace_or_404(workspace_id)
        _require_owner(workspace, user.uid)
        try:
            member = workspaces.add_member(workspace_id, request.email)
        except WorkspaceError as exc:
            # Seat limits are the constraint the instructor manages; 409
            # (state conflict) rather than 422 (bad request).
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "uid": member.uid,
            "email": member.email,
            "status": member.status,
            "invite": "recorded; the Firebase invite email is sent by the "
            "deployment when credentials are configured",
        }

    @app.delete("/workspaces/{workspace_id}/members/{uid}")
    def remove_member(workspace_id: str, uid: str, http_request: Request) -> dict:
        user = _user(http_request)
        workspace = _workspace_or_404(workspace_id)
        _require_owner(workspace, user.uid)
        if uid == workspace.owner_uid:
            raise HTTPException(status_code=409, detail="cannot remove the owner")
        removed = workspaces.remove_member(workspace_id, uid)
        if not removed:
            raise HTTPException(status_code=404, detail=f"member '{uid}' not found")
        return {"removed": uid}

    @app.get("/workspaces/{workspace_id}/usage")
    def workspace_usage(workspace_id: str, http_request: Request) -> dict:
        user = _user(http_request)
        workspace = _workspace_or_404(workspace_id)
        _require_owner(workspace, user.uid)
        try:
            return workspaces.usage(workspace_id)
        except WorkspaceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/workspaces/{workspace_id}/pool/topup")
    def pool_topup(
        workspace_id: str, request: PoolTopupRequest, http_request: Request
    ) -> dict:
        user = _user(http_request)
        workspace = _workspace_or_404(workspace_id)
        _require_owner(workspace, user.uid)
        # The checkout session carries the topup; tokens are granted by the
        # webhook in production.  In dry-run the session is the mock's, and
        # the test wires the webhook to the same store.
        session = stripe.create_checkout_session(
            user_id=user.uid, user_email=user.email, plan=f"pool_topup_{request.tokens}"
        )
        return {
            "checkout_url": session["checkout_url"],
            "session_id": session["session_id"],
            "tokens": request.tokens,
            "note": "pool tokens are granted when checkout completes",
        }
