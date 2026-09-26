import unittest
from pathlib import Path

import numpy as np

from monitor.cow_fall.detector import (
    COCO_COW_CLASS_ID,
    DetectorConfig,
    _iou,
    _nms,
    decode_detections,
)

NUM_CLASSES = 80
NUM_PREDICTIONS = 8400


def head_row(cx, cy, w, h, class_id, score):
    """One (4+nc) column for a YOLOv8 head."""
    row = np.zeros(4 + NUM_CLASSES, dtype=np.float32)
    row[:4] = (cx, cy, w, h)
    row[4 + class_id] = score
    return row


def head_output(rows):
    """Assemble (1, 4+nc, N) with the given rows padded to N columns."""
    padded = np.zeros((len(rows), 4 + NUM_CLASSES), dtype=np.float32)
    for i, row in enumerate(rows):
        padded[i] = row
    return padded.T[np.newaxis, ...]


class NmsTests(unittest.TestCase):
    def test_iou_full_and_none(self):
        a = [0, 0, 10, 10]
        self.assertAlmostEqual(_iou(a, [0, 0, 10, 10]), 1.0)
        self.assertEqual(_iou(a, [20, 20, 30, 30]), 0.0)

    def test_nms_suppresses_overlap_keeps_separate(self):
        boxes = [[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]]
        scores = [0.9, 0.8, 0.7]
        kept = _nms(boxes, scores, iou_threshold=0.45)
        self.assertIn(0, kept)
        self.assertNotIn(1, kept)
        self.assertIn(2, kept)


class DecodeTests(unittest.TestCase):
    config = DetectorConfig(
        model_path=Path("/nonexistent"),  # decode path never touches the file
        conf_threshold=0.35,
        iou_threshold=0.45,
    )

    def test_cow_decoded_with_letterbox_coordinates(self):
        out = head_output([
            head_row(320, 320, 200, 400, COCO_COW_CLASS_ID, 0.9),
        ])
        # Source 1920x1080 -> scale 640/1920 = 1/3, no padding (width-bound).
        detections = decode_detections(out, self.config, (1920, 1080), (1 / 3, 0.0, 54.0))
        self.assertEqual(len(detections), 1)
        det = detections[0]
        self.assertEqual(det.label, "cow")
        self.assertAlmostEqual(det.conf, 0.9, places=5)
        # x: (320 ± 100 - 0)/scale = *3 -> 660..1260; y: (320 ± 200 - 54)*3 -> 198..918
        self.assertAlmostEqual(det.box[0], (320 - 100) * 3, places=3)
        self.assertAlmostEqual(det.box[1], (320 - 200 - 54) * 3, places=3)
        self.assertAlmostEqual(det.box[2], (320 + 100) * 3, places=3)
        self.assertAlmostEqual(det.box[3], (320 + 200 - 54) * 3, places=3)

    def test_other_classes_and_low_confidence_dropped(self):
        out = head_output([
            head_row(100, 100, 50, 50, 0, 0.95),          # person
            head_row(200, 200, 50, 50, COCO_COW_CLASS_ID, 0.2),  # weak cow
        ])
        self.assertEqual(decode_detections(out, self.config, (1920, 1080), (1 / 3, 0.0, 0.0)), [])

    def test_duplicate_cow_suppressed_by_nms(self):
        out = head_output([
            head_row(320, 320, 200, 400, COCO_COW_CLASS_ID, 0.9),
            head_row(324, 320, 200, 400, COCO_COW_CLASS_ID, 0.8),
        ])
        detections = decode_detections(out, self.config, (1920, 1080), (1 / 3, 0.0, 0.0))
        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0].conf, 0.9, places=5)


if __name__ == "__main__":
    unittest.main()
