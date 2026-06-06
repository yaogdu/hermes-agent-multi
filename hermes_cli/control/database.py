"""Lightweight database abstraction supporting SQLite, MySQL, and PostgreSQL.

Single import point for all control-panel database access. Usage::

    from hermes_cli.control.database import Database

    db = Database("sqlite:///~/.hermes/agentops_control.db")
    # or
    db = Database("mysql://user:pass@host:3306/agentops_control")
    # or
    db = Database("postgresql://user:pass@host:5432/agentops_control")

    row = db.fetchone("select * from users where id = ?", (user_id,))
    rows = db.fetchall("select * from users where role = ?", (role,))
    db.execute("update users set status = ? where id = ?", (status, user_id))

    with db.transaction():
        db.execute("insert into users (...) values (...)", params)
        db.execute("insert into user_identities (...) values (...)", params2)

All SQL uses ``?`` placeholders — the backend converts them to the correct
parameter style automatically.  ``INSERT OR IGNORE`` is also translated to
each backend's equivalent.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\?")

# Error codes for "already exists" that we silently accept during DDL.
_MYSQL_DUP_TABLE = 1050
_MYSQL_DUP_KEY = 1061
_PG_DUP_TABLE = "42P07"
_PG_DUP_OBJECT = "42710"


# ── Public API ──────────────────────────────────────────────────────────────────


class Database:
    """Unified database handle — auto-selects backend from a connection URL.

    URL formats::

        sqlite:///absolute/path/to/db
        sqlite:///relative/path/to/db
        mysql://user:password@host:port/database
        postgresql://user:password@host:port/database
    """

    def __init__(self, url: str) -> None:
        self._url = url
        if url.startswith("sqlite:///") or url.startswith("sqlite://"):
            self._backend: _BaseBackend = _SQLiteBackend(_sqlite_path(url))
        elif url.startswith("mysql://"):
            self._backend = _MySQLBackend(url)
        elif url.startswith("postgresql://") or url.startswith("postgres://"):
            self._backend = _PostgresBackend(url)
        else:
            raise ValueError(
                f"Unsupported database URL scheme: {url.split('://')[0] if '://' in url else url}"
            )

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def url(self) -> str:
        return self._url

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        """Execute a statement, returning a cursor with ``rowcount``."""
        return self._backend.execute(sql, params)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute a query and return the first row as a dict, or None."""
        return self._backend.fetchone(sql, params)

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute a query and return all rows as a list of dicts."""
        return self._backend.fetchall(sql, params)

    @contextmanager
    def transaction(self):
        """Context manager: commit on success, rollback on exception.

        For MySQL/PostgreSQL this keeps a single connection open across all
        operations inside the block so they share one transaction.
        """
        self._backend.begin()
        try:
            yield
            self._backend.commit()
        except Exception:
            self._backend.rollback()
            raise

    def close(self) -> None:
        self._backend.close()

    def fts_search(
        self,
        table: str,
        query: str,
        columns: list[str] | None = None,
        where: str | None = None,
        where_params: tuple = (),
        order: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """Full-text search across *table*, routing to the backend's native FTS.

        ``query`` is the user-provided search string. The backend handles
        escaping and syntax translation.

        ``columns`` is the list of columns to SELECT; defaults to ``*``.

        ``where`` is an additional SQL WHERE clause (with ``?`` placeholders).

        Returns a list of dict rows.
        """
        return self._backend.fts_search(
            table=table,
            query=query,
            columns=columns,
            where=where,
            where_params=where_params,
            order=order,
            limit=limit,
            offset=offset,
        )

    def fulltext_setup(self, table: str, columns: list[str]) -> None:
        """Create or ensure a full-text index exists for *table*.

        Idempotent — skips if the index already exists.
        """
        self._backend.fulltext_setup(table, columns)

    def conn(self) -> Any:
        """Return the raw underlying connection for advanced use cases.

        For SQLite this is a sqlite3.Connection. For MySQL/PG, it returns a
        connection from the pool that the caller must close.
        """
        return self._backend.raw_connection()


# ── Cursor ──────────────────────────────────────────────────────────────────────


class Cursor:
    """Thin wrapper so callers can read ``rowcount`` after execute()."""

    def __init__(self, rowcount: int = 0, lastrowid: int = 0) -> None:
        self.rowcount = rowcount
        self.lastrowid = lastrowid


# ── Base backend ────────────────────────────────────────────────────────────────


class _BaseBackend:
    name: str = ""

    def dialect_sql(self, sql: str) -> str:
        raise NotImplementedError

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        raise NotImplementedError

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        raise NotImplementedError

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        raise NotImplementedError

    def begin(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fts_search(self, table, query, columns=None, where=None, where_params=(),
                   order=None, limit=20, offset=0):
        raise NotImplementedError

    def fulltext_setup(self, table, columns):
        pass

    def raw_connection(self):
        raise NotImplementedError

    def _is_ddl(self, sql: str) -> bool:
        """Heuristic: is this a DDL statement?"""
        return sql.strip().upper().startswith(
            ("CREATE ", "ALTER ", "DROP ", "TRUNCATE ")
        )

    def _is_dup_error(self, exc: Exception) -> bool:
        """Check if the exception means 'object already exists'."""
        return False


# ── SQLite backend ──────────────────────────────────────────────────────────────


def _sqlite_path(url: str) -> str:
    path = url
    for prefix in ("sqlite:///", "sqlite://"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    return os.path.expanduser(path)


class _SQLiteBackend(_BaseBackend):
    name = "sqlite"

    def __init__(self, path: str) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def dialect_sql(self, sql: str) -> str:
        return sql

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        sql = self.dialect_sql(sql)
        with self._connect() as conn:
            with conn:
                cur = conn.execute(sql, params)
                return Cursor(cur.rowcount, cur.lastrowid)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        sql = self.dialect_sql(sql)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = self.dialect_sql(sql)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def fts_search(self, table, query, columns=None, where=None, where_params=(),
                   order=None, limit=20, offset=0):
        """SQLite FTS5 search — *table* is the FTS virtual table name."""
        cols = ", ".join(columns) if columns else "*"
        where_clause = f"WHERE {table} MATCH ?"
        params = [query]
        if where:
            where_clause += f" AND ({where})"
            params.extend(where_params)
        params.extend([limit, offset])
        order_clause = f"ORDER BY {order}" if order else "ORDER BY rank"
        sql = f"SELECT {cols} FROM {table} {where_clause} {order_clause} LIMIT ? OFFSET ?"
        return self.fetchall(sql, tuple(params))

    def raw_connection(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


# ── MySQL backend ───────────────────────────────────────────────────────────────


class _MySQLBackend(_BaseBackend):
    name = "mysql"

    def __init__(self, url: str) -> None:
        self._pool = None
        self._url = url
        self._conn_params = _parse_mysql_url(url)
        self._lock = threading.Lock()
        self._tx_conn = None  # set during transaction()

    def _ensure_pool(self):
        if self._pool is not None:
            return
        try:
            import pymysql  # noqa: F401
        except ImportError:
            raise ImportError(
                "pymysql is required for MySQL support. "
                "Install it with: pip install pymysql DBUtils"
            )
        try:
            from dbutils.pooled_db import PooledDB
        except ImportError:
            raise ImportError(
                "DBUtils is required for MySQL connection pooling. "
                "Install it with: pip install DBUtils"
            )
        self._pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            maxcached=5,
            blocking=True,
            **self._conn_params,
        )

    def _get_conn(self):
        """Return the active tx connection if inside a transaction, else a new one from the pool."""
        if self._tx_conn is not None:
            return self._tx_conn
        self._ensure_pool()
        return self._pool.connection()

    def _release_conn(self, conn):
        """Close conn unless it's the active tx connection."""
        if conn is not self._tx_conn:
            conn.close()

    def dialect_sql(self, sql: str) -> str:
        # INSERT OR IGNORE → INSERT IGNORE (MySQL supports this natively)
        sql = re.sub(
            r"insert\s+or\s+ignore\s+into",
            "INSERT IGNORE INTO",
            sql,
            flags=re.IGNORECASE,
        )
        # CREATE INDEX IF NOT EXISTS → CREATE INDEX (MySQL doesn't support IF NOT EXISTS);
        # the DDL dup-error handler will silently ignore 1061 "duplicate key" errors.
        sql = re.sub(
            r"create\s+index\s+if\s+not\s+exists\s+",
            "CREATE INDEX ",
            sql,
            flags=re.IGNORECASE,
        )
        # AUTOINCREMENT → AUTO_INCREMENT (MySQL syntax)
        sql = re.sub(
            r"\bAUTOINCREMENT\b",
            "AUTO_INCREMENT",
            sql,
            flags=re.IGNORECASE,
        )
        return _PLACEHOLDER_RE.sub("%s", sql)

    def _is_dup_error(self, exc: Exception) -> bool:
        try:
            code = getattr(exc, "args", [None])[0]
            return code in (_MYSQL_DUP_TABLE, _MYSQL_DUP_KEY)
        except (IndexError, TypeError):
            return False

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            lastrowid = cur.lastrowid
            if self._tx_conn is None:
                conn.commit()
            return Cursor(cur.rowcount, lastrowid)
        except Exception as exc:
            if self._tx_conn is None:
                conn.rollback()
            if self._is_ddl(sql) and self._is_dup_error(exc):
                return Cursor(0)
            raise
        finally:
            self._release_conn(conn)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            self._release_conn(conn)

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            self._release_conn(conn)

    def begin(self) -> None:
        self._ensure_pool()
        self._tx_conn = self._pool.connection()

    def commit(self) -> None:
        if self._tx_conn is not None:
            try:
                self._tx_conn.commit()
            finally:
                self._tx_conn.close()
                self._tx_conn = None

    def rollback(self) -> None:
        if self._tx_conn is not None:
            try:
                self._tx_conn.rollback()
            finally:
                self._tx_conn.close()
                self._tx_conn = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def fts_search(self, table, query, columns=None, where=None, where_params=(),
                   order=None, limit=20, offset=0):
        """MySQL FULLTEXT search. *table* is the actual data table."""
        cols = ", ".join(columns) if columns else "*"
        where_clause = "WHERE MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE)"
        params = [query]
        if where:
            where_clause += f" AND ({where})"
            params.extend(where_params)
        params.extend([limit, offset])
        order_clause = f"ORDER BY {order}" if order else (
            "ORDER BY MATCH(content) AGAINST(%s IN NATURAL LANGUAGE MODE) DESC"
        )
        if not order:
            params.insert(1, query)
        sql = f"SELECT {cols} FROM {table} {where_clause} {order_clause} LIMIT %s OFFSET %s"
        return self.fetchall(sql, tuple(params))

    def fulltext_setup(self, table, columns):
        """Add FULLTEXT index with ngram parser for CJK (MySQL 5.7+)."""
        col_list = ", ".join(columns)
        try:
            self.execute(
                f"ALTER TABLE {table} ADD FULLTEXT INDEX ft_{table}_content "
                f"({col_list}) WITH PARSER ngram"
            )
        except Exception as exc:
            err_code = getattr(exc, "args", [None])[0] if hasattr(exc, "args") else None
            if err_code in (1061, 1283):
                return
            try:
                self.execute(
                    f"ALTER TABLE {table} ADD FULLTEXT INDEX ft_{table}_content "
                    f"({col_list})"
                )
            except Exception as exc2:
                err_code2 = getattr(exc2, "args", [None])[0] if hasattr(exc2, "args") else None
                if err_code2 not in (1061, 1283):
                    raise

    def raw_connection(self):
        self._ensure_pool()
        return self._pool.connection()


