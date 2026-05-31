"""Gateway-side hooks into the Control Panel's identity & ownership tables.

This module is the *gateway-side* counterpart of `server/app/group_owners.py`
and `server/app/users.py`. It opens the shared sqlite file directly so the
gateway stays deployable independently of the Control Panel.

If `AGENTOPS_CONTROL_DB_PATH` is not configured, every call is a no-op —
gateway behavior is unchanged.

Two responsibilities:
  - resolve_or_claim_group_owner: first human to message a group chat becomes
    the owner; all subsequent sessions in that chat attribute to them.
  - upsert_identity_display_name: opportunistically populate
    user_identities.display_name from real-time sender names, so admins see
    "ou_xxx (张三)" instead of opaque ids in the Control Panel.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_ENV_DB_PATH = "AGENTOPS_CONTROL_DB_PATH"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_platform(platform) -> str:
    """Accept either a Platform enum (gateway.session.Platform.FEISHU) or
    a plain string. Returns a lowercased string like 'feishu'."""
    if platform is None:
        return ""
    # Platform enum has a .value attribute; falling back to str() handles
    # custom adapter platform identifiers that aren't part of the enum.
    raw = getattr(platform, "value", None) or str(platform)
    return raw.strip().lower()


def _resolve_db_path() -> Optional[Path]:
    raw = os.getenv(_ENV_DB_PATH, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


# How long the gateway should trust an identity's display_name + metadata
# without re-hitting the platform contact API. Refreshes after this so
# job titles / phone numbers stay reasonably current, but the typical
# session-create path doesn't pay an HTTP roundtrip every time.
_IDENTITY_FRESH_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def is_identity_fresh(*, platform: str, external_id: str) -> bool:
    """Return True if (platform, external_id) is already in user_identities
    with a real (non-placeholder) display_name OR concrete contact info,
    and was bound within the freshness window.

    Used by platform adapters to skip the contact API call when there's
    already enough info on file. Cheap (one indexed SELECT). Safe to call
    from anywhere — returns False on any error or when the db is offline.
    """
    db_path = _resolve_db_path()
    if db_path is None:
        return False
    norm_platform = _normalize_platform(platform)
    norm_external = (external_id or "").strip()
    if not norm_platform or not norm_external:
        return False
    try:
        with sqlite3.connect(str(db_path), timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select display_name, metadata_json, bound_at
                from user_identities
                where platform = ? and external_id = ?
                """,
                (norm_platform, norm_external),
            ).fetchone()
        if not row:
            return False
        # Age check
        try:
            bound = datetime.fromisoformat(row["bound_at"])
            age = (datetime.now(timezone.utc) - bound).total_seconds()
            if age > _IDENTITY_FRESH_TTL_SECONDS:
                return False
        except Exception:
            return False
        # Content check: we consider it "fresh enough" when we have either
        # a real-looking display_name (not a legacy/auto-discovered placeholder)
        # OR concrete metadata fields (email / mobile / employee_no).
        display_name = (row["display_name"] or "").strip()
        has_real_name = bool(display_name) and not display_name.startswith("legacy ")
        if has_real_name:
            return True
        if row["metadata_json"]:
            try:
                import json as _json
                meta = _json.loads(row["metadata_json"])
                if isinstance(meta, dict):
                    if any(meta.get(k) for k in ("email", "mobile", "employee_no")):
                        return True
            except Exception:
                pass
        return False
    except Exception:
        logger.debug(
            "is_identity_fresh: db access failed for %s/%s",
            platform, external_id, exc_info=True,
        )
        return False


