import ast
from pathlib import Path
import unittest


class ToolPolicyMetadataStaticTests(unittest.TestCase):
    def test_builtin_tool_register_calls_declare_metadata(self):
        tools_dir = Path(__file__).resolve().parents[2] / "tools"
        missing: list[str] = []

        for path in sorted(tools_dir.glob("*.py")):
            if path.name in {"__init__.py", "registry.py"}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for stmt in tree.body:
                call = getattr(stmt, "value", None)
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "register"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "registry"
                ):
                    continue
                if not any(keyword.arg == "metadata" for keyword in call.keywords):
                    missing.append(f"{path.name}:{stmt.lineno}")

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
