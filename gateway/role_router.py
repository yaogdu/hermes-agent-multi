"""Role routing primitives for Hermes team/multi-role gateway usage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    from gateway.agent_roles import AgentRoleRegistry, normalize_agent_key
except Exception:  # pragma: no cover - supports direct file loading in minimal envs
    from agent_roles import AgentRoleRegistry, normalize_agent_key


_ASK_RE = re.compile(r"^\s*/ask\s+([a-zA-Z0-9_.-]+)(?:\s+|$)(.*)$", re.DOTALL)
_AT_RE = re.compile(r"^\s*@([a-zA-Z0-9_.-]+)(?:\s+|$)(.*)$", re.DOTALL)


@dataclass(frozen=True)
class RouteInput:
    platform: str
    chat_id: str
    chat_type: str = "dm"
    user_id: Optional[str] = None
    thread_id: Optional[str] = None
    message: str = ""


@dataclass(frozen=True)
class RouteDecision:
    agent_key: str
    route_reason: str
    confidence: float
    normalized_message: str


def _channel_binding_key(route_input: RouteInput) -> tuple[str, str, str | None]:
    return (route_input.platform, route_input.chat_id, route_input.thread_id)


class RoleRouter:
    def __init__(self, registry: AgentRoleRegistry, routing_config: dict | None = None):
        self.registry = registry
        self.routing_config = routing_config if isinstance(routing_config, dict) else {}

    @classmethod
    def from_config(cls, config: dict | None) -> "RoleRouter":
        cfg = config if isinstance(config, dict) else {}
        return cls(
            registry=AgentRoleRegistry.from_config(cfg),
            routing_config=cfg.get("role_routing") or {},
        )

    def route(self, route_input: RouteInput) -> RouteDecision:
        message = route_input.message or ""

        explicit = self._route_explicit_command(message)
        if explicit:
            return explicit

        binding = self._route_channel_binding(route_input)
        if binding:
            return binding

        keyword = self._route_keyword(message)
        if keyword:
            return keyword

        default_key = self.registry.default_key
        return RouteDecision(
            agent_key=default_key,
            route_reason="default",
            confidence=0.1,
            normalized_message=message,
        )

    def _route_explicit_command(self, message: str) -> RouteDecision | None:
        for pattern, reason in ((_ASK_RE, "slash_command"), (_AT_RE, "mention_alias")):
            match = pattern.match(message)
            if not match:
                continue
            key = normalize_agent_key(match.group(1))
            if not self.registry.has(key):
                continue
            normalized = match.group(2).strip() if match.lastindex and match.group(2) else ""
            return RouteDecision(
                agent_key=key,
                route_reason=reason,
                confidence=1.0,
                normalized_message=normalized,
            )
        return None

    def _route_channel_binding(self, route_input: RouteInput) -> RouteDecision | None:
        bindings = self.routing_config.get("channel_bindings") or []
        if not isinstance(bindings, list):
            return None
        platform, chat_id, thread_id = _channel_binding_key(route_input)
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            if str(binding.get("platform") or "") != platform:
                continue
            if str(binding.get("chat_id") or "") != chat_id:
                continue
            binding_thread = binding.get("thread_id")
            if binding_thread is not None and str(binding_thread) != str(thread_id or ""):
                continue
            key = normalize_agent_key(binding.get("default_role") or binding.get("role"))
            if not self.registry.has(key):
                continue
            return RouteDecision(
                agent_key=key,
                route_reason="channel_binding",
                confidence=0.9,
                normalized_message=route_input.message,
            )
        return None

    def _route_keyword(self, message: str) -> RouteDecision | None:
        rules = self.routing_config.get("keyword_rules") or []
        if not isinstance(rules, list):
            return None
        lowered = message.lower()
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            key = normalize_agent_key(rule.get("role"))
            if not self.registry.has(key):
                continue
            keywords = rule.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [keywords]
            for keyword in keywords:
                if str(keyword).lower() in lowered:
                    return RouteDecision(
                        agent_key=key,
                        route_reason="keyword_rule",
                        confidence=0.6,
                        normalized_message=message,
                    )
        return None
