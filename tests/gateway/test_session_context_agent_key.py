import importlib.util
import os
import unittest
from pathlib import Path


def _load_session_context_module():
    path = Path(__file__).resolve().parents[2] / "gateway" / "session_context.py"
    spec = importlib.util.spec_from_file_location("session_context_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


session_context = _load_session_context_module()


class SessionContextAgentKeyTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("HERMES_AGENT_KEY", None)
        for var in session_context._VAR_MAP.values():
            var.set(session_context._UNSET)

    def test_agent_key_set_via_contextvars(self):
        tokens = session_context.set_session_vars(agent_key="ops")

        self.assertEqual(session_context.get_session_env("HERMES_AGENT_KEY"), "ops")

        session_context.clear_session_vars(tokens)
        self.assertEqual(session_context.get_session_env("HERMES_AGENT_KEY"), "")

    def test_agent_key_falls_back_to_env_only_when_unset(self):
        os.environ["HERMES_AGENT_KEY"] = "env-agent"

        self.assertEqual(session_context.get_session_env("HERMES_AGENT_KEY"), "env-agent")

        tokens = session_context.set_session_vars(agent_key="ctx-agent")
        self.assertEqual(session_context.get_session_env("HERMES_AGENT_KEY"), "ctx-agent")

        session_context.clear_session_vars(tokens)
        self.assertEqual(session_context.get_session_env("HERMES_AGENT_KEY"), "")


if __name__ == "__main__":
    unittest.main()
