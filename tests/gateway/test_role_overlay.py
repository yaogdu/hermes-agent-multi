import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def _load_agent_roles_module():
    path = Path(__file__).resolve().parents[2] / "gateway" / "agent_roles.py"
    spec = importlib.util.spec_from_file_location("agent_roles_overlay_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["agent_roles_overlay_under_test"] = module
    spec.loader.exec_module(module)
    return module


agent_roles = _load_agent_roles_module()
build_role_overlay = agent_roles.build_role_overlay


class RoleOverlayTests(unittest.TestCase):
    def test_default_role_inherits_platform_toolsets(self):
        overlay = build_role_overlay(
            config={},
            agent_key="main",
            platform_toolsets=["memory", "web"],
            global_disabled_toolsets=[],
            base_ephemeral_prompt="base prompt",
        )

        self.assertEqual(overlay.role.key, "main")
        self.assertEqual(overlay.enabled_toolsets, ["memory", "web"])
        self.assertEqual(overlay.disabled_toolsets, [])
        self.assertEqual(overlay.ephemeral_system_prompt, "base prompt")

    def test_role_enabled_toolsets_replace_platform_toolsets(self):
        overlay = build_role_overlay(
            config={
                "agent_roles": {
                    "roles": {
                        "ops": {
                            "enabled_toolsets": ["memory", "terminal"],
                            "disabled_toolsets": ["terminal"],
                            "system_prompt": "You are an ops agent.",
                        }
                    }
                }
            },
            agent_key="ops",
            platform_toolsets=["web", "browser"],
            global_disabled_toolsets=["browser"],
            base_ephemeral_prompt="base prompt",
        )

        self.assertEqual(overlay.role.key, "ops")
        self.assertEqual(overlay.enabled_toolsets, ["memory"])
        self.assertEqual(overlay.disabled_toolsets, ["browser", "terminal"])
        self.assertEqual(
            overlay.ephemeral_system_prompt,
            "base prompt\n\nYou are an ops agent.",
        )

    def test_role_prompt_file_is_loaded_relative_to_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "ops.md").write_text("Prompt from file", encoding="utf-8")
            overlay = build_role_overlay(
                config={
                    "agent_roles": {
                        "roles": {
                            "ops": {
                                "system_prompt_file": "ops.md",
                                "system_prompt": "Inline prompt",
                            }
                        }
                    }
                },
                agent_key="ops",
                platform_toolsets=[],
                base_ephemeral_prompt="base",
                config_dir=config_dir,
            )

        self.assertEqual(
            overlay.ephemeral_system_prompt,
            "base\n\nPrompt from file\n\nInline prompt",
        )
        self.assertTrue(overlay.cache_key["system_prompt_file_loaded"])


if __name__ == "__main__":
    unittest.main()
