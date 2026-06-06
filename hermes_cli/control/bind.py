"""Self-service identity binding for the /bind slash command.

Users can bind their chat-platform identity (e.g. Feishu open_id) to a
Control Panel user account without admin intervention.

Flow:
1. User logs into Control Panel → profile → "Generate binding code"
2. User copies the 6-character code
3. User types ``/bind <code>`` in chat
4. Gateway verifies the code and creates the user_identities row
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

from .database import Database

CODE_LENGTH = 6
CODE_TTL_MINUTES = 10


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_code() -> str:
    """Generate a short alphanumeric code easy to type on mobile."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/0/O/1
    return "".join(secrets.choice(alphabet) for _ in range(CODE_LENGTH))


def generate_bind_code(db: Database, user_id: str) -> str:
    """Create a one-time binding code for *user_id*. Returns the code string.

    Expired codes are cleaned up before generation.
    """
    _cleanup_expired(db)
    code = _generate_code()
    now = _now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)).isoformat()
    bind_id = f"pbd_{os.urandom(8).hex()}"
    db.execute(
        """
        insert into pending_bindings (id, code, user_id, created_at, expires_at)
        values (?, ?, ?, ?, ?)
        """,
        (bind_id, code, user_id, now, expires),
    )
    return code


def verify_and_bind(
    db: Database,
    *,
    code: str,
    platform: str,
    external_id: str,
    external_id_alt: str | None = None,
    display_name: str | None = None,
) -> dict | None:
    """Verify a binding code and create the user_identities row.

    Returns the created identity dict, or None if the code is invalid/expired.
    Raises ValueError if the identity already exists (unique constraint).
    """
    code = (code or "").strip().upper()
    if not code:
        return None

    _cleanup_expired(db)

    now = _now()
    row = db.fetchone(
        """
        select id, user_id from pending_bindings
        where code = ? and used_at is null and expires_at > ?
        """,
        (code, now),
    )
    if not row:
        return None

    user_id = row["user_id"]

    # Check if identity already exists
    existing = db.fetchone(
        """
        select id from user_identities
        where platform = ? and external_id = ?
        """,
        (platform.strip().lower(), external_id.strip()),
    )
    if existing:
        raise ValueError("identity already bound to a user")

    # Mark code as used
    db.execute(
        "update pending_bindings set used_at = ? where id = ?",
        (now, row["id"]),
    )

    # Create identity
    from .users import add_identity
    return add_identity(
        db,
        user_id=user_id,
        platform=platform,
        external_id=external_id,
        external_id_alt=external_id_alt,
        display_name=display_name,
        bound_by="self-bind",
    )


def get_binding_status(db: Database, platform: str, external_id: str) -> dict | None:
    """Check if an external identity is bound to a user.

    Returns {identity_id, user_id, username, display_name, ...} or None.
    """
    row = db.fetchone(
        """
        select i.id, i.user_id, i.platform, i.external_id,
               i.display_name, i.bound_at, u.username, u.display_name as user_display_name
        from user_identities i
        join users u on u.id = i.user_id
        where i.platform = ? and i.external_id = ?
        """,
        (platform.strip().lower(), external_id.strip()),
    )
    return dict(row) if row else None


def _cleanup_expired(db: Database) -> None:
    """Remove expired, unused binding codes."""
    now = _now()
    db.execute(
        "delete from pending_bindings where used_at is null and expires_at <= ?",
        (now,),
    )
