"""Lightweight runtime governance helpers for role-scoped agent execution."""

from __future__ import annotations

import re
from typing import Any


_AGENT_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def normalize_agent_key(value: Any, default: str = "main") -> str:
    text = str(value or "").strip().lower()
    if not text:
        text = default
    text = _AGENT_KEY_RE.sub("-", text).strip("-._")
    return text or default


def normalize_memory_scope(value: Any) -> str:
    text = str(value or "user_session").strip().lower().replace("-", "_")
    aliases = {
        "session": "user_session",
        "user": "user_session",
        "role": "role_user_session",
        "role_session": "role_user_session",
        "none": "disabled",
        "off": "disabled",
        "false": "disabled",
    }
    return aliases.get(text, text or "user_session")


def normalize_policy_name(value: Any) -> str:
    return str(value or "standard").strip().lower().replace("-", "_") or "standard"


def role_scoped_identity(base: str, *, agent_key: str, memory_scope: str) -> str:
    base = str(base or "").strip()
    if memory_scope in {"role_user_session", "role_global"}:
        return f"{base}:{agent_key}" if base else agent_key
    return base


def role_scoped_gateway_session_key(base: str, *, agent_key: str, memory_scope: str) -> str:
    if memory_scope == "role_global":
        return f"agent:{agent_key}:global"
    return str(base or "")
