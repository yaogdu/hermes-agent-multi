"""Authentication and authorization for the Control Panel.

Reusable module shared between the standalone Control Panel (server/app)
and the Hermes Dashboard (hermes dashboard web-server integration).

Naming:
- "Control session" = operator login session (stored in control_sessions).
- "Hermes session" = chat session inside state.db (referenced by user_id).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Password hashing ────────────────────────────────────────────────────────


class PasswordHasher:
    """Password hashing using bcrypt directly (passlib is unmaintained)."""

    def __init__(self) -> None:
        import bcrypt

        self._bcrypt = bcrypt

    def hash(self, plain: str) -> str:
        plain_bytes = plain.encode("utf-8")[:72]
        return self._bcrypt.hashpw(plain_bytes, self._bcrypt.gensalt()).decode("utf-8")

    def verify(self, plain: str, hashed: str) -> bool:
        if not plain or not hashed:
            return False
        if not hashed.startswith("$"):
            return False
        try:
            plain_bytes = plain.encode("utf-8")[:72]
            hashed_bytes = hashed.encode("utf-8")
            return self._bcrypt.checkpw(plain_bytes, hashed_bytes)
        except Exception:
            logger.debug("password verify failed", exc_info=True)
            return False


# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── User lookup ──────────────────────────────────────────────────────────────


def authenticate_user(
    db_path: Path,
    username: str,
    password: str,
    *,
    hasher: PasswordHasher | None = None,
) -> dict | None:
    """Return the user dict if (username, password) matches an active user."""
    username = (username or "").strip().lower()
    if not username or not password:
        return None
    hasher = hasher or PasswordHasher()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, username, password_hash, display_name, role, status,
                   created_at, updated_at, last_login_at, password_changed_at
            from users
            where lower(username) = ? and status = 'active' and role != 'system'
            """,
            (username,),
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    if not hasher.verify(password, user["password_hash"]):
        return None
    user.pop("password_hash", None)
    return user


def touch_last_login(db_path: Path, user_id: str) -> None:
    now = _now_iso()
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                "update users set last_login_at = ?, updated_at = ? where id = ?",
                (now, now, user_id),
            )


def get_user(db_path: Path, user_id: str) -> dict | None:
    if not user_id:
        return None
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, username, display_name, role, status,
                   created_at, updated_at, last_login_at, password_changed_at
            from users where id = ?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_user_by_username(db_path: Path, username: str) -> dict | None:
    username = (username or "").strip().lower()
    if not username:
        return None
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select id, username, display_name, role, status,
                   created_at, updated_at, last_login_at, password_changed_at
            from users where lower(username) = ?
            """,
            (username,),
        ).fetchone()
    return dict(row) if row else None


# ── Session → user resolution ────────────────────────────────────────────────


def resolve_session_to_user(db_path: Path, token: str) -> dict | None:
    """Look up the active control_session by token, JOIN users on actor=username.

    Returns {session: {id, actor, ...}, user: {id, username, role, ...}} or None.
    """
    token = (token or "").strip()
    if not token:
        return None
    now = _now_iso()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select
              s.id           as session_id,
              s.actor        as session_actor,
              s.created_at   as session_created_at,
              s.expires_at   as session_expires_at,
              u.id           as user_id,
              u.username     as username,
              u.display_name as display_name,
              u.role         as role,
              u.status       as status
            from control_sessions s
            join users u on lower(u.username) = lower(s.actor)
            where s.token_hash = ? and s.revoked_at is null
              and s.expires_at > ? and u.status = 'active'
            """,
            (_hash_token(token), now),
        ).fetchone()
    if not row:
        return None
    r = dict(row)
    return {
        "session": {
            "id": r["session_id"],
            "actor": r["session_actor"],
            "created_at": r["session_created_at"],
            "expires_at": r["session_expires_at"],
        },
        "user": {
            "id": r["user_id"],
            "username": r["username"],
            "display_name": r["display_name"],
            "role": r["role"],
            "status": r["status"],
        },
    }


# ── Control session management ───────────────────────────────────────────────


def create_control_session(
    db_path: Path,
    actor: str,
    *,
    source_ip: str | None = None,
    user_agent: str | None = None,
    ttl_seconds: int = 28800,
) -> dict:
    """Create a control session row and return {token, session_id, expires_at}."""
    token = secrets.token_urlsafe(48)
    session_id = f"ses_{os.urandom(8).hex()}"
    now = _now_iso()
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            with conn:
                conn.execute(
                    """
                    insert into control_sessions
                      (id, actor, token_hash, source_ip, user_agent, created_at, expires_at)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, actor.strip(), _hash_token(token), source_ip, user_agent, now, expires),
                )
    except sqlite3.OperationalError:
        # control_sessions table might not exist yet
        raise
    return {"token": token, "session_id": session_id, "expires_at": expires}


def revoke_control_session(db_path: Path, token: str) -> bool:
    """Revoke a control session by token. Returns True if a row was revoked."""
    now = _now_iso()
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            cur = conn.execute(
                "update control_sessions set revoked_at = ? where token_hash = ? and revoked_at is null",
                (now, _hash_token(token)),
            )
    return cur.rowcount > 0


def change_password(
    db_path: Path,
    user_id: str,
    current_password: str,
    new_password: str,
    *,
    hasher: PasswordHasher | None = None,
) -> str:
    """Change password for a user. Returns error string or empty string on success.

    Validates: current_password correct, new_password >= 8 chars, user active.
    """
    if not isinstance(new_password, str) or len(new_password) < 8:
        return "new password must be at least 8 characters"
    hasher = hasher or PasswordHasher()
    now = _now_iso()
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select id, password_hash, status from users where id = ? and status = 'active'",
            (user_id,),
        ).fetchone()
    if not row:
        return "user not found or disabled"
    user = dict(row)
    if not hasher.verify(current_password, user["password_hash"]):
        return "current password is incorrect"
    new_hash = hasher.hash(new_password)
    with closing(sqlite3.connect(str(db_path))) as conn:
        with conn:
            conn.execute(
                "update users set password_hash = ?, password_changed_at = ?, updated_at = ? where id = ?",
                (new_hash, now, now, user_id),
            )
    return ""


# ── Scope computation ────────────────────────────────────────────────────────


def scope_for_user(db_path: Path, user: dict) -> dict:
    """Compute the data scope for a user.

    Returns {all: bool, hermes_user_ids: list[str]}.
    - admin/system → all=true (sees everything)
    - user → all=false, hermes_user_ids from bound identities
    """
    role = (user.get("role") or "").strip().lower()
    if role in ("admin", "system"):
        return {"all": True, "hermes_user_ids": []}

    user_id = user.get("id")
    if not user_id:
        return {"all": False, "hermes_user_ids": []}

    ids: list[str] = []
    try:
        with closing(sqlite3.connect(str(db_path))) as conn:
            rows = conn.execute(
                """
                select external_id, external_id_alt
                from user_identities
                where user_id = ?
                """,
                (user_id,),
            ).fetchall()
        for r in rows:
            if r[0]:
                ids.append(r[0])
            if r[1]:
                ids.append(r[1])
    except sqlite3.OperationalError:
        pass

    return {"all": False, "hermes_user_ids": list(set(ids))}


def is_admin(user: dict | None) -> bool:
    if not user:
        return False
    return (user.get("role") or "").strip().lower() in ("admin", "system")
