"""Role-aware tool policy decisions for Hermes Team Edition.

This module is deliberately lightweight and side-effect free except for the
optional approval wait. It can be called from both the agent tool executor and
the lower-level model_tools dispatcher.
"""

from __future__ import annotations

import contextvars
import fnmatch
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


VALID_TOOL_CATEGORIES = frozenset({"read_only", "write", "external_action", "unknown"})


_tool_policy_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "tool_policy_name",
    default="",
)
_tool_policy_configs: contextvars.ContextVar[Mapping[str, Mapping[str, list[str]]] | None] = (
    contextvars.ContextVar("tool_policy_configs", default=None)
)


READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "browser_snapshot",
        "browser_get_images",
        "browser_vision",
        "skills_list",
        "skill_view",
    }
)

WRITE_TOOLS = frozenset(
    {
        "write_file",
        "patch",
        "todo",
        "memory",
        "skill_manage",
    }
)

EXTERNAL_ACTION_TOOLS = frozenset(
    {
        "terminal",
        "execute_code",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "browser_back",
        "browser_console",
        "browser_cdp",
        "browser_dialog",
        "send_message",
        "cronjob",
        "delegate_task",
        "process",
        "computer_use",
    }
)


@dataclass(frozen=True)
class ToolPolicyDecision:
    action: str = "allow"  # allow | require_approval | deny
    policy_name: str = "standard"
    reason: str = "default allow"
    tool_name: str = ""
    category: str = "unknown"

    @property
    def allows_execution(self) -> bool:
        return self.action == "allow"

    def to_result(self) -> str:
        return json.dumps(
            {
                "error": (
                    f"Tool policy {self.action}: {self.reason}. "
                    "Do NOT retry this tool unchanged."
                ),
                "tool_policy": {
                    "action": self.action,
                    "policy_name": self.policy_name,
                    "reason": self.reason,
                    "tool_name": self.tool_name,
                    "category": self.category,
                },
            },
            ensure_ascii=False,
        )


def normalize_policy_name(value: Any) -> str:
    return str(value or "standard").strip().lower().replace("-", "_") or "standard"


def set_current_tool_policy(policy: str) -> contextvars.Token[str]:
    return _tool_policy_name.set(normalize_policy_name(policy))


def reset_current_tool_policy(token: contextvars.Token[str]) -> None:
    _tool_policy_name.reset(token)


def _normalize_single_policy_config(value: Any) -> Mapping[str, list[str]]:
    raw = value if isinstance(value, Mapping) else {}
    require_approval = [
        *_as_list(raw.get("require_approval")),
        *_as_list(raw.get("approval_required")),
        *_as_list(raw.get("require_manual")),
    ]
    return {
        "allow": _as_list(raw.get("allow")),
        "require_approval": require_approval,
        "deny": _as_list(raw.get("deny")),
    }


def normalize_policy_configs(value: Any) -> Mapping[str, Mapping[str, list[str]]]:
    raw = value if isinstance(value, Mapping) else {}
    result: dict[str, Mapping[str, list[str]]] = {}
    for key, config in raw.items():
        policy_name = normalize_policy_name(key)
        normalized = _normalize_single_policy_config(config)
        if any(normalized.values()):
            result[policy_name] = normalized
    return result


def set_current_tool_policy_configs(
    configs: Mapping[str, Any] | None,
) -> contextvars.Token[Mapping[str, Mapping[str, list[str]]] | None]:
    return _tool_policy_configs.set(normalize_policy_configs(configs))


def reset_current_tool_policy_configs(
    token: contextvars.Token[Mapping[str, Mapping[str, list[str]]] | None],
) -> None:
    _tool_policy_configs.reset(token)


def get_current_tool_policy_configs() -> Mapping[str, Mapping[str, list[str]]]:
    return _tool_policy_configs.get() or {}


def get_current_tool_policy(default: str = "standard") -> str:
    value = _tool_policy_name.get()
    if value:
        return normalize_policy_name(value)
    try:
        from gateway.session_context import get_session_env

        value = get_session_env("HERMES_APPROVAL_POLICY", "")
        if value:
            return normalize_policy_name(value)
    except Exception:
        pass
    return normalize_policy_name(os.getenv("HERMES_APPROVAL_POLICY", default))


