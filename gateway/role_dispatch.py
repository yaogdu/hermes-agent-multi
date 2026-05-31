"""Gateway dispatch helpers for role-aware Team Edition sessions."""

from __future__ import annotations

import dataclasses
from typing import Any

try:
    from gateway.role_router import RoleRouter, RouteInput, RouteDecision
except Exception:  # pragma: no cover - supports direct file loading in minimal envs
    from role_router import RoleRouter, RouteInput, RouteDecision


def _config_to_dict(config: Any) -> dict:
    if isinstance(config, dict):
        return config
    if config is None:
        return {}
    result: dict[str, Any] = {}
    for attr in ("agent_roles", "role_routing"):
        value = getattr(config, attr, None)
        if value is not None:
            result[attr] = value
    return result


def route_event_to_role(event: Any, config: Any) -> tuple[Any, Any, RouteDecision]:
    """Return a role-aware ``(event, source, decision)`` tuple.

    The function is intentionally pure-ish: it only creates dataclass copies
    when a role route is resolved. Existing single-agent installs keep the
    default ``main`` role and therefore preserve their existing session key.
    """
    source = getattr(event, "source", None)
    if source is None:
        router = RoleRouter.from_config(_config_to_dict(config))
        decision = router.route(RouteInput(platform="", chat_id="", message=getattr(event, "text", "") or ""))
        return event, source, decision

    platform = getattr(getattr(source, "platform", None), "value", None)
    if platform is None:
        platform = str(getattr(source, "platform", "") or "")

    router = RoleRouter.from_config(_config_to_dict(config))
    decision = router.route(RouteInput(
        platform=platform,
        chat_id=str(getattr(source, "chat_id", "") or ""),
        chat_type=str(getattr(source, "chat_type", "dm") or "dm"),
        user_id=str(getattr(source, "user_id", "") or "") or None,
        thread_id=str(getattr(source, "thread_id", "") or "") or None,
        message=getattr(event, "text", "") or "",
    ))

    new_source = dataclasses.replace(source, agent_key=decision.agent_key)
    new_event = event
    # If the router would rewrite event.text to an empty/whitespace-only
    # string (typical for `/ask <role>` with no message), keep the original
    # text. The downstream slash-command dispatcher will see the unchanged
    # `/ask` invocation and route to _handle_ask_command, which can then
    # tell the user how to actually use it. Without this we'd send empty
    # text to the LLM, which silently improvises a reply.
    normalized = (decision.normalized_message or "")
    original_text = (getattr(event, "text", "") or "")
    if normalized.strip() and normalized != original_text:
        new_event = dataclasses.replace(event, text=normalized, source=new_source)
    else:
        new_event = dataclasses.replace(event, source=new_source)
    return new_event, new_source, decision
