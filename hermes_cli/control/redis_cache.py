"""Optional Redis cache for control-panel session tokens.

When a Redis URL is configured (``AGENTOPS_REDIS_URL``), session-token
lookups hit Redis first and fall back to the database on cache miss.
Writes (create / revoke) sync through to both Redis and the database.

If Redis is unreachable or not configured, all operations are no-ops.

Usage::

    from hermes_cli.control.redis_cache import setup_redis, get_redis

    setup_redis("redis://host:6379/0")
    r = get_redis()
    if r:
        r.setex("session:abc", 3600, json.dumps(data))
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_redis_client: "Redis | None" = None  # type: ignore[name-defined]
_redis_url: str | None = None

# Prefix for Redis keys to avoid collisions.
KEY_PREFIX = "agentops:session:"


class Redis:
    """Thin wrapper around redis-py with graceful degradation."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None
        self._available = False
        try:
            import redis as _redis
            self._client = _redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
            self._client.ping()
            self._available = True
            logger.info("Redis connected: %s", _redact_url(url))
        except ImportError:
            logger.debug("redis-py not installed; session cache disabled")
        except Exception as exc:
            logger.warning("Redis unavailable (%s); session cache disabled", exc)

    @property
    def available(self) -> bool:
        return self._available

    def get_session(self, token_hash: str) -> dict | None:
        """Return cached session data or None."""
        if not self._available or not self._client:
            return None
        try:
            raw = self._client.get(KEY_PREFIX + token_hash)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set_session(self, token_hash: str, data: dict, ttl: int) -> None:
        """Cache session data with TTL in seconds."""
        if not self._available or not self._client:
            return
        try:
            self._client.setex(
                KEY_PREFIX + token_hash,
                ttl,
                json.dumps(data, default=str),
            )
        except Exception:
            pass

    def delete_session(self, token_hash: str) -> None:
        """Remove a session from the cache."""
        if not self._available or not self._client:
            return
        try:
            self._client.delete(KEY_PREFIX + token_hash)
        except Exception:
            pass

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._available = False


def setup_redis(url: str | None) -> Redis | None:
    """Configure Redis from a connection URL.

    Called once at startup. Returns the Redis wrapper (or None if skipped).
    """
    global _redis_client, _redis_url
    if _redis_client is not None:
        _redis_client.close()
        _redis_client = None
    _redis_url = url
    if url and url.strip():
        _redis_client = Redis(url.strip())
    return _redis_client


def get_redis() -> Redis | None:
    """Return the configured Redis wrapper, or None."""
    return _redis_client


def _redact_url(url: str) -> str:
    import re
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)
