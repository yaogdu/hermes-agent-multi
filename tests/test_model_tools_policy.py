import json
import sys
import types
import unittest
from unittest.mock import patch

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda stream: {}
    yaml_stub.safe_dump = lambda data, *args, **kwargs: str(data)
    sys.modules["yaml"] = yaml_stub

import model_tools
from tools.tool_policy import reset_current_tool_policy, set_current_tool_policy


class ModelToolsPolicyTests(unittest.TestCase):
    def test_handle_function_call_blocks_by_tool_policy_before_dispatch(self):
        token = set_current_tool_policy("deny_dangerous")
        try:
            with patch.object(model_tools.registry, "dispatch") as dispatch:
                result = model_tools.handle_function_call(
                    "terminal",
                    {"command": "echo hi"},
                    task_id="t1",
                    skip_pre_tool_call_hook=True,
                )
        finally:
            reset_current_tool_policy(token)

        dispatch.assert_not_called()
        parsed = json.loads(result)
        self.assertEqual(parsed["tool_policy"]["action"], "deny")
        self.assertEqual(parsed["tool_policy"]["tool_name"], "terminal")

    def test_handle_function_call_can_skip_policy_when_checked_upstream(self):
        token = set_current_tool_policy("deny_dangerous")
        try:
            with patch.object(model_tools.registry, "dispatch", return_value='{"ok": true}') as dispatch:
                result = model_tools.handle_function_call(
                    "terminal",
                    {"command": "echo hi"},
                    task_id="t1",
                    skip_pre_tool_call_hook=True,
                    skip_tool_policy_check=True,
                )
        finally:
            reset_current_tool_policy(token)

        dispatch.assert_called_once()
        self.assertEqual(result, '{"ok": true}')


if __name__ == "__main__":
    unittest.main()
