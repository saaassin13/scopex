import unittest

from monitor.cow_fall.capture import (
    CaptureConfig,
    FileReplayCapture,
    Frame,
    RingBuffer,
    RtspCapture,
    split_jpeg_stream,
)


class JpegStreamSplitTests(unittest.TestCase):
    def test_splits_complete_and_keeps_partial(self):
        a = b"\xff\xd8" + b"A" * 20 + b"\xff\xd9"
        b = b"\xff\xd8" + b"B" * 10  # incomplete tail
        images, remainder = split_jpeg_stream(a + b)
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].endswith(b"\xff\xd9"))
        self.assertTrue(remainder.startswith(b"\xff\xd8"))

    def test_partial_carries_over(self):
        head = b"\xff\xd8" + b"A" * 5
        _images, partial = split_jpeg_stream(head)
        tail = b"B" * 10 + b"\xff\xd9"
        images, remainder = split_jpeg_stream(tail, partial)
        self.assertEqual(len(images), 1)
        self.assertEqual(remainder, b"")

    def test_empty_chunk_passthrough(self):
        images, remainder = split_jpeg_stream(b"", b"\xff\xd8xx")
        self.assertEqual(images, [])
        self.assertEqual(remainder, b"\xff\xd8xx")


class RingBufferTests(unittest.TestCase):
    def test_window_eviction_and_query(self):
        buffer = RingBuffer(window_s=10.0)
        for index in range(20):
            buffer.add(Frame(timestamp=float(index), data=bytes([index])))
        # Only the newest 10s window survives.
        self.assertEqual(len(buffer), 11)  # t=9..19 inclusive
        frames = buffer.window(12.0, 15.0)
        self.assertEqual([f.timestamp for f in frames], [12.0, 13.0, 14.0, 15.0])

    def test_latest(self):
        buffer = RingBuffer(window_s=10.0)
        self.assertIsNone(buffer.latest())
        buffer.add(Frame(timestamp=1.0, data=b"x"))
        self.assertEqual(buffer.latest().timestamp, 1.0)


class CaptureCommandTests(unittest.TestCase):
    def test_rtsp_command_uses_tcp_transport(self):
        capture = RtspCapture(CaptureConfig(rtsp_url="rtsp://cam/stream"))
        cmd = capture._build_command()
        self.assertIn("-rtsp_transport", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], "rtsp://cam/stream")
        self.assertNotIn("-re", cmd)

    def test_file_replay_reads_at_native_rate(self):
        capture = FileReplayCapture("clip.mkv", fps=2.0)
        cmd = capture._build_command()
        self.assertIn("-re", cmd)
        self.assertNotIn("-rtsp_transport", cmd)
        self.assertEqual(cmd[cmd.index("-i") + 1], "clip.mkv")
        # The fps filter still applies: replay is decimated like a stream.
        self.assertIn("fps=2.0", cmd)


if __name__ == "__main__":
    unittest.main()
