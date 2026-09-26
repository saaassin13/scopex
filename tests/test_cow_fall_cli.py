import json
from pathlib import Path
import tempfile
import unittest

from monitor.cow_fall.__main__ import load_session_config


def write_config(root: Path) -> Path:
    config = {
        "fps": 2.0,
        # Non-default values on purpose: asserting against the defaults
        # would pass even if the loader ignored the file entirely.
        "min_area_ratio": 0.03,
        "warmup_frames": 7,
        "trigger_sustain_s": 25.0,
        "clear_sustain_s": 8.0,
        "merge_distance_px": 250.0,
        "model_path": str(root / "model.onnx"),
        "output_root": str(root / "out"),
        "zones": {
            "carousel": [[0, 0], [100, 0], [100, 100], [0, 100]],
            "forbidden_zones": [[[10, 60], [90, 60], [90, 100], [10, 100]]],
            "platform_line": None,
        },
    }
    path = root / "cow-fall.json"
    path.write_text(json.dumps(config))
    return path


class LoadSessionConfigTests(unittest.TestCase):
    def test_reads_zones_and_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_session_config(write_config(root), "rtsp://x")
            self.assertEqual(config.capture.rtsp_url, "rtsp://x")
            self.assertIn("-rtsp_transport", config.capture.extra_ffmpeg_args)
            self.assertEqual(len(config.zones.forbidden_zones), 1)
            self.assertIsNone(config.zones.platform_line)
            self.assertEqual(config.trigger_sustain_s, 25.0)
            self.assertEqual(config.clear_sustain_s, 8.0)
            self.assertEqual(config.merge_distance_px, 250.0)
            self.assertEqual(config.foreground.min_area_ratio, 0.03)
            self.assertEqual(config.foreground.warmup_frames, 7)

    def test_replay_mode_swaps_transport_for_native_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = load_session_config(write_config(root), str(root / "clip.mkv"),
                                         replay=True)
            self.assertEqual(config.capture.extra_ffmpeg_args, ("-re",))


if __name__ == "__main__":
    unittest.main()
