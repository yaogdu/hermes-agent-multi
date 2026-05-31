import json
import os
import sys
import types
import unittest

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda stream: {}
    yaml_stub.safe_dump = lambda data, *args, **kwargs: str(data)
    sys.modules["yaml"] = yaml_stub

from tools.tool_policy import (
    classify_tool,
    decide_tool_policy,
    enforce_tool_policy,
    get_current_tool_policy,
    reset_current_tool_policy_configs,
    reset_current_tool_policy,
    set_current_tool_policy_configs,
    set_current_tool_policy,
)


class ToolPolicyTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        os.environ.pop("HERMES_APPROVAL_POLICY", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_classifies_common_tool_categories(self):
        self.assertEqual(classify_tool("read_file"), "read_only")
        self.assertEqual(classify_tool("write_file"), "write")
        self.assertEqual(classify_tool("terminal"), "external_action")
        self.assertEqual(classify_tool("browser_console"), "external_action")
        self.assertEqual(classify_tool("browser_vision"), "read_only")
        self.assertEqual(classify_tool("mcp_slack_send_message"), "external_action")

    def test_metadata_category_overrides_name_fallback(self):
        self.assertEqual(
            classify_tool("mcp_docs_read", {"risk_category": "read_only"}),
            "read_only",
        )
        self.assertEqual(
            classify_tool("odd_tool", {"tool_policy": {"category": "external"}}),
            "external_action",
        )

    def test_mcp_annotations_drive_category(self):
        self.assertEqual(
            classify_tool("mcp_docs_search", {"annotations": {"readOnlyHint": True}}),
            "read_only",
        )
        self.assertEqual(
            classify_tool("mcp_issue_create", {"annotations": {"destructiveHint": True}}),
            "external_action",
        )

    def test_registry_metadata_is_used_without_exposing_to_schema(self):
        from tools.registry import registry

        registry.register(
            name="metadata_policy_test_tool",
            toolset="metadata_policy_test",
            schema={
                "name": "metadata_policy_test_tool",
                "description": "test tool",
                "parameters": {"type": "object", "properties": {}},
            },
            handler=lambda _args, **_kw: "{}",
            metadata={"risk_category": "write"},
        )
        try:
            self.assertEqual(classify_tool("metadata_policy_test_tool"), "write")
            definitions = registry.get_definitions({"metadata_policy_test_tool"}, quiet=True)
        finally:
            registry.deregister("metadata_policy_test_tool")

        self.assertEqual(definitions[0]["function"]["name"], "metadata_policy_test_tool")
        self.assertNotIn("metadata", definitions[0]["function"])

    def test_ops_safe_requires_approval_for_external_action(self):
        decision = decide_tool_policy("terminal", {}, policy_name="ops_safe")

        self.assertEqual(decision.action, "require_approval")
        self.assertEqual(decision.category, "external_action")

    def test_ops_safe_allows_read_only_and_write_tools(self):
        self.assertEqual(
            decide_tool_policy("read_file", {}, policy_name="ops_safe").action,
            "allow",
        )
        self.assertEqual(
            decide_tool_policy("write_file", {}, policy_name="ops_safe").action,
            "allow",
        )

    def test_deny_dangerous_denies_external_action(self):
        result = enforce_tool_policy("terminal", {}, policy_name="deny_dangerous")
        parsed = json.loads(result)

        self.assertIn("error", parsed)
        self.assertEqual(parsed["tool_policy"]["action"], "deny")

    def test_contextvar_policy_is_used(self):
        token = set_current_tool_policy("manual")
        try:
            self.assertEqual(get_current_tool_policy(), "manual")
            decision = decide_tool_policy("write_file", {})
        finally:
            reset_current_tool_policy(token)

        self.assertEqual(decision.action, "require_approval")

    def test_custom_policy_config_overrides_named_policy(self):
        policy_token = set_current_tool_policy("ops_safe")
        config_token = set_current_tool_policy_configs({
            "ops_safe": {
                "allow": ["web.search"],
                "require_approval": ["write_file"],
                "deny": ["terminal"],
            }
        })
        try:
            self.assertEqual(decide_tool_policy("web_search", {}).action, "allow")
            self.assertEqual(decide_tool_policy("write_file", {}).action, "require_approval")
            decision = decide_tool_policy("terminal", {})
        finally:
            reset_current_tool_policy_configs(config_token)
            reset_current_tool_policy(policy_token)

        self.assertEqual(decision.action, "deny")
        self.assertEqual(decision.policy_name, "ops_safe")

    def test_custom_policy_supports_approval_required_alias(self):
        config_token = set_current_tool_policy_configs({
            "review": {"approval_required": ["category:write"]}
        })
        try:
            decision = decide_tool_policy("patch", {}, policy_name="review")
        finally:
            reset_current_tool_policy_configs(config_token)

        self.assertEqual(decision.action, "require_approval")


if __name__ == "__main__":
    unittest.main()
