import unittest
import importlib.util
import sys
from pathlib import Path


def _load_gateway_module(module_name: str):
    path = Path(__file__).resolve().parents[2] / "gateway" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_gateway_module("agent_roles")
role_router = _load_gateway_module("role_router")
RoleRouter = role_router.RoleRouter
RouteInput = role_router.RouteInput


CONFIG = {
    "agent_roles": {
        "default": "assistant",
        "roles": {
            "assistant": {},
            "ops": {},
            "coder": {},
        },
    },
    "role_routing": {
        "channel_bindings": [
            {
                "platform": "feishu",
                "chat_id": "oc_ops",
                "default_role": "ops",
            }
        ],
        "keyword_rules": [
            {"role": "ops", "keywords": ["告警", "n9e", "sls"]},
            {"role": "coder", "keywords": ["代码", "bug", "pr"]},
        ],
    },
}


class RoleRouterTests(unittest.TestCase):
    def test_slash_command_routes_to_role_and_strips_prefix(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_general",
            message="/ask ops 查一下订单服务告警",
        ))

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(decision.route_reason, "slash_command")
        self.assertEqual(decision.confidence, 1.0)
        self.assertEqual(decision.normalized_message, "查一下订单服务告警")

    def test_mention_alias_routes_to_role(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_general",
            message="@coder 写个接口",
        ))

        self.assertEqual(decision.agent_key, "coder")
        self.assertEqual(decision.route_reason, "mention_alias")
        self.assertEqual(decision.normalized_message, "写个接口")

    def test_unknown_explicit_role_falls_back_to_default(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_general",
            message="/ask researcher 查竞品",
        ))

        self.assertEqual(decision.agent_key, "assistant")
        self.assertEqual(decision.route_reason, "default")

    def test_channel_binding_routes_to_role(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_ops",
            message="帮我看一下",
        ))

        self.assertEqual(decision.agent_key, "ops")
        self.assertEqual(decision.route_reason, "channel_binding")

    def test_keyword_routes_to_role(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_general",
            message="这个 bug 帮我看一下",
        ))

        self.assertEqual(decision.agent_key, "coder")
        self.assertEqual(decision.route_reason, "keyword_rule")

    def test_default_route(self):
        router = RoleRouter.from_config(CONFIG)
        decision = router.route(RouteInput(
            platform="feishu",
            chat_id="oc_general",
            message="你好",
        ))

        self.assertEqual(decision.agent_key, "assistant")
        self.assertEqual(decision.route_reason, "default")


if __name__ == "__main__":
    unittest.main()
