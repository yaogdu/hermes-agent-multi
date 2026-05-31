import os
import sys
import types
import unittest

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda stream: {}
    yaml_stub.safe_dump = lambda data, *args, **kwargs: str(data)
    sys.modules["yaml"] = yaml_stub

import tools.approval as approval_module
from tools.approval import (
    check_all_command_guards,
    check_dangerous_command,
    disable_session_yolo,
    enable_session_yolo,
    reset_current_approval_policy,
    reset_current_session_key,
    set_current_approval_policy,
    set_current_session_key,
)


class RoleApprovalPolicyTests(unittest.TestCase):
    def setUp(self):
        self._old_env = dict(os.environ)
        self._clear_env()
        approval_module.clear_session("role-policy-test")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        approval_module.clear_session("role-policy-test")

    def _clear_env(self):
        for key in (
            "HERMES_INTERACTIVE",
            "HERMES_GATEWAY_SESSION",
            "HERMES_EXEC_ASK",
            "HERMES_CRON_SESSION",
            "HERMES_APPROVAL_POLICY",
        ):
            os.environ.pop(key, None)

    def test_deny_dangerous_policy_blocks_non_interactive_auto_approve(self):
        token = set_current_approval_policy("deny_dangerous")
        try:
            result = check_dangerous_command("rm -rf /tmp/hermes-role-policy-test", "local")
        finally:
            reset_current_approval_policy(token)

        self.assertFalse(result["approved"])
        self.assertEqual(result["outcome"], "policy_denied")
        self.assertEqual(result["role_approval_policy"], "deny_dangerous")

    def test_deny_dangerous_policy_beats_session_yolo(self):
        session_token = set_current_session_key("role-policy-test")
        policy_token = set_current_approval_policy("deny_dangerous")
        enable_session_yolo("role-policy-test")
        try:
            result = check_all_command_guards("rm -rf /tmp/hermes-role-policy-test", "local")
        finally:
            disable_session_yolo("role-policy-test")
            reset_current_approval_policy(policy_token)
            reset_current_session_key(session_token)
            approval_module.clear_session("role-policy-test")

        self.assertFalse(result["approved"])
        self.assertEqual(result["outcome"], "policy_denied")

    def test_standard_policy_preserves_existing_non_interactive_behavior(self):
        token = set_current_approval_policy("standard")
        try:
            result = check_dangerous_command("rm -rf /tmp/hermes-role-policy-test", "local")
        finally:
            reset_current_approval_policy(token)

        self.assertTrue(result["approved"])

    def test_policy_can_fallback_to_environment(self):
        os.environ["HERMES_APPROVAL_POLICY"] = "manual"

        self.assertEqual(approval_module.get_current_approval_policy(), "manual")


if __name__ == "__main__":
    unittest.main()
