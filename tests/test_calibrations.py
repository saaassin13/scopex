from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest

from scopex.api.calibrations import CalibrationError, CalibrationService, validate_config

# 1x1 red JPEG (SOI + APP0 + minimal) and a real header check via PNG magic.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def sample_config(frame=(1920, 1080)):
    return {
        "fps": 2.0,
        "min_area_ratio": 0.02,
        "warmup_frames": 50,
        "trigger_sustain_s": 30.0,
        "clear_sustain_s": 10.0,
        "merge_distance_px": 300.0,
        "frame_size": list(frame),
        "zones": {
            "carousel": [[100, 100], [1500, 100], [1500, 700], [100, 700]],
            "forbidden_zones": [[[100, 720], [1500, 720], [1500, 1000], [100, 1000]]],
            "platform_line": None,
            "min_on_carousel_ratio": 0.5,
            "platform_line_margin_px": 8.0,
            "min_conf": 0.5,
        },
    }


class ValidateConfigTests(unittest.TestCase):
    def test_valid_config_passes_without_warnings(self):
        errors, warnings = validate_config(sample_config())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_explicit_zones_warns(self):
        config = sample_config()
        config["zones"]["forbidden_zones"] = []
        errors, warnings = validate_config(config)
        self.assertEqual(errors, [])
        self.assertTrue(any("没有其它运动结构" in warning for warning in warnings))

    def test_forbidden_zone_overlapping_carousel_warns(self):
        config = sample_config()
        config["zones"]["forbidden_zones"] = [[[100, 100], [1500, 100], [1500, 700], [100, 700]]]
        _, warnings = validate_config(config)
        self.assertTrue(any("转盘多边形内" in warning for warning in warnings))

    def test_degenerate_and_out_of_frame_polygons_are_errors(self):
        config = sample_config()
        config["zones"]["carousel"] = [[10, 10], [20, 20]]
        errors, _ = validate_config(config)
        self.assertTrue(any("转盘多边形" in message for message in errors))
        config = sample_config()
        config["zones"]["carousel"] = [[-500, 0], [3000, 0], [1500, 800], [100, 800]]
        errors, _ = validate_config(config)
        self.assertTrue(any("超出画面" in message for message in errors))


class CalibrationServiceTests(unittest.TestCase):
    def test_version_lifecycle_save_activate_verify(self):
        with tempfile.TemporaryDirectory() as td:
            service = CalibrationService(Path(td))
            result = service.save_version("camera-96", sample_config(), "初版", None)
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["active_version"], 1)
            self.assertIn("scan_foreground", result["verify"]["command"])

            second = service.save_version("camera-96", sample_config(), "缩小禁区", None)
            self.assertEqual(second["version"], 2)
            # New versions do not steal the active flag from a calibrated one.
            self.assertEqual(second["active_version"], 1)

            listing = service.list_cameras()
            self.assertEqual(listing[0]["active_version"], 1)
            self.assertEqual(len(listing[0]["versions"]), 2)
            self.assertEqual(listing[0]["versions"][0]["verify"]["status"], "pending")

            service.activate("camera-96", 2)
            self.assertEqual(service.camera_detail("camera-96")["active_version"], 2)

            verified = service.record_verify("camera-96", 2, "failed", "静止片段 40s 出现 1 次占用")
            self.assertEqual(verified["verify"]["status"], "failed")
            self.assertIn("静止片段", service.camera_detail("camera-96")["versions"][1]["verify"]["note"])

            saved = json.loads((Path(td) / "camera-96" / "zones-v1.json").read_text())
            self.assertEqual(saved["zones"]["carousel"][0], [100, 100])

    def test_invalid_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            service = CalibrationService(Path(td))
            config = sample_config()
            config["zones"]["carousel"] = [[0, 0], [1, 1]]
            with self.assertRaises(CalibrationError):
                service.save_version("camera-96", config, "", None)
            self.assertEqual(service.list_cameras(), [])

    def test_camera_name_and_frame_validation(self):
        with tempfile.TemporaryDirectory() as td:
            service = CalibrationService(Path(td))
            with self.assertRaises(CalibrationError):
                service.save_version("../escape", sample_config(), "", None)
            with self.assertRaises(CalibrationError):
                service.save_frame("camera-96", base64.b64encode(b"not an image").decode())
            frame_id = service.save_frame(
                "camera-96", base64.b64encode(_PNG_MAGIC + b"payload").decode())
            self.assertTrue(service.frame_path("camera-96", frame_id).is_file())
            with self.assertRaises(CalibrationError):
                service.frame_path("camera-96", "../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
