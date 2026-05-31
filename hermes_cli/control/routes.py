"""Control Panel API routes — mounted on the Hermes Dashboard web server.

Provides:
- ``/api/auth/*`` — login, logout, me, change-password
- ``/api/admin/users/*`` — user CRUD (admin only)
- ``/api/admin/identities/*`` — identity binding management (admin only)
- ``/api/admin/group-owners/*`` — group ownership management (admin only)

All endpoints require a valid control session token via ``X-AgentOps-Session``
header, except ``/api/auth/login`` which creates the session.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from .auth import (
    authenticate_user,
    change_password,
    create_control_session,
    is_admin,
    resolve_session_to_user,
    revoke_control_session,
    scope_for_user,
    touch_last_login,
)
from .group_owners import (
    get_group_owner,
    list_group_owners,
    reassign_group_owner,
)
from .users import (
    add_identity,
    create_user,
    get_identity,
    list_identities,
    list_users,
    remove_identity,
    transfer_identity,
    update_user,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Set by web_server.start_server — the control panel's own sqlite database.
_control_db_path: Path | None = None


def set_control_db_path(path: Path) -> None:
    global _control_db_path
    _control_db_path = path


def get_control_db_path() -> Path | None:
    return _control_db_path


def _get_db() -> Path:
    if _control_db_path is None:
        raise HTTPException(status_code=500, detail="Control database not configured")
    return _control_db_path


# ── Pydantic models ────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    session_id: str
    expires_at: str
    user: dict


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    display_name: str | None = None


class UpdateUserRequest(BaseModel):
    role: str | None = None
    status: str | None = None
    display_name: str | None = None
    password: str | None = None


class AddIdentityRequest(BaseModel):
    user_id: str
    platform: str
    external_id: str
    external_id_alt: str | None = None
    display_name: str | None = None


class TransferIdentityRequest(BaseModel):
    new_user_id: str


class ReassignGroupOwnerRequest(BaseModel):
    new_external_id: str
    new_external_id_alt: str | None = None
    notes: str | None = None


# ── Auth middleware for control routes ──────────────────────────────────────────

_SESSION_HEADER = "x-agentops-session"


async def _require_auth(
    request: Request,
    x_agentops_session: str | None = Header(None, alias=_SESSION_HEADER),
) -> dict:
    """FastAPI dependency: resolve the control session token to a user.

    Raises 401 if the token is missing, expired, or the user is disabled.
    Returns the resolved ``{session, user}`` dict from
    :func:`resolve_session_to_user`.
    """
    token = (x_agentops_session or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing session token")
    db = _get_db()
    resolved = resolve_session_to_user(db, token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    resolved["_db"] = db
    return resolved


def _require_admin(auth: dict = Depends(_require_auth)) -> dict:
    """FastAPI dependency: require admin role."""
    if not is_admin(auth["user"]):
        raise HTTPException(status_code=403, detail="Admin access required")
    return auth


# ── Auth endpoints ─────────────────────────────────────────────────────────────


@router.post("/api/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request):
    db = _get_db()
    user = authenticate_user(db, body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    touch_last_login(db, user["id"])
    session = create_control_session(
        db,
        actor=user["username"],
        source_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "token": session["token"],
        "session_id": session["session_id"],
        "expires_at": session["expires_at"],
        "user": user,
    }


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    x_agentops_session: str | None = Header(None, alias=_SESSION_HEADER),
):
    token = (x_agentops_session or "").strip()
    if token:
        revoke_control_session(_get_db(), token)
    return {"ok": True}


@router.get("/api/auth/me")
async def me(auth: dict = Depends(_require_auth)):
    db = auth["_db"]
    scope = scope_for_user(db, auth["user"])
    return {
        "user": auth["user"],
        "session": auth["session"],
        "scope": scope,
    }


@router.post("/api/auth/change-password")
async def change_pwd(
    body: ChangePasswordRequest,
    auth: dict = Depends(_require_auth),
):
    db = auth["_db"]
    err = change_password(
        db,
        auth["user"]["id"],
        body.current_password,
        body.new_password,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)
    return {"ok": True}


# ── Admin: Users ───────────────────────────────────────────────────────────────


@router.get("/api/admin/users")
async def admin_list_users(
    role: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth: dict = Depends(_require_admin),
):
    return list_users(
        _get_db(),
        role=role,
        status=status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/api/admin/users")
async def admin_create_user(
    body: CreateUserRequest,
    auth: dict = Depends(_require_admin),
):
    try:
        user = create_user(
            _get_db(),
            username=body.username,
            password=body.password,
            role=body.role,
            display_name=body.display_name,
            created_by=auth["user"]["username"],
        )
        return user
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/admin/users/{user_id}")
async def admin_get_user(
    user_id: str,
    auth: dict = Depends(_require_admin),
):
    from .users import get_user
    user = get_user(_get_db(), user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    body: UpdateUserRequest,
    auth: dict = Depends(_require_admin),
):
    try:
        user = update_user(
            _get_db(),
            user_id,
            role=body.role,
            status=body.status,
            display_name=body.display_name,
            password=body.password,
            updated_by=auth["user"]["username"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# ── Admin: Identities ──────────────────────────────────────────────────────────


@router.get("/api/admin/identities")
async def admin_list_identities(
    user_id: str | None = None,
    platform: str | None = None,
    unassigned_only: bool = False,
    auth: dict = Depends(_require_admin),
):
    return list_identities(
        _get_db(),
        user_id=user_id,
        platform=platform,
        unassigned_only=unassigned_only,
    )


@router.post("/api/admin/identities")
async def admin_add_identity(
    body: AddIdentityRequest,
    auth: dict = Depends(_require_admin),
):
    try:
        return add_identity(
            _get_db(),
            user_id=body.user_id,
            platform=body.platform,
            external_id=body.external_id,
            external_id_alt=body.external_id_alt,
            display_name=body.display_name,
            bound_by=auth["user"]["username"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/api/admin/identities/{identity_id}")
async def admin_remove_identity(
    identity_id: str,
    auth: dict = Depends(_require_admin),
):
    ok = remove_identity(_get_db(), identity_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Identity not found")
    return {"ok": True}


@router.post("/api/admin/identities/{identity_id}/transfer")
async def admin_transfer_identity(
    identity_id: str,
    body: TransferIdentityRequest,
    auth: dict = Depends(_require_admin),
):
    try:
        result = transfer_identity(
            _get_db(),
            identity_id,
            body.new_user_id,
            transferred_by=auth["user"]["username"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    return result


# ── Admin: Group Owners ────────────────────────────────────────────────────────


@router.get("/api/admin/group-owners")
async def admin_list_group_owners(
    platform: str | None = None,
    limit: int = 200,
    auth: dict = Depends(_require_admin),
):
    return list_group_owners(_get_db(), platform=platform, limit=limit)


@router.post("/api/admin/group-owners/{group_id}/reassign")
async def admin_reassign_group_owner(
    group_id: str,
    body: ReassignGroupOwnerRequest,
    auth: dict = Depends(_require_admin),
):
    try:
        return reassign_group_owner(
            _get_db(),
            group_id,
            new_external_id=body.new_external_id,
            new_external_id_alt=body.new_external_id_alt,
            notes=body.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
