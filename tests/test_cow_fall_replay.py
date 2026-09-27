"""End-to-end replay validation on a synthetic recording.

Generates a short clip (quiet scene, then a moving object inside the
forbidden zone, then quiet again) and pushes it through the real capture
pipe, foreground trigger, tracker and event archive — the identical code
path used for live RTSP. This is the offline stand-in for stream testing
until credentials are available.
"""
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest

from monitor.cow_fall.capture import CaptureConfig
from monitor.cow_fall.detector import DetectorConfig
from monitor.cow_fall.foreground import ForegroundConfig
from monitor.cow_fall.session import FallSession, SessionConfig
from monitor.cow_fall.tracker import FallTracker
from monitor.cow_fall.zones import ZoneConfig

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - environments without OpenCV
    cv2 = None
    np = None

W, H = 320, 240
SRC_FPS = 25
# Carousel fills the upper half; the forbidden zone is the lower half.
CAROUSEL = ((0.0, 0.0), (float(W), 0.0), (float(W), 120.0), (0.0, 120.0))


def write_synthetic_clip(path: Path) -> None:
    """8s clip: quiet background; a dark block moves through the forbidden
    zone from t=2s to t=6s; quiet again afterwards."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), SRC_FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("VideoWriter failed to open")
    try:
        for index in range(SRC_FPS * 8):
            t = index / SRC_FPS
            frame = np.full((H, W, 3), 100, dtype=np.uint8)
            if 2.0 <= t < 6.0:
                x = int(40 + (t - 2.0) / 4.0 * 180)
                frame[150:210, x:x + 50] = (20, 20, 20)
            writer.write(frame)
    finally:
        writer.release()


class FakeDetector:
    """Stand-in for the ONNX detector: no cow labels in the synthetic scene."""

    def detect(self, frame_bgr, source_size):
        return []


@unittest.skipIf(
    shutil.which("ffmpeg") is None or cv2 is None,
    "ffmpeg and OpenCV required for replay integration test",
)
class FileReplaySessionTests(unittest.TestCase):
    def test_stop_flag_is_instance_local(self):
        """Regression: a class-level Event would let one session's stop()
        silently kill every later session in the process."""
        from monitor.cow_fall.capture import CaptureConfig
        from monitor.cow_fall.detector import DetectorConfig
        config = SessionConfig(
            capture=CaptureConfig(rtsp_url="rtsp://ignored"),
            zones=ZoneConfig(carousel=CAROUSEL),
            detector=DetectorConfig(model_path=Path("/nonexistent.onnx")),
        )
        first = FallSession(config=config, detector=FakeDetector())
        second = FallSession(config=config, detector=FakeDetector())
        first._stop.set()
        self.assertFalse(second._stop.is_set())

    def test_fall_event_fires_and_archives_from_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "synthetic.avi"
            write_synthetic_clip(video)

            fired = []
            session = FallSession(
                config=SessionConfig(
                    capture=CaptureConfig(
                        rtsp_url=str(video), fps=5.0,
                        extra_ffmpeg_args=("-re",),
                    ),
                    zones=ZoneConfig(carousel=CAROUSEL),
                    detector=DetectorConfig(
                        model_path=Path("/nonexistent.onnx")),
                    foreground=ForegroundConfig(warmup_frames=5),
                    event_pre_s=30.0,
                    event_post_s=2.0,
                    poll_interval_s=0.2,
                    output_root=root / "out",
                ),
                detector=FakeDetector(),
                tracker=FallTracker(trigger_sustain_s=1.5,
                                    clear_sustain_s=10.0),
                on_event=lambda event_dir, event: fired.append(
                    (event_dir, event)),
            )
            session.start()
            try:
                # Replay is real-time (-re): the block appears at video
                # t=2s and must trigger after 1.5s of sustain.
                deadline = time.monotonic() + 30.0
                while not fired and time.monotonic() < deadline:
                    time.sleep(0.5)
            finally:
                session.stop()

            self.assertTrue(fired, "no event fired during replay")
            event_dir, _event = fired[0]
            meta = json.loads((event_dir / "meta.json").read_text())
            self.assertEqual(meta["status"], "open")
            # The primary trigger must be identifiable in the archive:
            # zone_foreground, not a piggybacked YOLO rule id.
            self.assertIn("zone_foreground", meta["rules"])
            frames = sorted((event_dir / "frames").glob("*.jpg"))
            self.assertGreater(len(frames), 0)
            # Every archived frame decodes back to the source geometry.
            image = cv2.imread(str(frames[0]))
            self.assertEqual(image.shape[:2], (H, W))

    def test_quiet_replay_fires_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "quiet.avi"
            writer = cv2.VideoWriter(
                str(video), cv2.VideoWriter_fourcc(*"MJPG"), SRC_FPS,
                (W, H))
            for _ in range(SRC_FPS * 4):
                writer.write(np.full((H, W, 3), 100, dtype=np.uint8))
            writer.release()

            fired = []
            session = FallSession(
                config=SessionConfig(
                    capture=CaptureConfig(
                        rtsp_url=str(video), fps=5.0,
                        extra_ffmpeg_args=("-re",),
                    ),
                    zones=ZoneConfig(carousel=CAROUSEL),
                    detector=DetectorConfig(
                        model_path=Path("/nonexistent.onnx")),
                    foreground=ForegroundConfig(warmup_frames=5),
                    event_pre_s=30.0,
                    event_post_s=2.0,
                    poll_interval_s=0.2,
                    output_root=root / "out",
                ),
                detector=FakeDetector(),
                tracker=FallTracker(trigger_sustain_s=1.5),
                on_event=lambda event_dir, event: fired.append(
                    (event_dir, event)),
            )
            session.start()
            try:
                # Clip is 4s; wait past its end plus a margin.
                time.sleep(7.0)
            finally:
                session.stop()
            self.assertEqual(fired, [])


if __name__ == "__main__":
    unittest.main()
