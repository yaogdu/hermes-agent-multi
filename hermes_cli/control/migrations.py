"""Database migrations driven by PRAGMA user_version.

Each migration is a tuple (version, name, body). The body is either:
  - a SQL string (executed via executescript), or
  - a callable that receives the open sqlite3.Connection.

Apply order is strictly ascending version; only migrations with version >
current user_version are applied. Each migration runs inside a transaction
that also bumps user_version on success.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Union

logger = logging.getLogger(__name__)

MigrationBody = Union[str, Callable[[sqlite3.Connection], None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Placeholder password hash that is not a valid bcrypt string — guarantees
# the system user can never authenticate, no matter what is sent to verify().
SYSTEM_DISABLED_HASH = "!disabled-no-login!"
SYSTEM_USER_ID = "usr_system"
SYSTEM_USERNAME = "system"


def _migration_v1_users_and_identities(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists users (
          id text primary key,
          username text not null unique,
          password_hash text not null,
          display_name text,
          role text not null default 'user',
          status text not null default 'active',
          created_at text not null,
          updated_at text not null,
          last_login_at text,
          password_changed_at text not null
        );

        create index if not exists idx_users_role on users(role);
        create index if not exists idx_users_status on users(status);

        create table if not exists user_identities (
          id text primary key,
          user_id text not null references users(id) on delete cascade,
          platform text not null,
          external_id text not null,
          external_id_alt text,
          display_name text,
          bound_at text not null,
          bound_by text,
          unique(platform, external_id)
        );

        create index if not exists idx_user_identities_user on user_identities(user_id);
        create index if not exists idx_user_identities_external on user_identities(platform, external_id);
        """
    )


def _migration_v2_seed_system_user(conn: sqlite3.Connection) -> None:
    now = _now()
    conn.execute(
        """
        insert or ignore into users
          (id, username, password_hash, display_name, role, status,
           created_at, updated_at, password_changed_at)
        values (?, ?, ?, ?, 'system', 'disabled', ?, ?, ?)
        """,
        (
            SYSTEM_USER_ID,
            SYSTEM_USERNAME,
            SYSTEM_DISABLED_HASH,
            "System (legacy data)",
            now,
            now,
            now,
        ),
    )