# ── PostgreSQL backend ──────────────────────────────────────────────────────────


class _PostgresBackend(_BaseBackend):
    name = "postgresql"

    def __init__(self, url: str) -> None:
        self._pool = None
        self._url = url
        self._conn_params = _parse_postgres_url(url)
        self._lock = threading.Lock()
        self._tx_conn = None

    def _ensure_pool(self):
        if self._pool is not None:
            return
        try:
            import psycopg2  # noqa: F401
            import psycopg2.extras  # noqa: F401
        except ImportError:
            raise ImportError(
                "psycopg2 is required for PostgreSQL support. "
                "Install it with: pip install psycopg2-binary DBUtils"
            )
        try:
            from dbutils.pooled_db import PooledDB
        except ImportError:
            raise ImportError(
                "DBUtils is required for PostgreSQL connection pooling. "
                "Install it with: pip install DBUtils"
            )
        self._pool = PooledDB(
            creator=psycopg2,
            maxconnections=10,
            mincached=2,
            maxcached=5,
            blocking=True,
            **self._conn_params,
        )

    def _get_conn(self):
        if self._tx_conn is not None:
            return self._tx_conn
        self._ensure_pool()
        return self._pool.connection()

    def _release_conn(self, conn):
        if conn is not self._tx_conn:
            conn.close()

    def dialect_sql(self, sql: str) -> str:
        # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
        # Only transform if the SQL is actually an INSERT OR IGNORE.
        new_sql, count = re.subn(
            r"insert\s+or\s+ignore\s+",
            "INSERT ",
            sql,
            count=1,
            flags=re.IGNORECASE,
        )
        if count:
            if new_sql.rstrip().endswith(";"):
                new_sql = new_sql.rstrip()[:-1] + " ON CONFLICT DO NOTHING;"
            else:
                new_sql = new_sql.rstrip() + " ON CONFLICT DO NOTHING"
            sql = new_sql
        # AUTOINCREMENT → SERIAL for PG (in CREATE TABLE, use SERIAL instead)
        # This is a simplification; for full PG compat, use GENERATED BY DEFAULT AS IDENTITY
        sql = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
            "SERIAL PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\bBIGINT\s+AUTOINCREMENT\b",
            "BIGSERIAL",
            sql,
            flags=re.IGNORECASE,
        )
        return _PLACEHOLDER_RE.sub("%s", sql)

    def _is_dup_error(self, exc: Exception) -> bool:
        try:
            pgcode = getattr(exc, "pgcode", None)
            return pgcode in (_PG_DUP_TABLE, _PG_DUP_OBJECT)
        except Exception:
            return False

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            lastrowid = cur.lastrowid
            if self._tx_conn is None:
                conn.commit()
            return Cursor(cur.rowcount, lastrowid)
        except Exception as exc:
            if self._tx_conn is None:
                conn.rollback()
            if self._is_ddl(sql) and self._is_dup_error(exc):
                return Cursor(0)
            raise
        finally:
            self._release_conn(conn)

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            self._release_conn(conn)

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = self.dialect_sql(sql)
        conn = self._get_conn()
        try:
            import psycopg2.extras
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            self._release_conn(conn)

    def begin(self) -> None:
        self._ensure_pool()
        self._tx_conn = self._pool.connection()

    def commit(self) -> None:
        if self._tx_conn is not None:
            try:
                self._tx_conn.commit()
            finally:
                self._tx_conn.close()
                self._tx_conn = None

    def rollback(self) -> None:
        if self._tx_conn is not None:
            try:
                self._tx_conn.rollback()
            finally:
                self._tx_conn.close()
                self._tx_conn = None

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None

    def fts_search(self, table, query, columns=None, where=None, where_params=(),
                   order=None, limit=20, offset=0):
        """PostgreSQL full-text search with tsvector/tsquery."""
        cols = ", ".join(columns) if columns else "*"
        where_clause = (
            "WHERE to_tsvector('simple', content) @@ plainto_tsquery('simple', %s)"
        )
        params = [query]
        if where:
            where_clause += f" AND ({where})"
            params.extend(where_params)
        params.extend([limit, offset])
        order_clause = f"ORDER BY {order}" if order else (
            "ORDER BY ts_rank(to_tsvector('simple', content), "
            "plainto_tsquery('simple', %s)) DESC"
        )
        if not order:
            params.insert(1, query)
        sql = f"SELECT {cols} FROM {table} {where_clause} {order_clause} LIMIT %s OFFSET %s"
        return self.fetchall(sql, tuple(params))

    def fulltext_setup(self, table, columns):
        """Create GIN index for full-text search on *columns*."""
        col_list = " || ' ' || ".join(f"COALESCE({c}, '')" for c in columns)
        try:
            self.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_fts "
                f"ON {table} USING GIN (to_tsvector('simple', {col_list}))"
            )
        except Exception:
            pass  # index already exists or not supported

    def raw_connection(self):
        self._ensure_pool()
        return self._pool.connection()


# ── URL parsers ─────────────────────────────────────────────────────────────────


def _parse_mysql_url(url: str) -> dict[str, Any]:
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    params: dict[str, Any] = {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": (parsed.path or "/")[1:] if parsed.path else "",
        "charset": "utf8mb4",
    }
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "charset" in qs:
            params["charset"] = qs["charset"][0]
    return params


def _parse_postgres_url(url: str) -> dict[str, Any]:
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    params: dict[str, Any] = {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": (parsed.path or "/")[1:] if parsed.path else "",
    }
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "sslmode" in qs:
            params["sslmode"] = qs["sslmode"][0]
    return params
