"""Cross-platform session listing for the /sessions slash command.

Resolves the caller's identity (e.g. Feishu open_id) to the set of all
external_ids bound to the same Control Panel user (across Feishu, Telegram,
Discord, ...), then queries Hermes' state.db for sessions matching any
of those external_ids.

No HTTP — reads agentops_control.db and state.db directly via the shared
Docker volume. Failures are silent: an unreachable Control Panel db means
we just list the single-identity sessions, never error out.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ENV_DB_PATH = "AGENTOPS_CONTROL_DB_PATH"


def _resolve_control_db() -> Optional[Path]:
    raw = os.getenv(_ENV_DB_PATH, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def _normalize_platform(platform: Any) -> str:
    if platform is None:
        return ""
    raw = getattr(platform, "value", None) or str(platform)
    return raw.strip().lower()


def resolve_user_identity_ids(*, platform: Any, external_id: str) -> list[str]:
    """Return all external_ids that belong to the same person as the given
    (platform, external_id).

    Resolution order:
      1. Match the input against user_identities.external_id directly.
      2. If no hit, match against user_identities.external_id_alt (so e.g.
         an open_id can find the person whose canonical id is union_id but
         who has open_id stored as an alias).
      3. If we land on a real user (not system), return *all* external_ids
         AND external_id_alts under that user — covers cross-platform and
         multi-ID-per-platform cases.
      4. If the identity isn't bound to a real user, return just the
         original input so callers can still try the literal match against
         state.db.

    Always includes the original input id at the head so a session row
    holding the input id is always queryable.
    """
    norm_platform = _normalize_platform(platform)
    norm_external = (external_id or "").strip()
    if not norm_platform or not norm_external:
        return []
    db_path = _resolve_control_db()
    if db_path is None:
        return [norm_external]
    try:
        with closing(sqlite3.connect(str(db_path), timeout=2.0)) as conn:
            conn.row_factory = sqlite3.Row
            # Step 1: direct match, or as alias.
            row = conn.execute(
                """
                select id, user_id, external_id, external_id_alt
                from user_identities
                where platform = ? and (external_id = ? or external_id_alt = ?)
                """,
                (norm_platform, norm_external, norm_external),
            ).fetchone()
            if not row:
                return [norm_external]

            # Always trust the canonical/alias pair on this very row — it's
            # platform-attested that they belong to the same human, even if
            # the row hasn't been adopted by a real Control Panel account
            # yet. This is what lets a fresh open_id-keyed session find
            # the identity that was registered under union_id.
            ids: list[str] = []
            if row["external_id"]:
                ids.append(row["external_id"])
            if row["external_id_alt"]:
                ids.append(row["external_id_alt"])

            # If the row is owned by a real user (not the auto-discovered
            # system bucket), pull in ids from every other identity bound
            # to the same user — that's how cross-platform aggregation
            # works (Feishu + Telegram on one account).
            user_id = row["user_id"]
            if user_id and user_id != "usr_system":
                more_rows = conn.execute(
                    """
                    select external_id, external_id_alt
                    from user_identities
                    where user_id = ? and id != ?
                    """,
                    (user_id, row["id"]),
                ).fetchall()
                for r in more_rows:
                    if r["external_id"]:
                        ids.append(r["external_id"])
                    if r["external_id_alt"]:
                        ids.append(r["external_id_alt"])

            if norm_external not in ids:
                ids.append(norm_external)
            # De-dup while preserving order so the original input comes first.
            seen: set[str] = set()
            unique: list[str] = []
            for x in ids:
                if x not in seen:
                    seen.add(x)
                    unique.append(x)
            return unique
    except Exception:
        logger.debug(
            "resolve_user_identity_ids: db access failed for %s/%s",
            platform, external_id, exc_info=True,
        )
        return [norm_external]


def list_my_sessions(
    *,
    state_db_path: Path,
    platform: Any,
    external_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """List Hermes sessions owned by the caller across every platform their
    Control Panel account is bound to. Sorted newest-first.

    Each dict carries enough info to render an interactive card:
      session_id, source, agent_key, title, message_count,
      estimated_cost_usd, started_at (ISO string), last_message_at
    """
    external_ids = resolve_user_identity_ids(platform=platform, external_id=external_id)
    if not external_ids:
        return []
    if not state_db_path or not Path(state_db_path).exists():
        return []
    placeholders = ",".join("?" * len(external_ids))
    try:
        with closing(sqlite3.connect(f"file:{state_db_path}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                select
                    s.id            as session_id,
                    s.source        as source_json,
                    s.title,
                    s.message_count,
                    s.estimated_cost_usd,
                    s.started_at,
                    (
                        select max(m.timestamp)
                        from messages m
                        where m.session_id = s.id
                    ) as last_message_ts
                from sessions s
                where s.user_id in ({placeholders})
                order by coalesce(last_message_ts, s.started_at) desc
                limit ? offset ?
                """,
                (*external_ids, max(1, min(int(limit), 200)), max(0, int(offset))),
            ).fetchall()

            # Fetch a preview message for each session in one extra query.
            # Pick the most recent user OR assistant message (skip tool I/O).
            # Use a per-session subquery; OK for small page sizes (<=50).
            previews: dict[str, dict] = {}
            if rows:
                session_ids = [r["session_id"] for r in rows]
                ph = ",".join("?" * len(session_ids))
                # For each session, find the row in messages with the largest
                # timestamp where role in ('user', 'assistant').
                preview_rows = conn.execute(
                    f"""
                    select m.session_id, m.role, m.content
                    from messages m
                    join (
                        select session_id, max(timestamp) as ts
                        from messages
                        where session_id in ({ph})
                          and role in ('user', 'assistant')
                          and content is not null and content != ''
                        group by session_id
                    ) latest
                      on latest.session_id = m.session_id
                     and latest.ts = m.timestamp
                    """,
                    tuple(session_ids),
                ).fetchall()
                for pr in preview_rows:
                    if pr["session_id"] not in previews:
                        previews[pr["session_id"]] = dict(pr)
    except Exception:
        logger.debug("list_my_sessions: state.db query failed", exc_info=True)
        return []

    out: list[dict] = []
    for r in rows:
        try:
            src = json.loads(r["source_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            src = {}
        pv = previews.get(r["session_id"])
        out.append({
            "session_id": r["session_id"],
            "platform": (src.get("platform") or "?") if isinstance(src, dict) else "?",
            "agent_key": (src.get("agent_key") or "main") if isinstance(src, dict) else "main",
            "title": r["title"] or "",
            "message_count": int(r["message_count"] or 0),
            "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
            "started_at": _iso_from_unix(r["started_at"]),
            "last_message_at": _iso_from_unix(r["last_message_ts"]),
            "last_message_preview": (pv["content"] if pv else "") or "",
            "last_message_role": (pv["role"] if pv else "") or "",
        })
    return out


def _iso_from_unix(value: Any) -> Optional[str]:
    """Convert a unix timestamp (float|int|None) to ISO string. Hermes
    state.db stores these as floats."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def count_my_sessions(
    *,
    state_db_path: Path,
    platform: Any,
    external_id: str,
) -> int:
    """Total session count for the caller (used for pagination footer)."""
    external_ids = resolve_user_identity_ids(platform=platform, external_id=external_id)
    if not external_ids or not state_db_path or not Path(state_db_path).exists():
        return 0
    placeholders = ",".join("?" * len(external_ids))
    try:
        with closing(sqlite3.connect(f"file:{state_db_path}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                f"select count(*) from sessions where user_id in ({placeholders})",
                tuple(external_ids),
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        logger.debug("count_my_sessions failed", exc_info=True)
        return 0
