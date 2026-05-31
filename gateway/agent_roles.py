"""Agent role configuration primitives for team/multi-role gateway usage.

This module is intentionally standalone: it parses role configuration without
changing the existing single-agent gateway flow. Later gateway patches can
consume AgentRoleRegistry to route messages and build role-aware sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_ROLE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def normalize_agent_key(value: Any, default: str = "main") -> str:
    text = str(value or "").strip().lower()
    if not text:
        text = default
    text = _ROLE_KEY_RE.sub("-", text).strip("-._")
    return text or default


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


@dataclass(frozen=True)
class AgentRole:
    key: str
    display_name: str
    system_prompt: Optional[str] = None
    system_prompt_file: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    enabled_toolsets: list[str] = field(default_factory=list)
    disabled_toolsets: list[str] = field(default_factory=list)
    memory_scope: str = "user_session"
    approval_policy: str = "standard"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, key: str, data: Any) -> "AgentRole":
        cfg = data if isinstance(data, dict) else {}
        normalized_key = normalize_agent_key(key)
        display_name = str(cfg.get("display_name") or cfg.get("name") or normalized_key)
        return cls(
            key=normalized_key,
            display_name=display_name,
            system_prompt=cfg.get("system_prompt"),
            system_prompt_file=cfg.get("system_prompt_file"),
            model=cfg.get("model"),
            provider=cfg.get("provider"),
            enabled_toolsets=_as_list(cfg.get("enabled_toolsets")),
            disabled_toolsets=_as_list(cfg.get("disabled_toolsets")),
            memory_scope=str(cfg.get("memory_scope") or "user_session"),
            approval_policy=str(cfg.get("approval_policy") or "standard"),
            metadata={k: v for k, v in cfg.items() if k not in {
                "display_name",
                "name",
                "system_prompt",
                "system_prompt_file",
                "model",
                "provider",
                "enabled_toolsets",
                "disabled_toolsets",
                "memory_scope",
                "approval_policy",
            }},
        )


@dataclass(frozen=True)
class AgentRoleRegistry:
    default_key: str
    roles: Dict[str, AgentRole]

    @classmethod
    def from_config(cls, config: dict | None) -> "AgentRoleRegistry":
        cfg = config if isinstance(config, dict) else {}
        raw = cfg.get("agent_roles") or {}
        raw = raw if isinstance(raw, dict) else {}
        raw_roles = raw.get("roles") or {}
        raw_roles = raw_roles if isinstance(raw_roles, dict) else {}

        roles: Dict[str, AgentRole] = {
            role.key: role
            for role in (
                AgentRole.from_config(key, value)
                for key, value in raw_roles.items()
            )
        }
        default_key = normalize_agent_key(raw.get("default") or "main")
        if default_key not in roles:
            roles[default_key] = AgentRole(key=default_key, display_name=default_key)
        if not roles:
            roles["main"] = AgentRole(key="main", display_name="main")
            default_key = "main"
        return cls(default_key=default_key, roles=roles)

    def get(self, key: str | None) -> AgentRole:
        normalized = normalize_agent_key(key or self.default_key)
        return self.roles.get(normalized) or self.roles[self.default_key]

    def has(self, key: str | None) -> bool:
        return normalize_agent_key(key or "") in self.roles

    def keys(self) -> list[str]:
        return sorted(self.roles)


@dataclass(frozen=True)
class RoleOverlay:
    role: AgentRole
    enabled_toolsets: list[str]
    disabled_toolsets: list[str]
    ephemeral_system_prompt: str
    cache_key: dict[str, Any]


def _merge_unique(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _read_role_prompt_file(path_value: str | None, *, base_dir: Path | None = None) -> str:
    path_text = str(path_value or "").strip()
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def build_role_overlay(
    *,
    config: dict | None,
    agent_key: str | None,
    platform_toolsets: Iterable[str],
    global_disabled_toolsets: Iterable[str] | None = None,
    base_ephemeral_prompt: str = "",
    config_dir: Path | None = None,
) -> RoleOverlay:
    """Apply a role's prompt/tool overlay to gateway runtime config.

    Defaults are intentionally conservative: a role with no toolset overrides
    inherits the platform toolsets. A role with ``enabled_toolsets`` replaces
    the platform toolsets, making role capability boundaries explicit.
    """
    registry = AgentRoleRegistry.from_config(config)
    role = registry.get(agent_key)

    enabled = _merge_unique(role.enabled_toolsets or platform_toolsets)
    disabled = _merge_unique([*(global_disabled_toolsets or []), *role.disabled_toolsets])
    if disabled:
        disabled_set = set(disabled)
        enabled = [toolset for toolset in enabled if toolset not in disabled_set]

    role_prompt_parts = []
    file_prompt = _read_role_prompt_file(role.system_prompt_file, base_dir=config_dir)
    if file_prompt:
        role_prompt_parts.append(file_prompt)
    if role.system_prompt:
        role_prompt_parts.append(str(role.system_prompt).strip())

    prompt_parts = [str(base_ephemeral_prompt or "").strip()]
    prompt_parts.extend(part for part in role_prompt_parts if part)
    ephemeral = "\n\n".join(part for part in prompt_parts if part)

    return RoleOverlay(
        role=role,
        enabled_toolsets=enabled,
        disabled_toolsets=disabled,
        ephemeral_system_prompt=ephemeral,
        cache_key={
            "agent_key": role.key,
            "model": role.model,
            "provider": role.provider,
            "enabled_toolsets": enabled,
            "disabled_toolsets": disabled,
            "system_prompt": role.system_prompt or "",
            "system_prompt_file": role.system_prompt_file or "",
            "system_prompt_file_loaded": bool(file_prompt),
            "memory_scope": role.memory_scope,
            "approval_policy": role.approval_policy,
        },
    )