def normalize_tool_category(value: Any) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    aliases = {
        "read": "read_only",
        "readonly": "read_only",
        "read_only": "read_only",
        "safe_read": "read_only",
        "write": "write",
        "mutation": "write",
        "mutating": "write",
        "state_change": "write",
        "state_changing": "write",
        "external": "external_action",
        "external_action": "external_action",
        "side_effect": "external_action",
        "side_effecting": "external_action",
        "destructive": "external_action",
        "open_world": "external_action",
        "unknown": "unknown",
    }
    normalized = aliases.get(text, text)
    return normalized if normalized in VALID_TOOL_CATEGORIES else None


def _metadata_category(metadata: Mapping[str, Any] | None) -> str | None:
    if not isinstance(metadata, Mapping):
        return None

    for key in (
        "risk_category",
        "policy_category",
        "tool_policy_category",
        "category",
        "x_hermes_risk_category",
        "x-hermes-risk-category",
    ):
        category = normalize_tool_category(metadata.get(key))
        if category is not None:
            return category

    for nested_key in ("policy", "tool_policy", "governance"):
        nested = metadata.get(nested_key)
        if isinstance(nested, Mapping):
            category = _metadata_category(nested)
            if category is not None:
                return category

    annotations = metadata.get("annotations")
    if isinstance(annotations, Mapping):
        read_only = bool(
            annotations.get("readOnlyHint")
            or annotations.get("read_only_hint")
            or annotations.get("read_only")
        )
        destructive = bool(
            annotations.get("destructiveHint")
            or annotations.get("destructive_hint")
            or annotations.get("destructive")
        )
        open_world = bool(
            annotations.get("openWorldHint")
            or annotations.get("open_world_hint")
            or annotations.get("open_world")
        )
        if read_only:
            return "read_only"
        if destructive or open_world:
            return "external_action"

    return None


def _registry_metadata(tool_name: str) -> Mapping[str, Any]:
    try:
        from tools.registry import registry

        return registry.get_metadata(tool_name)
    except Exception:
        return {}


def classify_tool(tool_name: str, tool_metadata: Mapping[str, Any] | None = None) -> str:
    name = str(tool_name or "")
    metadata_category = _metadata_category(tool_metadata)
    if metadata_category is None:
        metadata_category = _metadata_category(_registry_metadata(name))
    if metadata_category is not None:
        return metadata_category
    if name in READ_ONLY_TOOLS:
        return "read_only"
    if name in WRITE_TOOLS:
        return "write"
    if name in EXTERNAL_ACTION_TOOLS:
        return "external_action"
    if name.startswith("mcp_") or name.startswith("mcp-"):
        return "external_action"
    if name.endswith("_search") or name.endswith("_read") or name.endswith("_list"):
        return "read_only"
    return "unknown"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _matches(patterns: Iterable[str], tool_name: str, category: str) -> bool:
    aliases = {
        tool_name,
        tool_name.replace("_", "."),
        tool_name.replace("_", "-"),
        f"category:{category}",
    }
    for pattern in patterns:
        p = str(pattern or "").strip()
        if not p:
            continue
        if p in aliases:
            return True
        for alias in aliases:
            if fnmatch.fnmatch(alias, p):
                return True
    return False


def _named_policy(policy_name: str) -> Mapping[str, list[str]]:
    if policy_name in {"deny", "deny_all"}:
        return {"deny": ["*"]}
    if policy_name == "deny_dangerous":
        return {
            "allow": ["category:read_only", "category:write"],
            "deny": ["category:external_action"],
        }
    if policy_name in {"manual", "manual_required", "require_manual", "approval_required"}:
        return {
            "allow": ["category:read_only"],
            "require_approval": ["category:write", "category:external_action", "category:unknown"],
        }
    if policy_name in {"ops_safe", "code_safe", "code_review"}:
        return {
            "allow": ["category:read_only", "category:write"],
            "require_approval": ["category:external_action"],
        }
    if policy_name in {"off", "yolo", "allow_all", "auto_approve"}:
        return {"allow": ["*"]}
    return {"allow": ["*"]}


def decide_tool_policy(
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    *,
    policy_name: str | None = None,
    policy_config: Mapping[str, Any] | None = None,
    tool_metadata: Mapping[str, Any] | None = None,
) -> ToolPolicyDecision:
    name = str(tool_name or "")
    category = classify_tool(name, tool_metadata=tool_metadata)
    policy = normalize_policy_name(policy_name or get_current_tool_policy())
    policy_configs = get_current_tool_policy_configs()
    if isinstance(policy_config, Mapping):
        raw = _normalize_single_policy_config(policy_config)
    elif policy in policy_configs:
        raw = policy_configs[policy]
    else:
        raw = _named_policy(policy)

    deny = _as_list(raw.get("deny"))
    require_approval = _as_list(raw.get("require_approval"))
    allow = _as_list(raw.get("allow"))

    if _matches(deny, name, category):
        return ToolPolicyDecision("deny", policy, f"{name} matched deny policy", name, category)
    if _matches(require_approval, name, category):
        return ToolPolicyDecision(
            "require_approval",
            policy,
            f"{name} requires approval under policy '{policy}'",
            name,
            category,
        )
    if _matches(allow, name, category) or not allow:
        return ToolPolicyDecision("allow", policy, "allowed by policy", name, category)
    return ToolPolicyDecision("deny", policy, f"{name} is not in allow policy", name, category)


