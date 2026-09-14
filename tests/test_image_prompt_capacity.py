from __future__ import annotations

import copy
import os
import unittest
from unittest.mock import patch

from scopex.model_capabilities import (
    IMAGE_LIMIT_ENV, count_request_images, render_image_capacity_context,
    resolve_image_limit, validate_request_images,
)


def batches(counts):
    return {"messages": [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,same"}}
            for _ in range(count)
        ]} for count in counts
    ]}


class ImagePromptCapacityTests(unittest.TestCase):
    def test_three_two_image_calls_are_six_not_two(self):
        p = batches([2, 2, 2])
        self.assertEqual(count_request_images(p), 6)
        with self.assertRaisesRegex(ValueError, "image_prompt_capacity_exceeded:6>4"):
            validate_request_images(p, 4)

    def test_six_images_fit_an_explicit_twelve_image_profile(self):
        validate_request_images(batches([2, 2, 2]), 12)

    def test_duplicate_image_urls_still_count(self):
        self.assertEqual(count_request_images(batches([4])), 4)

    def test_plain_paths_are_not_image_attachments(self):
        self.assertEqual(count_request_images({"messages": [{"role": "user", "content": "open a.jpg and b.jpg"}]}), 0)

    def test_user_and_tool_message_images_both_count(self):
        p = batches([2, 2, 2])
        p["messages"][1]["role"] = "tool"
        self.assertEqual(count_request_images(p), 6)

    def test_capacity_check_never_removes_history(self):
        p = batches([2, 2, 2]); before = copy.deepcopy(p)
        with self.assertRaises(ValueError):
            validate_request_images(p, 4)
        self.assertEqual(p, before)

    def test_default_matches_existing_four_image_deployment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_image_limit(), 4)

    def test_profile_and_context_use_same_environment_value(self):
        with patch.dict(os.environ, {IMAGE_LIMIT_ENV: "12"}):
            self.assertEqual(resolve_image_limit(), 12)
            text = render_image_capacity_context()
            self.assertIn("at most 12 image attachments", text)
            self.assertIn("at most 2 originals per view_image call", text)
            self.assertIn("does not reset", text)

    def test_one_image_server_restricts_per_call_too(self):
        with patch.dict(os.environ, {IMAGE_LIMIT_ENV: "1"}):
            self.assertIn("at most 1 originals per view_image call", render_image_capacity_context())

    def test_invalid_profile_fails_before_task_work(self):
        for bad in ("0", "13", "-1", "1.5", "not-an-int"):
            with self.subTest(bad=bad), patch.dict(os.environ, {IMAGE_LIMIT_ENV: bad}):
                with self.assertRaises(ValueError):
                    resolve_image_limit()


if __name__ == "__main__":
    unittest.main()
