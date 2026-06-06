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
from datetime import datetime, timezone
from typing import Iterable

from .database import Database

logger = logging.getLogger(__name__)


def _redis() -> "Redis | None":  # type: ignore[name-defined]  # noqa: F821
    """Lazy import to avoid circular dependency."""
    try:
        from .redis_cache import get_redis
        return get_redis()
    except ImportError:
        return None


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
    db: Database,
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
    row = db.fetchone(
        """
        select id, username, password_hash, display_name, role, status,
               created_at, updated_at, last_login_at, password_changed_at
        from users
        where lower(username) = ? and status = 'active' and role != 'system'
        """,
        (username,),
    )
    if not row:
        return None
    if not hasher.verify(password, row["password_hash"]):
        return None
    row.pop("password_hash", None)
    return row


def touch_last_login(db: Database, user_id: str) -> None:
    now = _now_iso()
    db.execute(
        "update users set last_login_at = ?, updated_at = ? where id = ?",
        (now, now, user_id),
    )


def get_user(db: Database, user_id: str) -> dict | None:
    if not user_id:
        return None
    return db.fetchone(
        """
        select id, username, display_name, role, status,
               created_at, updated_at, last_login_at, password_changed_at
        from users where id = ?
        """,
        (user_id,),
    )


def get_user_by_username(db: Database, username: str) -> dict | None:
    username = (username or "").strip().lower()
    if not username:
        return None
    return db.fetchone(
        """
        select id, username, display_name, role, status,
               created_at, updated_at, last_login_at, password_changed_at
        from users where lower(username) = ?
        """,
        (username,),
    )


# ── Session → user resolution ────────────────────────────────────────────────


def resolve_session_to_user(db: Database, token: str) -> dict | None:
    """Look up the active control_session by token, JOIN users on actor=username.

    Returns {session: {id, actor, ...}, user: {id, username, role, ...}} or None.
    Checks Redis cache first; falls back to database query on cache miss.
    """
    token = (token or "").strip()
    if not token:
        return None
    token_hash = _hash_token(token)

    # 1. Try Redis cache.
    r = _redis()
    if r and r.available:
        cached = r.get_session(token_hash)
        if cached:
            return cached

    # 2. Database query.
    now = _now_iso()
    row = db.fetchone(
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
        (token_hash, now),
    )
    if not row:
        return None

    result = {
        "session": {
            "id": row["session_id"],
            "actor": row["session_actor"],
            "created_at": row["session_created_at"],
            "expires_at": row["session_expires_at"],
        },
        "user": {
            "id": row["user_id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "status": row["status"],
        },
    }

    # 3. Cache for subsequent lookups.
    if r and r.available:
        try:
            expire_dt = datetime.fromisoformat(row["session_expires_at"])
            ttl = max(1, int((expire_dt - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError):
            ttl = 28800
        r.set_session(token_hash, result, ttl)

    return result


# ── Control session management ───────────────────────────────────────────────


def create_control_session(
    db: Database,
    actor: str,
    *,
    source_ip: str | None = None,
    user_agent: str | None = None,
    ttl_seconds: int = 28800,
) -> dict:
    """Create a control session row and return {token, session_id, expires_at}.

    Also caches the session in Redis if configured.
    """
    token = secrets.token_urlsafe(48)
    session_id = f"ses_{os.urandom(8).hex()}"
    now = _now_iso()
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    token_hash = _hash_token(token)
    db.execute(
        """
        insert into control_sessions
          (id, actor, token_hash, source_ip, user_agent, created_at, expires_at)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, actor.strip(), token_hash, source_ip, user_agent, now, expires),
    )
    return {"token": token, "session_id": session_id, "expires_at": expires}


def revoke_control_session(db: Database, token: str) -> bool:
    """Revoke a control session by token. Returns True if a row was revoked.

    Also removes the session from Redis cache if configured.
    """
    now = _now_iso()
    token_hash = _hash_token(token)
    cur = db.execute(
        "update control_sessions set revoked_at = ? where token_hash = ? and revoked_at is null",
        (now, token_hash),
    )
    revoked = cur.rowcount > 0
    if revoked:
        r = _redis()
        if r and r.available:
            r.delete_session(token_hash)
    return revoked


def change_password(
    db: Database,
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
    row = db.fetchone(
        "select id, password_hash, status from users where id = ? and status = 'active'",
        (user_id,),
    )
    if not row:
        return "user not found or disabled"
    if not hasher.verify(current_password, row["password_hash"]):
        return "current password is incorrect"
    new_hash = hasher.hash(new_password)
    db.execute(
        "update users set password_hash = ?, password_changed_at = ?, updated_at = ? where id = ?",
        (new_hash, now, now, user_id),
    )
    return ""


# ── Scope computation ────────────────────────────────────────────────────────


def scope_for_user(db: Database, user: dict) -> dict:
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
        rows = db.fetchall(
            """
            select external_id, external_id_alt
            from user_identities
            where user_id = ?
            """,
            (user_id,),
        )
        for r in rows:
            if r["external_id"]:
                ids.append(r["external_id"])
            if r["external_id_alt"]:
                ids.append(r["external_id_alt"])
    except Exception:
        pass

    return {"all": False, "hermes_user_ids": list(set(ids))}


def is_admin(user: dict | None) -> bool:
    if not user:
        return False
    return (user.get("role") or "").strip().lower() in ("admin", "system")