def _migration_v3_group_owners(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists group_owners (
          id text primary key,
          platform text not null,
          chat_id text not null,
          owner_external_id text not null,
          owner_user_id_alt text,
          established_at text not null,
          established_session_id text,
          notes text,
          unique(platform, chat_id)
        );

        create index if not exists idx_group_owners_platform_chat on group_owners(platform, chat_id);
        """
    )


def _migration_v4_backfill_legacy_identities(conn: sqlite3.Connection) -> None:
    """Copy distinct (source, user_id) pairs from Hermes state.db into
    user_identities, all attached to the system user.

    Admins can later transfer these to real users via the UI. Skips silently
    if state.db is missing — this lets fresh installs apply the migration
    without bombing out.
    """
    state_db = os.getenv("HERMES_STATE_DB_PATH", "")
    if not state_db or not Path(state_db).exists():
        logger.info("backfill v4 skipped: state.db not found at %s", state_db)
        return

    try:
        with sqlite3.connect(state_db) as src:
            src.row_factory = sqlite3.Row
            rows = src.execute(
                """
                select source, user_id, count(*) as cnt
                from sessions
                where user_id is not null and user_id != ''
                group by source, user_id
                """
            ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.warning("backfill v4: state.db has no sessions table (%s)", exc)
        return

    now = _now()
    inserted = 0
    for row in rows:
        platform = str(row["source"] or "").strip().lower() or "unknown"
        external_id = str(row["user_id"]).strip()
        if not external_id:
            continue
        identity_id = f"uid_{os.urandom(8).hex()}"
        try:
            conn.execute(
                """
                insert into user_identities
                  (id, user_id, platform, external_id, external_id_alt,
                   display_name, bound_at, bound_by)
                values (?, ?, ?, ?, ?, ?, ?, 'backfill_v4')
                """,
                (
                    identity_id,
                    SYSTEM_USER_ID,
                    platform,
                    external_id,
                    None,
                    f"legacy {platform} ({row['cnt']} sessions)",
                    now,
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    logger.info("backfill v4: inserted %d legacy identities under system user", inserted)


def _migration_v5_identity_metadata(conn: sqlite3.Connection) -> None:
    """Add metadata_json column on user_identities for platform-specific
    profile fields (email, mobile, employee_no, dept ids, ...).
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(user_identities)")}
    if "metadata_json" not in cols:
        conn.execute("alter table user_identities add column metadata_json text")


def _migration_v6_control_sessions(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists control_sessions (
          id text primary key,
          actor text not null,
          token_hash text not null,
          source_ip text,
          user_agent text,
          created_at text not null,
          expires_at text not null,
          revoked_at text
        );

        create index if not exists idx_control_sessions_token
          on control_sessions(token_hash);
        create index if not exists idx_control_sessions_actor
          on control_sessions(actor);
        """
    )


MIGRATIONS: list[tuple[int, str, MigrationBody]] = [
    (1, "users_and_identities", _migration_v1_users_and_identities),
    (2, "seed_system_user", _migration_v2_seed_system_user),
    (3, "group_owners", _migration_v3_group_owners),
    (4, "backfill_legacy_identities", _migration_v4_backfill_legacy_identities),
    (5, "identity_metadata", _migration_v5_identity_metadata),
    (6, "control_sessions", _migration_v6_control_sessions),
]


def apply_migrations(db_path: Path) -> int:
    """Apply pending migrations. Returns the resulting user_version."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        applied: list[int] = []
        for version, name, body in sorted(MIGRATIONS, key=lambda m: m[0]):
            if version <= current_version:
                continue
            try:
                with conn:
                    if isinstance(body, str):
                        conn.executescript(body)
                    else:
                        body(conn)
                    conn.execute(f"PRAGMA user_version = {version}")
                applied.append(version)
                logger.info("migration v%d applied: %s", version, name)
            except Exception:
                logger.exception("migration v%d failed: %s", version, name)
                raise
        final_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if applied:
            logger.info("migrations applied: %s (now at v%d)", applied, final_version)
        else:
            logger.info("migrations up to date at v%d", final_version)
        return int(final_version)


def bootstrap_admin(
    db_path: Path,
    *,
    username: str | None = None,
    password: str | None = None,
    password_hasher: Callable[[str], str] | None = None,
) -> dict | None:
    """Seed an initial admin user if no active admin exists.

    Returns the created user record (without password_hash), or None if
    bootstrapping was skipped (admin already exists, or env vars missing).
    Reads from AGENTOPS_BOOTSTRAP_ADMIN_USERNAME / _PASSWORD if args are
    omitted.
    """
    if password_hasher is None:
        from .auth import PasswordHasher
        password_hasher = PasswordHasher().hash

    username = (username or os.getenv("AGENTOPS_BOOTSTRAP_ADMIN_USERNAME", "")).strip()
    password = password or os.getenv("AGENTOPS_BOOTSTRAP_ADMIN_PASSWORD", "")
    if not username or not password:
        logger.info("bootstrap_admin skipped: env vars not set")
        return None

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        existing_admin = conn.execute(
            "select id from users where role = 'admin' and status = 'active' limit 1"
        ).fetchone()
        if existing_admin:
            logger.info("bootstrap_admin skipped: active admin already exists")
            return None

        username_clash = conn.execute(
            "select id, role, status from users where username = ?", (username,)
        ).fetchone()
        if username_clash:
            logger.warning(
                "bootstrap_admin: username %r already exists (role=%s, status=%s); "
                "not overwriting",
                username,
                username_clash["role"],
                username_clash["status"],
            )
            return None

        user_id = f"usr_{os.urandom(8).hex()}"
        now = _now()
        password_hash = password_hasher(password)
        with conn:
            conn.execute(
                """
                insert into users
                  (id, username, password_hash, display_name, role, status,
                   created_at, updated_at, password_changed_at)
                values (?, ?, ?, ?, 'admin', 'active', ?, ?, ?)
                """,
                (user_id, username, password_hash, username, now, now, now),
            )
        logger.warning(
            "bootstrap_admin: created admin user %r — please log in and change "
            "the password immediately",
            username,
        )
        return {
            "id": user_id,
            "username": username,
            "role": "admin",
            "status": "active",
            "created_at": now,
        }
