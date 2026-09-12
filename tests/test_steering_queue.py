import unittest

from scopex.runtime.steering import PendingSteeringQueue


class PendingSteeringQueueTests(unittest.TestCase):
    def test_messages_preserve_order_and_newer_instruction_priority_note(self):
        queue = PendingSteeringQueue()
        first = queue.push("先查 system.log")
        second = queue.push("不要继续 system，改查 robot.log")
        self.assertEqual(first.index, 1)
        self.assertEqual(second.index, 2)
        message = queue.drain_message()
        self.assertIn("1. 先查 system.log", message)
        self.assertIn("2. 不要继续 system，改查 robot.log", message)
        self.assertIn("以较新的指令为准", message)
        self.assertEqual(queue.pending, ())

    def test_clear_discards_stale_steering(self):
        queue = PendingSteeringQueue()
        queue.push("old direction")
        queue.clear()
        self.assertIsNone(queue.drain_message())

    def test_empty_message_is_rejected(self):
        queue = PendingSteeringQueue()
        with self.assertRaises(ValueError):
            queue.push("   ")


if __name__ == "__main__":
    unittest.main()
