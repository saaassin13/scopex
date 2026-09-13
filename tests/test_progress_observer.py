from __future__ import annotations

import json
import unittest

from scopex.events.observer import AgentProgressObserver
from scopex.events.progress import EventType, InMemoryEventSink


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


class ProgressObserverTests(unittest.TestCase):
    def test_progress_card_becomes_dedicated_progress_event(self):
        sink = InMemoryEventSink()
        observer = AgentProgressObserver("task-1", sink)
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    tool_call(
                        "p1",
                        "progress_card",
                        {
                            "plan": [
                                {"step": "读取日志并定位失败时间", "status": "completed"},
                                {"step": "查找失败附近图片", "status": "in_progress"},
                                {"step": "综合日志和图片", "status": "pending"},
                            ],
                            "markdown": "已定位失败时间，正在匹配附近图片。",
                        },
                    )
                ],
            },
            {"role": "tool", "tool_call_id": "p1", "content": "Progress card updated"},
        ]

        observer.observe_request(2, messages)

        self.assertEqual(
            [event.type for event in sink.events],
            [EventType.MODEL_REQUEST, EventType.PROGRESS_UPDATE],
        )
        progress = sink.events[1]
        self.assertEqual(progress.data["markdown"], "已定位失败时间，正在匹配附近图片。")
        self.assertEqual(progress.data["plan"][1]["status"], "in_progress")
        self.assertEqual(progress.data["plan"][1]["step"], "查找失败附近图片")

    def test_tool_actions_and_result_preview_are_user_readable(self):
        sink = InMemoryEventSink()
        observer = AgentProgressObserver("task-1", sink)
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    tool_call(
                        "e1",
                        "exec",
                        {
                            "command": "ls -la /agent-data/poc07/images",
                            "title": "查找失败时间附近的图片",
                        },
                    ),
                    tool_call(
                        "i1",
                        "view_image",
                        {
                            "paths": ["/agent-data/poc07/images/a.jpg", "/agent-data/poc07/images/b.jpg"],
                            "prompt": "比较两张图的清晰度和曝光。",
                        },
                    ),
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "e1",
                "content": "a.jpg\nb.jpg\n",
            },
            {
                "role": "tool",
                "tool_call_id": "i1",
                "content": "Loaded 2 images into private model context for inspection.",
            },
        ]

        observer.observe_request(3, messages)

        calls = [event for event in sink.events if event.type is EventType.TOOL_CALL]
        results = [event for event in sink.events if event.type is EventType.TOOL_RESULT]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].data["title"], "查找失败时间附近的图片")
        self.assertEqual(calls[0].data["command"], "ls -la /agent-data/poc07/images")
        self.assertEqual(calls[1].data["paths"], [
            "/agent-data/poc07/images/a.jpg",
            "/agent-data/poc07/images/b.jpg",
        ])
        self.assertEqual(calls[1].data["prompt"], "比较两张图的清晰度和曝光。")
        self.assertEqual(results[0].data["preview"], "a.jpg\nb.jpg")
        self.assertIn("Loaded 2 images", results[1].data["preview"])


if __name__ == "__main__":
    unittest.main()
