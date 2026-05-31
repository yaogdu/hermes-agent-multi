import unittest
import importlib.util
import sys
from pathlib import Path


def _load_agent_roles_module():
    path = Path(__file__).resolve().parents[2] / "gateway" / "agent_roles.py"
    spec = importlib.util.spec_from_file_location("agent_roles_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["agent_roles_under_test"] = module
    spec.loader.exec_module(module)
    return module


agent_roles = _load_agent_roles_module()
AgentRoleRegistry = agent_roles.AgentRoleRegistry
normalize_agent_key = agent_roles.normalize_agent_key


class AgentRoleRegistryTests(unittest.TestCase):
    def test_default_registry_keeps_main_role(self):
        registry = AgentRoleRegistry.from_config({})

        self.assertEqual(registry.default_key, "main")
        self.assertEqual(registry.get(None).key, "main")
        self.assertEqual(registry.keys(), ["main"])

    def test_parses_multiple_roles(self):
        registry = AgentRoleRegistry.from_config({
            "agent_roles": {
                "default": "assistant",
                "roles": {
                    "assistant": {
                        "display_name": "General Assistant",
                        "enabled_toolsets": ["web", "memory"],
                    },
                    "Ops Agent": {
                        "model": "gpt-5.1",
                        "provider": "openai",
                        "disabled_toolsets": "terminal",
                        "memory_scope": "role_user_session",
                        "approval_policy": "ops_safe",
                    },
                },
            }
        })

        self.assertEqual(registry.default_key, "assistant")
        self.assertTrue(registry.has("ops-agent"))
        self.assertEqual(registry.get("ops-agent").model, "gpt-5.1")
        self.assertEqual(registry.get("ops-agent").disabled_toolsets, ["terminal"])
        self.assertEqual(registry.get("assistant").enabled_toolsets, ["web", "memory"])

    def test_missing_default_role_is_created(self):
        registry = AgentRoleRegistry.from_config({
            "agent_roles": {
                "default": "assistant",
                "roles": {"ops": {"model": "x"}},
            }
        })

        self.assertTrue(registry.has("assistant"))
        self.assertTrue(registry.has("ops"))

    def test_normalize_agent_key(self):
        self.assertEqual(normalize_agent_key("Ops Agent"), "ops-agent")
        self.assertEqual(normalize_agent_key(""), "main")


if __name__ == "__main__":
    unittest.main()
