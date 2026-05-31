import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda stream: {}
    yaml_stub.safe_dump = lambda data, *args, **kwargs: str(data)
    sys.modules["yaml"] = yaml_stub

from tools.code_execution_tool import _execute_remote, _rpc_server_loop


class CodeExecutionToolPolicyTests(unittest.TestCase):
    def test_execute_remote_passes_tool_policy_to_rpc_thread(self):
        class FakeEnv:
            def __init__(self):
                self.commands = []

            def get_temp_dir(self):
                return "/tmp"

            def execute(self, command, cwd=None, timeout=None):
                self.commands.append((command, cwd, timeout))
                if "command -v python3" in command:
                    return {"output": "OK\n"}
                if "python3 script.py" in command:
                    return {"output": "done\n", "returncode": 0}
                return {"output": ""}

        env = FakeEnv()
        fake_thread = MagicMock()

        with patch(
            "tools.code_execution_tool._load_config",
            return_value={"timeout": 30, "max_tool_calls": 5},
        ), patch(
            "tools.code_execution_tool._get_or_create_env",
            return_value=(env, "ssh"),
        ), patch(
            "tools.code_execution_tool._ship_file_to_remote",
        ), patch(
            "tools.code_execution_tool.threading.Thread",
            return_value=fake_thread,
        ) as thread_factory:
            result = json.loads(
                _execute_remote(
                    "print('done')",
                    "task-1",
                    ["terminal"],
                    tool_policy="deny_dangerous",
                    tool_policy_configs={
                        "deny_dangerous": {"deny": ["terminal"]},
                    },
                )
            )

        self.assertEqual(result["status"], "success")
        rpc_args = thread_factory.call_args.kwargs["args"]
        self.assertEqual(rpc_args[-2], "deny_dangerous")
        self.assertEqual(rpc_args[-1], {"deny_dangerous": {"deny": ["terminal"]}})

    def test_rpc_server_loop_applies_passed_tool_policy(self):
        class FakeConn:
            def __init__(self, request: bytes):
                self._chunks = [request, b""]
                self.sent = []
                self.closed = False

            def settimeout(self, _timeout):
                pass

            def recv(self, _size):
                return self._chunks.pop(0)

            def sendall(self, payload):
                self.sent.append(payload)

            def close(self):
                self.closed = True

        class FakeServerSocket:
            def __init__(self, conn):
                self.conn = conn

            def settimeout(self, _timeout):
                pass

            def accept(self):
                return self.conn, None

        request = (
            json.dumps(
                {
                    "tool": "terminal",
                    "args": {"command": "echo should-not-run"},
                }
            ).encode("utf-8")
            + b"\n"
        )
        conn = FakeConn(request)
        server_sock = FakeServerSocket(conn)

        tool_call_log = []
        tool_call_counter = [0]
        _rpc_server_loop(
            server_sock,
            "task-1",
            tool_call_log,
            tool_call_counter,
            5,
            frozenset({"terminal"}),
            "deny_dangerous",
        )

        response = conn.sent[0].decode("utf-8").strip()
        parsed = json.loads(response)
        self.assertEqual(parsed["tool_policy"]["action"], "deny")
        self.assertEqual(parsed["tool_policy"]["tool_name"], "terminal")
        self.assertEqual(tool_call_counter[0], 1)

    def test_rpc_server_loop_applies_custom_tool_policy_config(self):
        class FakeConn:
            def __init__(self, request: bytes):
                self._chunks = [request, b""]
                self.sent = []

            def settimeout(self, _timeout):
                pass

            def recv(self, _size):
                return self._chunks.pop(0)

            def sendall(self, payload):
                self.sent.append(payload)

            def close(self):
                pass

        class FakeServerSocket:
            def __init__(self, conn):
                self.conn = conn

            def settimeout(self, _timeout):
                pass

            def accept(self):
                return self.conn, None

        request = (
            json.dumps(
                {
                    "tool": "web_search",
                    "args": {"query": "agent runtime"},
                }
            ).encode("utf-8")
            + b"\n"
        )
        conn = FakeConn(request)

        _rpc_server_loop(
            FakeServerSocket(conn),
            "task-1",
            [],
            [0],
            5,
            frozenset({"web_search"}),
            "research_safe",
            {"research_safe": {"deny": ["web.search"]}},
        )

        parsed = json.loads(conn.sent[0].decode("utf-8").strip())
        self.assertEqual(parsed["tool_policy"]["action"], "deny")
        self.assertEqual(parsed["tool_policy"]["policy_name"], "research_safe")
        self.assertEqual(parsed["tool_policy"]["tool_name"], "web_search")


if __name__ == "__main__":
    unittest.main()