def _approval_description(decision: ToolPolicyDecision) -> str:
    return (
        f"tool policy '{decision.policy_name}' requires approval for "
        f"{decision.tool_name} ({decision.category})"
    )


def enforce_tool_policy(
    tool_name: str,
    args: Mapping[str, Any] | None = None,
    *,
    policy_name: str | None = None,
    policy_config: Mapping[str, Any] | None = None,
    tool_metadata: Mapping[str, Any] | None = None,
) -> str | None:
    """Return None when execution is allowed, otherwise a JSON error result."""
    decision = decide_tool_policy(
        tool_name,
        args,
        policy_name=policy_name,
        policy_config=policy_config,
        tool_metadata=tool_metadata,
    )
    if decision.action == "allow":
        return None
    if decision.action == "deny":
        return decision.to_result()

    # Reuse the existing gateway/CLI approval queue. We do not add permanent
    # allowlisting here; role policy approval is per tool call.
    try:
        from tools.approval import (
            _ApprovalEntry,
            _fire_approval_hook,
            _gateway_notify_cbs,
            _gateway_queues,
            _get_approval_config,
            _lock,
            get_current_session_key,
        )

        session_key = get_current_session_key()
        is_gateway_or_ask = bool(
            os.getenv("HERMES_EXEC_ASK")
            or os.getenv("HERMES_GATEWAY_SESSION")
        )
        try:
            from tools.approval import _is_gateway_approval_context

            is_gateway_or_ask = is_gateway_or_ask or _is_gateway_approval_context()
        except Exception:
            pass

        if not is_gateway_or_ask:
            return decision.to_result()

        with _lock:
            notify_cb = _gateway_notify_cbs.get(session_key)
        if notify_cb is None:
            return decision.to_result()

        approval_data = {
            "command": f"tool:{tool_name} {json.dumps(args or {}, ensure_ascii=False)[:500]}",
            "pattern_key": f"tool_policy:{decision.policy_name}:{tool_name}",
            "pattern_keys": [f"tool_policy:{decision.policy_name}:{tool_name}"],
            "description": _approval_description(decision),
        }
        entry = _ApprovalEntry(approval_data)
        with _lock:
            _gateway_queues.setdefault(session_key, []).append(entry)

        _fire_approval_hook(
            "pre_approval_request",
            command=approval_data["command"],
            description=approval_data["description"],
            pattern_key=approval_data["pattern_key"],
            pattern_keys=list(approval_data["pattern_keys"]),
            session_key=session_key,
            surface="gateway",
        )
        try:
            notify_cb(approval_data)
        except Exception:
            with _lock:
                queue = _gateway_queues.get(session_key, [])
                if entry in queue:
                    queue.remove(entry)
                if not queue:
                    _gateway_queues.pop(session_key, None)
            return decision.to_result()

        timeout = _get_approval_config().get("gateway_timeout", 300)
        try:
            timeout = int(timeout)
        except (ValueError, TypeError):
            timeout = 300
        deadline = time.monotonic() + max(timeout, 0)
        resolved = False
        while time.monotonic() < deadline:
            if entry.event.wait(timeout=min(1.0, max(0.0, deadline - time.monotonic()))):
                resolved = True
                break

        with _lock:
            queue = _gateway_queues.get(session_key, [])
            if entry in queue:
                queue.remove(entry)
            if not queue:
                _gateway_queues.pop(session_key, None)

        choice = entry.result
        _fire_approval_hook(
            "post_approval_response",
            command=approval_data["command"],
            description=approval_data["description"],
            pattern_key=approval_data["pattern_key"],
            pattern_keys=list(approval_data["pattern_keys"]),
            session_key=session_key,
            surface="gateway",
            choice=("timeout" if not resolved else (choice or "timeout")),
        )
        if resolved and choice in {"once", "session", "always"}:
            return None
        return decision.to_result()
    except Exception:
        return decision.to_result()
