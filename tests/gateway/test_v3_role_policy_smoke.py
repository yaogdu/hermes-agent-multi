import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
_MISSING = object()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_gateway_modules():
    module_names = [
        "gateway",
        "gateway.config",
        "gateway.whatsapp_identity",
        "gateway.agent_roles",
        "gateway.role_router",
        "gateway.role_dispatch",
        "gateway.session_under_test",
        "utils",
    ]
    saved = {name: sys.modules.get(name, _MISSING) for name in module_names}
    try:
        gateway_pkg = types.ModuleType("gateway")
        gateway_pkg.__path__ = [str(ROOT / "gateway")]
        sys.modules["gateway"] = gateway_pkg

        config_mod = types.ModuleType("gateway.config")

        class Platform(Enum):
            LOCAL = "local"
            FEISHU = "feishu"
            TELEGRAM = "telegram"
            DISCORD = "discord"
            WHATSAPP = "whatsapp"
            SIGNAL = "signal"
            BLUEBUBBLES = "bluebubbles"
            SLACK = "slack"
            YUANBAO = "yuanbao"

        config_mod.Platform = Platform
        config_mod.GatewayConfig = object
        config_mod.SessionResetPolicy = object
        config_mod.HomeChannel = object
        sys.modules["gateway.config"] = config_mod

        whatsapp_mod = types.ModuleType("gateway.whatsapp_identity")
        whatsapp_mod.canonical_whatsapp_identifier = lambda value: str(value or "").split("@", 1)[0]
        whatsapp_mod.normalize_whatsapp_identifier = whatsapp_mod.canonical_whatsapp_identifier
        sys.modules["gateway.whatsapp_identity"] = whatsapp_mod

        utils_mod = types.ModuleType("utils")
        utils_mod.atomic_replace = lambda src, dst: None
        sys.modules["utils"] = utils_mod

        agent_roles = _load_module("gateway.agent_roles", ROOT / "gateway" / "agent_roles.py")
        _load_module("gateway.role_router", ROOT / "gateway" / "role_router.py")
        role_dispatch = _load_module("gateway.role_dispatch", ROOT / "gateway" / "role_dispatch.py")
        session = _load_module("gateway.session_under_test", ROOT / "gateway" / "session.py")
        return agent_roles, role_dispatch, session, Platform
    finally:
        for name, module in saved.items():
            if module is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


agent_roles, role_dispatch, session, Platform = _load_gateway_modules()
build_role_overlay = agent_roles.build_role_overlay
route_event_to_role = role_dispatch.route_event_to_role
SessionSource = session.SessionSource
build_session_key = session.build_session_key


@dataclass(frozen=True)
class Event:
    text: str
    source: SessionSource


CONFIG = {
    "agent_roles": {
        "default": "assistant",
        "roles": {
            "assistant": {
                "enabled_toolsets": ["memory", "web"],
                "approval_policy": "standard",
            },
            "ops": {
                "display_name": "Ops Agent",
                "enabled_toolsets": ["memory", "terminal", "web"],
                "disabled_toolsets": ["web"],
                "memory_scope": "role_user_session",
                "approval_policy": "ops_safe",
                "system_prompt": "You are an ops agent.",
            },
        },
    },
    "role_routing": {
        "keyword_rules": [{"role": "ops", "keywords": ["告警", "sls"]}],
    },
    "approval_policies": {
        "ops_safe": {
            "allow": ["read_file"],
            "require_approval": ["terminal"],
            "deny": ["browser_navigate"],
        },
    },
}


