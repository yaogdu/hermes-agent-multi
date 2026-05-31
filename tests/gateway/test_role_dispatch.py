import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


def _load_gateway_module(module_name: str):
    path = Path(__file__).resolve().parents[2] / "gateway" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_gateway_module("agent_roles")
_load_gateway_module("role_router")
role_dispatch = _load_gateway_module("role_dispatch")
route_event_to_role = role_dispatch.route_event_to_role


class Platform(Enum):
    FEISHU = "feishu"


@dataclass(frozen=True)
class Source:
    platform: Platform
    chat_id: str
    chat_type: str = "dm"
    user_id: str | None = None
    thread_id: str | None = None
    agent_key: str = "main"


@dataclass(frozen=True)
class Event:
    text: str
    source: Source


CONFIG = {
    "agent_roles": {
        "default": "assistant",
        "roles": {
            "assistant": {},
            "ops": {},
        },
    },
    "role_routing": {
        "channel_bindings": [
            {"platform": "feishu", "chat_id": "oc_ops", "default_role": "ops"},
        ],
    },
}


class RoleDispatchTests(unittest.TestCase):
    def test_explicit_role_updates_source_and_strips_prefix(self):
        source = Source(platform=Platform.FEISHU, chat_id="oc_general", user_id="u1")
        event = Event(text="/ask ops 查订单告警", source=source)

        routed_event, routed_source, decision = route_event_to_role(event, CONFIG)

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(decision.route_reason, "slash_command")
        self.assertEqual(routed_source.agent_key, "ops")
        self.assertEqual(routed_event.source.agent_key, "ops")
        self.assertEqual(routed_event.text, "查订单告警")
        self.assertEqual(event.source.agent_key, "main")

    def test_channel_binding_updates_source_without_rewriting_text(self):
        source = Source(platform=Platform.FEISHU, chat_id="oc_ops", user_id="u1")
        event = Event(text="看下这个告警", source=source)

        routed_event, routed_source, decision = route_event_to_role(event, CONFIG)

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(decision.route_reason, "channel_binding")
        self.assertEqual(routed_source.agent_key, "ops")
        self.assertEqual(routed_event.text, "看下这个告警")

    def test_object_config_is_supported(self):
        cfg = types.SimpleNamespace(
            agent_roles=CONFIG["agent_roles"],
            role_routing=CONFIG["role_routing"],
        )
        source = Source(platform=Platform.FEISHU, chat_id="oc_ops", user_id="u1")
        event = Event(text="看下这个告警", source=source)

        routed_event, _, decision = route_event_to_role(event, cfg)

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(routed_event.source.agent_key, "ops")


if __name__ == "__main__":
    unittest.main()