def upsert_identity_display_name(
    *,
    platform: str,
    external_id: str,
    display_name: Optional[str],
    user_id_alt: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Best-effort: register the identity and refresh display_name + metadata.

    Behavior:
      - If no row exists for (platform, external_id): INSERT one attached to
        the system user, so admin sees it in IdentityBindings → "待归属".
        display_name is set to the fresh value if given, else left NULL
        (admin can edit later).
      - If row exists and current display_name is null/blank/legacy-placeholder:
        UPDATE display_name to the fresh value (when non-empty).
      - If row exists and display_name has been set by a human (anything else):
        leave it alone — respect admin's edit.
      - metadata (when non-empty): merged into existing metadata_json. Useful
        for platform-specific profile bits (email, mobile, employee_no, ...).
        Always refreshed regardless of who's bound the identity — these are
        platform facts, not editable choices.

    Runs on EVERY message (not gated on display_name being present) so that
    even when the platform can't resolve a real name (e.g. Feishu app lacks
    contact:user.base:readonly), the identity still surfaces in the panel.

    Never raises. Drops silently if the db is missing/locked/busy.
    """
    import json
    db_path = _resolve_db_path()
    if db_path is None:
        return
    norm_platform = _normalize_platform(platform)
    norm_external = (external_id or "").strip()
    if not norm_platform or not norm_external:
        return
    fresh_name = (display_name or "").strip() or None
    fresh_metadata = metadata if (metadata and isinstance(metadata, dict)) else None

    try:
        with sqlite3.connect(str(db_path), timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select id, display_name, metadata_json
                from user_identities
                where platform = ? and external_id = ?
                """,
                (norm_platform, norm_external),
            ).fetchone()

            if row:
                # Compute new fields
                current = (row["display_name"] or "").strip()
                update_name = fresh_name and (not current or current.startswith("legacy "))
                # Merge new metadata into existing
                merged_metadata_json = row["metadata_json"]
                if fresh_metadata:
                    try:
                        existing = json.loads(row["metadata_json"] or "{}")
                        if not isinstance(existing, dict):
                            existing = {}
                    except json.JSONDecodeError:
                        existing = {}
                    existing.update(fresh_metadata)
                    merged_metadata_json = json.dumps(existing, ensure_ascii=False)
                # Only write when there's actually something to change
                if update_name or fresh_metadata:
                    conn.execute(
                        """
                        update user_identities
                        set display_name = case when ? then ? else display_name end,
                            external_id_alt = coalesce(?, external_id_alt),
                            metadata_json = ?
                        where id = ?
                        """,
                        (
                            1 if update_name else 0,
                            fresh_name,
                            user_id_alt,
                            merged_metadata_json,
                            row["id"],
                        ),
                    )
                    conn.commit()
                return

            # No row → register under system user; admin transfers later.
            identity_id = f"uid_{os.urandom(8).hex()}"
            metadata_json = json.dumps(fresh_metadata, ensure_ascii=False) if fresh_metadata else None
            conn.execute(
                """
                insert or ignore into user_identities
                  (id, user_id, platform, external_id, external_id_alt,
                   display_name, bound_at, bound_by, metadata_json)
                values (?, 'usr_system', ?, ?, ?, ?, ?, 'auto_discovered', ?)
                """,
                (
                    identity_id,
                    norm_platform,
                    norm_external,
                    user_id_alt,
                    fresh_name,
                    _now(),
                    metadata_json,
                ),
            )
            conn.commit()
    except Exception:
        logger.debug(
            "upsert_identity_display_name: db access failed for %s/%s",
            platform,
            external_id,
            exc_info=True,
        )


def resolve_or_claim_group_owner(
    *,
    platform: str,
    chat_id: str,
    candidate_external_id: str,
    candidate_user_id_alt: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Return (owner_external_id, owner_user_id_alt) for this group chat.

    - If a group_owner already exists, return its values (first-@Bot wins).
    - Otherwise, INSERT OR IGNORE to claim the candidate as the new owner.
    - If the control-panel db is unreachable, return the candidate unchanged.

    Never raises — gateway-critical path. All failures degrade silently
    with a debug log.
    """
    db_path = _resolve_db_path()
    if db_path is None:
        return candidate_external_id, candidate_user_id_alt

    if not platform or not chat_id or not candidate_external_id:
        return candidate_external_id, candidate_user_id_alt

    norm_platform = _normalize_platform(platform)
    norm_chat_id = chat_id.strip()

    try:
        with sqlite3.connect(str(db_path), timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select owner_external_id, owner_user_id_alt
                from group_owners
                where platform = ? and chat_id = ?
                """,
                (norm_platform, norm_chat_id),
            ).fetchone()
            if row:
                return row["owner_external_id"], row["owner_user_id_alt"]

            # Claim — INSERT OR IGNORE handles concurrent first-@Bot races.
            identity_id = f"grp_{os.urandom(8).hex()}"
            conn.execute(
                """
                insert or ignore into group_owners
                  (id, platform, chat_id, owner_external_id, owner_user_id_alt,
                   established_at, established_session_id, notes)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_id,
                    norm_platform,
                    norm_chat_id,
                    candidate_external_id,
                    candidate_user_id_alt,
                    _now(),
                    session_id,
                    "auto-claimed on first @Bot",
                ),
            )
            conn.commit()
            # Re-read in case someone else inserted concurrently — INSERT OR
            # IGNORE returns silently in that case.
            row = conn.execute(
                """
                select owner_external_id, owner_user_id_alt
                from group_owners
                where platform = ? and chat_id = ?
                """,
                (norm_platform, norm_chat_id),
            ).fetchone()
            if row:
                return row["owner_external_id"], row["owner_user_id_alt"]
    except Exception:
        logger.debug(
            "group_owner_resolver: db access failed for platform=%s chat_id=%s",
            platform,
            chat_id,
            exc_info=True,
        )
    return candidate_external_id, candidate_user_id_alt
