"""User and identity management helpers.

Reusable module shared between the standalone Control Panel and the
Hermes Dashboard integration.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .auth import PasswordHasher

_USERNAME_RE = re.compile(r"^[a-z0-9_.-]{2,32}$")
_VALID_ROLES = {"user", "admin"}
_VALID_STATUS = {"active", "disabled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def _validate_username(username: str) -> str:
    norm = _normalize_username(username)
    if not _USERNAME_RE.match(norm):
        raise ValueError("username must be 2-32 chars, lowercase letters/digits/._- only")
    if norm == "system":
        raise ValueError("username 'system' is reserved")
    return norm


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("password must be at least 8 characters")


def _validate_role(role: str) -> str:
    role = (role or "").strip().lower()
    if role not in _VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
    return role


def _validate_status(status: str) -> str:
    status = (status or "").strip().lower()
    if status not in _VALID_STATUS:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUS)}")
    return status


# ── User CRUD ────────────────────────────────────────────────────────────────


def create_user(
    db_path: Path,
    *,
    username: str,
    password: str,
    role: str = "user",
    display_name: str | None = None,
    created_by: str | None = None,
    hasher: PasswordHasher | None = None,
) -> dict:
    norm_username = _validate_username(username)
    _validate_password(password)
    norm_role = _validate_role(role)
    hasher = hasher or PasswordHasher()
    user_id = f"usr_{os.urandom(8).hex()}"
    now = _now()
    display = (display_name or username).strip() or norm_username
    with closing(sqlite3.connect(str(db_path))) as conn:
        try:
            with conn:
                conn.execute(
                    """
                    insert into users
                      (id, username, password_hash, display_name, role, status,
                       created_at, updated_at, password_changed_at)
                    values (?, ?, ?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (user_id, norm_username, hasher.hash(password), display, norm_role, now, now, now),
                )
        except sqlite3.IntegrityError as e:
            msg = str(e).lower()
            if "unique" in msg or "username" in msg:
                raise ValueError(f"username '{norm_username}' already exists")
            raise
    return get_user(db_path, user_id)  # type: ignore


def get_user(db_path: Path, user_id: str) -> dict | None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, username, display_name, role, status,
                   created_at, updated_at, last_login_at
            from users where id = ?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def list_users(
    db_path: Path,
    *,
    role: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    clauses = ["where 1=1"]
    params: list = []
    if role:
        clauses.append("and role = ?")
        params.append(_validate_role(role))
    if status:
        clauses.append("and status = ?")
        params.append(_validate_status(status))
    if search:
        clauses.append("and (username like ? or display_name like ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    clauses.append("order by username asc")
    clauses.append("limit ? offset ?")
    params.extend([limit, offset])
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            select id, username, display_name, role, status,
                   created_at, updated_at, last_login_at
            from users
            {' '.join(clauses)}
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def update_user(
    db_path: Path,
    user_id: str,
    *,
    role: str | None = None,
    status: str | None = None,
    display_name: str | None = None,
    password: str | None = None,
    updated_by: str | None = None,
    hasher: PasswordHasher | None = None,
) -> dict | None:
    """Update user fields. password=None means don't change password."""
    existing = get_user(db_path, user_id)
    if not existing:
        return None
    now = _now()
    sets: list[str] = ["updated_at = ?"]
    params: list = [now]
    if role is not None:
        sets.append("role = ?")
        params.append(_validate_role(role))
    if status is not None:
        sets.append("status = ?")
        params.append(_validate_status(status))
    if display_name is not None:
        sets.append("display_name = ?")
        params.append((display_name or "").strip())
    if password is not None:
        _validate_password(password)
        hasher = hasher or PasswordHasher()
        sets.append("password_hash = ?")
        sets.append("password_changed_at = ?")
        params.append(hasher.hash(password))
        params.append(now)
    params.append(user_id)
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                f"update users set {', '.join(sets)} where id = ?",
                params,
            )
    return get_user(db_path, user_id)


def count_users(
    db_path: Path,
    *,
    role: str | None = None,
    status: str | None = None,
) -> int:
    clauses = ["where 1=1"]
    params: list = []
    if role:
        clauses.append("and role = ?")
        params.append(role)
    if status:
        clauses.append("and status = ?")
        params.append(status)
    with closing(sqlite3.connect(str(db_path))) as conn:
        row = conn.execute(
            f"select count(*) from users {' '.join(clauses)}",
            params,
        ).fetchone()
    return int(row[0]) if row else 0


# ── Identity binding ─────────────────────────────────────────────────────────


def list_identities(
    db_path: Path,
    *,
    user_id: str | None = None,
    platform: str | None = None,
    unassigned_only: bool = False,
) -> list[dict]:
    clauses = ["where 1=1"]
    params: list = []
    if user_id:
        clauses.append("and i.user_id = ?")
        params.append(user_id)
    if platform:
        clauses.append("and i.platform = ?")
        params.append(platform.strip().lower())
    if unassigned_only:
        clauses.append("and i.user_id = 'usr_system'")
    clauses.append("order by i.platform, i.external_id")
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            select i.id, i.user_id, i.platform, i.external_id, i.external_id_alt,
                   i.display_name, i.bound_at, i.bound_by,
                   u.username, u.display_name as user_display_name
            from user_identities i
            left join users u on u.id = i.user_id
            {' '.join(clauses)}
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def add_identity(
    db_path: Path,
    *,
    user_id: str,
    platform: str,
    external_id: str,
    external_id_alt: str | None = None,
    display_name: str | None = None,
    bound_by: str = "admin",
) -> dict:
    if not platform or not external_id:
        raise ValueError("platform and external_id required")
    identity_id = f"idt_{os.urandom(8).hex()}"
    now = _now()
    platform = platform.strip().lower()
    external_id = external_id.strip()
    with closing(sqlite3.connect(str(db_path))) as conn:
        try:
            with conn:
                conn.execute(
                    """
                    insert into user_identities
                      (id, user_id, platform, external_id, external_id_alt,
                       display_name, bound_at, bound_by)
                    values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (identity_id, user_id, platform, external_id, external_id_alt,
                     display_name, now, bound_by),
                )
        except sqlite3.IntegrityError:
            raise ValueError(f"identity already exists: {platform}/{external_id}")
    return get_identity(db_path, identity_id)  # type: ignore


def remove_identity(db_path: Path, identity_id: str) -> bool:
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            cur = conn.execute("delete from user_identities where id = ?", (identity_id,))
    return cur.rowcount > 0


def transfer_identity(
    db_path: Path,
    identity_id: str,
    new_user_id: str,
    *,
    transferred_by: str = "admin",
) -> dict | None:
    """Transfer an identity binding from one user to another."""
    existing = get_identity(db_path, identity_id)
    if not existing:
        return None
    now = _now()
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                "update user_identities set user_id = ?, bound_at = ?, bound_by = ? where id = ?",
                (new_user_id, now, transferred_by, identity_id),
            )
    return get_identity(db_path, identity_id)


def get_identity(db_path: Path, identity_id: str) -> dict | None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select i.id, i.user_id, i.platform, i.external_id, i.external_id_alt,
                   i.display_name, i.bound_at, i.bound_by,
                   u.username, u.display_name as user_display_name
            from user_identities i
            left join users u on u.id = i.user_id
            where i.id = ?
            """,
            (identity_id,),
        ).fetchone()
    return dict(row) if row else None