class V3RolePolicySmokeTests(unittest.TestCase):
    def test_ask_ops_routes_session_overlay_and_policy(self):
        source = SessionSource(
            platform=Platform.FEISHU,
            chat_id="oc_general",
            chat_type="group",
            user_id="u1",
        )
        event = Event(text="/ask ops 查订单服务告警", source=source)

        routed_event, routed_source, decision = route_event_to_role(event, CONFIG)
        session_key = build_session_key(routed_source)
        overlay = build_role_overlay(
            config=CONFIG,
            agent_key=routed_source.agent_key,
            platform_toolsets=["memory", "web", "browser"],
            global_disabled_toolsets=[],
            base_ephemeral_prompt="base",
        )

        from tools.tool_policy import (
            decide_tool_policy,
            reset_current_tool_policy_configs,
            reset_current_tool_policy,
            set_current_tool_policy_configs,
            set_current_tool_policy,
        )

        policy_token = set_current_tool_policy(overlay.role.approval_policy)
        config_token = set_current_tool_policy_configs(CONFIG["approval_policies"])
        try:
            terminal_decision = decide_tool_policy("terminal", {})
            browser_decision = decide_tool_policy("browser_navigate", {})
        finally:
            reset_current_tool_policy_configs(config_token)
            reset_current_tool_policy(policy_token)

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(decision.route_reason, "slash_command")
        self.assertEqual(routed_event.text, "查订单服务告警")
        self.assertEqual(session_key, "agent:ops:feishu:group:oc_general:u1")
        self.assertEqual(overlay.role.memory_scope, "role_user_session")
        self.assertEqual(overlay.enabled_toolsets, ["memory", "terminal"])
        self.assertEqual(overlay.ephemeral_system_prompt, "base\n\nYou are an ops agent.")
        self.assertEqual(terminal_decision.action, "require_approval")
        self.assertEqual(browser_decision.action, "deny")


class V3GatewayEntrypointSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_message_routes_ask_role_before_agent_run(self):
        try:
            from gateway.config import GatewayConfig, Platform as RealPlatform, PlatformConfig
            from gateway.platforms.base import MessageEvent, MessageType
            from gateway.run import GatewayRunner, _AGENT_PENDING_SENTINEL
            from gateway.session import SessionSource, build_session_key as real_build_session_key
        except Exception as exc:
            self.skipTest(f"gateway.run dependencies unavailable: {exc}")

        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig(
            platforms={RealPlatform.FEISHU: PlatformConfig(enabled=True)}
        )
        runner.adapters = {
            RealPlatform.FEISHU: SimpleNamespace(
                send=AsyncMock(),
                _pending_messages={},
            )
        }
        runner.pairing_store = MagicMock()
        runner.session_store = MagicMock()
        runner.session_store._generate_session_key.side_effect = real_build_session_key
        runner.session_store.get_or_create_session.return_value = None
        runner._running_agents = {}
        runner._running_agents_ts = {}
        runner._busy_ack_ts = {}
        runner._pending_messages = {}
        runner._pending_approvals = {}
        runner._update_prompt_pending = {}
        runner._session_run_generation = {}
        runner._draining = False
        runner._is_user_authorized = lambda _source: True

        captured = {}

        async def _capture_agent_call(event, source, quick_key, run_generation):
            captured["event"] = event
            captured["source"] = source
            captured["quick_key"] = quick_key
            captured["run_generation"] = run_generation
            captured["sentinel_set"] = (
                runner._running_agents.get(quick_key) is _AGENT_PENDING_SENTINEL
            )
            return "ok"

        runner._handle_message_with_agent = _capture_agent_call

        source = SessionSource(
            platform=RealPlatform.FEISHU,
            chat_id="oc_general",
            chat_type="group",
            user_id="u1",
        )
        event = MessageEvent(
            text="/ask ops 查订单服务告警",
            message_type=MessageType.TEXT,
            source=source,
            message_id="m1",
        )

        with patch("gateway.run._load_gateway_config", return_value=CONFIG), patch(
            "hermes_cli.plugins.invoke_hook", return_value=[]
        ):
            result = await runner._handle_message(event)

        self.assertEqual(result, "ok")
        self.assertEqual(captured["event"].text, "查订单服务告警")
        self.assertEqual(captured["source"].agent_key, "ops")
        self.assertEqual(captured["quick_key"], "agent:ops:feishu:group:oc_general:u1")
        self.assertEqual(captured["run_generation"], 1)
        self.assertTrue(captured["sentinel_set"])
        self.assertNotIn(captured["quick_key"], runner._running_agents)
        self.assertNotIn(captured["quick_key"], runner._running_agents_ts)
        self.assertNotIn(captured["quick_key"], runner._busy_ack_ts)


if __name__ == "__main__":
    unittest.main()
