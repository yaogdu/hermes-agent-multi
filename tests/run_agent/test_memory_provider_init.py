"""Regression tests for memory provider selection during AIAgent init."""

from types import SimpleNamespace
import sys
import types
import unittest
from unittest.mock import patch

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda stream: {}
    yaml_stub.safe_dump = lambda data, *args, **kwargs: str(data)
    sys.modules["yaml"] = yaml_stub


def test_blank_memory_provider_does_not_auto_enable_honcho():
    """Blank memory.provider should remain opt-out even if Honcho fallback looks configured."""
    cfg = {"memory": {"provider": ""}, "agent": {}}
    honcho_cfg = SimpleNamespace(enabled=True, api_key="stale-key", base_url=None)

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.save_config") as save_config,
        patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=honcho_cfg,
        ) as from_global_config,
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert agent._memory_manager is None
    from_global_config.assert_not_called()
    load_memory_provider.assert_not_called()
    save_config.assert_not_called()


class TestRoleMemoryGovernance(unittest.TestCase):
    def test_role_memory_scope_flows_to_memory_provider(self):
        from agent.runtime_governance import (
            normalize_agent_key,
            normalize_memory_scope,
            role_scoped_gateway_session_key,
            role_scoped_identity,
        )

        agent_key = normalize_agent_key("Ops Agent")
        memory_scope = normalize_memory_scope("role-user-session")

        self.assertEqual(agent_key, "ops-agent")
        self.assertEqual(memory_scope, "role_user_session")
        self.assertEqual(
            role_scoped_gateway_session_key(
                "agent:ops:feishu:group:room-1",
                agent_key=agent_key,
                memory_scope=memory_scope,
            ),
            "agent:ops:feishu:group:room-1",
        )
        self.assertEqual(
            role_scoped_identity("default", agent_key=agent_key, memory_scope=memory_scope),
            "default:ops-agent",
        )

    def test_role_global_memory_scope_uses_role_global_session_key(self):
        from agent.runtime_governance import (
            normalize_agent_key,
            normalize_memory_scope,
            role_scoped_gateway_session_key,
            role_scoped_identity,
        )

        agent_key = normalize_agent_key("ops")
        memory_scope = normalize_memory_scope("role_global")

        self.assertEqual(
            role_scoped_gateway_session_key(
                "agent:ops:feishu:group:room-1",
                agent_key=agent_key,
                memory_scope=memory_scope,
            ),
            "agent:ops:global",
        )
        self.assertEqual(
            role_scoped_identity("default", agent_key=agent_key, memory_scope=memory_scope),
            "default:ops",
        )


if __name__ == "__main__":
    unittest.main()
