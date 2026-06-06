"""Group ownership tracking.

A group chat's "owner" is the user who first @Bot'd the bot in that chat.
That owner's external_id then drives sessions.user_id for every session in
the same chat, so the chat's conversation history ends up attributed to one
human (visible to that human in the panel) instead of spreading across
whoever happened to speak.

This module is read by the Control Panel and written by:
  - Control Panel (admin reassign)
  - Hermes Gateway (first-@Bot claim)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .database import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_group_owner(
    db: Database,
    platform: str,
    chat_id: str,
) -> dict | None:
    return db.fetchone(
        """
        select id, platform, chat_id, owner_external_id, owner_user_id_alt,
               established_at, established_session_id, notes
        from group_owners
        where platform = ? and chat_id = ?
        """,
        (platform.strip().lower(), chat_id.strip()),
    )


def claim_group_owner(
    db: Database,
    *,
    platform: str,
    chat_id: str,
    owner_external_id: str,
    owner_user_id_alt: str | None = None,
    session_id: str | None = None,
    notes: str | None = None,
) -> tuple[dict, bool]:
    """Try to register owner_external_id as the group's owner.

    Returns (record, created). `created=False` means someone else got there
    first; the returned record reflects the existing owner.
    """
    platform = platform.strip().lower()
    chat_id = chat_id.strip()
    if not platform or not chat_id or not owner_external_id:
        raise ValueError("platform, chat_id, owner_external_id required")
    identity_id = f"grp_{os.urandom(8).hex()}"
    now = _now()
    cur = db.execute(
        """
        insert or ignore into group_owners
          (id, platform, chat_id, owner_external_id, owner_user_id_alt,
           established_at, established_session_id, notes)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            identity_id,
            platform,
            chat_id,
            owner_external_id,
            owner_user_id_alt,
            now,
            session_id,
            notes,
        ),
    )
    created = cur.rowcount > 0
    row = db.fetchone(
        """
        select id, platform, chat_id, owner_external_id, owner_user_id_alt,
               established_at, established_session_id, notes
        from group_owners
        where platform = ? and chat_id = ?
        """,
        (platform, chat_id),
    )
    return row, created


def list_group_owners(
    db: Database,
    *,
    platform: str | None = None,
    limit: int = 200,
) -> list[dict]:
    where: list[str] = []
    params: list[str] = []
    if platform:
        where.append("platform = ?")
        params.append(platform.strip().lower())
    sql = """
        select id, platform, chat_id, owner_external_id, owner_user_id_alt,
               established_at, established_session_id, notes
        from group_owners
    """
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by established_at desc limit ?"
    params.append(str(max(1, min(int(limit), 1000))))
    return db.fetchall(sql, tuple(params))


def reassign_group_owner(
    db: Database,
    group_id: str,
    *,
    new_external_id: str,
    new_external_id_alt: str | None = None,
    notes: str | None = None,
) -> dict:
    new_external_id = (new_external_id or "").strip()
    if not group_id or not new_external_id:
        raise ValueError("group_id and new_external_id required")
    cur = db.execute(
        """
        update group_owners
        set owner_external_id = ?,
            owner_user_id_alt = ?,
            notes = coalesce(?, notes)
        where id = ?
        """,
        (new_external_id, new_external_id_alt, notes, group_id),
    )
    if cur.rowcount == 0:
        raise ValueError(f"group_owner not found: {group_id}")
    row = db.fetchone(
        """
        select id, platform, chat_id, owner_external_id, owner_user_id_alt,
               established_at, established_session_id, notes
        from group_owners where id = ?
        """,
        (group_id,),
    )
    return row
