from __future__ import annotations

import unittest

from scopex.agent.model_proxy import RequestRejected
from scopex.agent.request_policy import OpenClawRequestPolicy


def tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def payload(tool_names: list[str]) -> dict:
    return {
        "model": "local-model",
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
        "tool_choice": "auto",
        "tools": [tool(name) for name in tool_names],
        "messages": [{"role": "user", "content": "inspect"}],
    }


def toolless_payload() -> dict:
    return {
        "model": "local-model",
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [
            {"role": "system", "content": "Summarize the prior conversation."},
            {"role": "user", "content": "conversation transcript"},
        ],
    }


class RequestPolicyTests(unittest.TestCase):
    def test_configured_view_image_surface_is_accepted(self):
        policy = OpenClawRequestPolicy(
            "local-model",
            2048,
            frozenset({"read", "exec", "process", "view_image"}),
        )
        policy.validate(payload(["read", "exec", "process", "view_image"]))

    def test_configured_progress_card_surface_is_accepted(self):
        names = ["read", "exec", "process", "view_image", "progress_card"]
        policy = OpenClawRequestPolicy(
            "local-model",
            2048,
            frozenset(names),
        )
        policy.validate(payload(names))

    def test_toolless_internal_request_is_accepted(self):
        policy = OpenClawRequestPolicy("local-model", 2048)
        policy.validate(toolless_payload())

    def test_subset_of_configured_tools_is_accepted(self):
        policy = OpenClawRequestPolicy(
            "local-model",
            2048,
            frozenset({"read", "exec", "process", "view_image"}),
        )
        policy.validate(payload(["read", "exec"]))

    def test_unconfigured_view_image_surface_is_rejected(self):
        policy = OpenClawRequestPolicy("local-model", 2048)
        with self.assertRaises(RequestRejected):
            policy.validate(payload(["read", "exec", "process", "view_image"]))

    def test_duplicate_tool_surface_is_rejected(self):
        policy = OpenClawRequestPolicy("local-model", 2048)
        with self.assertRaises(RequestRejected):
            policy.validate(payload(["read", "read"]))

    def test_malformed_tools_field_is_rejected(self):
        policy = OpenClawRequestPolicy("local-model", 2048)
        body = toolless_payload()
        body["tools"] = "read"
        with self.assertRaises(RequestRejected):
            policy.validate(body)


if __name__ == "__main__":
    unittest.main()
