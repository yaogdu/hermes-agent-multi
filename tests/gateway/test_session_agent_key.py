import importlib.util
import sys
import types
import unittest
from enum import Enum
from pathlib import Path


_MISSING = object()


def _load_session_module():
    root = Path(__file__).resolve().parents[2]
    module_names = [
        "gateway",
        "gateway.config",
        "gateway.whatsapp_identity",
        "gateway.agent_roles",
        "gateway.session_under_test",
        "utils",
    ]
    saved_modules = {name: sys.modules.get(name, _MISSING) for name in module_names}

    try:
        gateway_pkg = types.ModuleType("gateway")
        gateway_pkg.__path__ = [str(root / "gateway")]
        sys.modules["gateway"] = gateway_pkg

        config_mod = types.ModuleType("gateway.config")

        class Platform(Enum):
            LOCAL = "local"
            TELEGRAM = "telegram"
            DISCORD = "discord"
            WHATSAPP = "whatsapp"
            SIGNAL = "signal"
            BLUEBUBBLES = "bluebubbles"
            SLACK = "slack"
            YUANBAO = "yuanbao"

        class GatewayConfig:
            pass

        class SessionResetPolicy:
            pass

        class HomeChannel:
            def to_dict(self):
                return {}

        config_mod.Platform = Platform
        config_mod.GatewayConfig = GatewayConfig
        config_mod.SessionResetPolicy = SessionResetPolicy
        config_mod.HomeChannel = HomeChannel
        sys.modules["gateway.config"] = config_mod

        whatsapp_mod = types.ModuleType("gateway.whatsapp_identity")

        def canonical_whatsapp_identifier(value):
            text = str(value or "")
            return text.split("@", 1)[0] if "@" in text else text

        whatsapp_mod.canonical_whatsapp_identifier = canonical_whatsapp_identifier
        whatsapp_mod.normalize_whatsapp_identifier = canonical_whatsapp_identifier
        sys.modules["gateway.whatsapp_identity"] = whatsapp_mod

        utils_mod = types.ModuleType("utils")
        utils_mod.atomic_replace = lambda src, dst: None
        sys.modules["utils"] = utils_mod

        roles_spec = importlib.util.spec_from_file_location(
            "gateway.agent_roles",
            root / "gateway" / "agent_roles.py",
        )
        roles_mod = importlib.util.module_from_spec(roles_spec)
        assert roles_spec.loader is not None
        sys.modules["gateway.agent_roles"] = roles_mod
        roles_spec.loader.exec_module(roles_mod)

        session_spec = importlib.util.spec_from_file_location(
            "gateway.session_under_test",
            root / "gateway" / "session.py",
        )
        session_mod = importlib.util.module_from_spec(session_spec)
        assert session_spec.loader is not None
        sys.modules["gateway.session_under_test"] = session_mod
        session_spec.loader.exec_module(session_mod)
        return session_mod
    finally:
        for name, module in saved_modules.items():
            if module is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


session = _load_session_module()
Platform = session.Platform
SessionSource = session.SessionSource
build_session_key = session.build_session_key


class SessionAgentKeyTests(unittest.TestCase):
    def test_default_agent_key_preserves_legacy_key(self):
        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="99",
            chat_type="dm",
        )

        self.assertEqual(source.agent_key, "main")
        self.assertEqual(build_session_key(source), "agent:main:telegram:dm:99")

    def test_agent_key_is_serialized_and_defaulted(self):
        source = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="99",
            chat_type="dm",
            agent_key="Ops Agent",
        )

        data = source.to_dict()
        self.assertEqual(data["agent_key"], "ops-agent")
        self.assertEqual(SessionSource.from_dict(data).agent_key, "ops-agent")
        self.assertEqual(
            SessionSource.from_dict({"platform": "telegram", "chat_id": "99"}).agent_key,
            "main",
        )

    def test_agent_key_partitions_dm_sessions(self):
        ops = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="99",
            chat_type="dm",
            agent_key="ops",
        )
        coder = SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="99",
            chat_type="dm",
            agent_key="coder",
        )

        self.assertEqual(build_session_key(ops), "agent:ops:telegram:dm:99")
        self.assertEqual(build_session_key(coder), "agent:coder:telegram:dm:99")
        self.assertNotEqual(build_session_key(ops), build_session_key(coder))

    def test_agent_key_partitions_group_sessions_before_user_scope(self):
        ops = SessionSource(
            platform=Platform.DISCORD,
            chat_id="guild-123",
            chat_type="group",
            user_id="alice",
            agent_key="ops",
        )
        coder = SessionSource(
            platform=Platform.DISCORD,
            chat_id="guild-123",
            chat_type="group",
            user_id="alice",
            agent_key="coder",
        )

        self.assertEqual(build_session_key(ops), "agent:ops:discord:group:guild-123:alice")
        self.assertEqual(build_session_key(coder), "agent:coder:discord:group:guild-123:alice")


if __name__ == "__main__":
    unittest.main()
