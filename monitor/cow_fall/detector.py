"""YOLO ONNX detector wrapper for cow detection.

Loads a COCO-pretrained YOLOv8-family ONNX model and turns picture frames
into Detection rows. Heavy imports (numpy/onnxruntime) happen lazily so the
pure rule layer and its tests stay dependency-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from monitor.cow_fall.rules import Detection

COCO_COW_CLASS_ID = 19  # official COCO-80: ...horse(17) sheep(18) cow(19)


@dataclass(frozen=True)
class DetectorConfig:
    model_path: Path
    input_size: int = 640
    conf_threshold: float = 0.35
    iou_threshold: float = 0.45
    # Only cows are ever interesting here; other classes are dropped before
    # the rule layer to keep observation lists small.
    class_whitelist: frozenset[int] = frozenset({COCO_COW_CLASS_ID})


def _nms(boxes: list[list[float]], scores: list[float], iou_threshold: float) -> list[int]:
    """Greedy NMS over xyxy boxes; returns kept indices in score order."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    keep: list[int] = []
    while order:
        best = order.pop(0)
        keep.append(best)
        order = [
            i for i in order
            if _iou(boxes[best], boxes[i]) < iou_threshold
        ]
    return keep


def _iou(a: list[float], b: list[float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def letterbox(frame, size: int):
    """Aspect-preserving resize with centered gray padding (ultralytics style).

    Returns (canvas, scale, pad_x, pad_y); reverse with
    (coord - pad) / scale to get back to source pixels.
    """
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    scale = min(size / width, size / height)
    new_w, new_h = round(width * scale), round(height * scale)
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, scale, float(pad_x), float(pad_y)


def decode_detections(output, config: DetectorConfig,
                      source_size: tuple[int, int],
                      letterbox_params: tuple[float, float, float]) -> list[Detection]:
    """Decode a YOLOv8 head output (1, 4+nc, N) into whitelisted detections.

    letterbox_params is (scale, pad_x, pad_y) from the preprocess call.
    """
    import numpy as np

    predictions = np.squeeze(output, axis=0).T  # (N, 4+nc)
    scale, pad_x, pad_y = letterbox_params
    boxes: list[list[float]] = []
    scores: list[float] = []
    for row in predictions:
        class_scores = row[4:]
        class_id = int(np.argmax(class_scores))
        if class_id not in config.class_whitelist:
            continue
        score = float(class_scores[class_id])
        if score < config.conf_threshold:
            continue
        cx, cy, w, h = row[:4]
        boxes.append([
            float((cx - w / 2 - pad_x) / scale),
            float((cy - h / 2 - pad_y) / scale),
            float((cx + w / 2 - pad_x) / scale),
            float((cy + h / 2 - pad_y) / scale),
        ])
        scores.append(score)
    return [
        Detection(box=(boxes[i][0], boxes[i][1], boxes[i][2], boxes[i][3]),
                  conf=scores[i], label="cow")
        for i in _nms(boxes, scores, config.iou_threshold)
    ]


class CowDetector:
    """Thread-safe wrapper around one ONNX session."""

    def __init__(self, config: DetectorConfig) -> None:
        import onnxruntime as ort

        self.config = config
        self._lock = threading.Lock()
        self._session = ort.InferenceSession(
            str(config.model_path),
            providers=["CPUExecutionProvider"],
        )
        self._input_name = self._session.get_inputs()[0].name

    def detect(self, frame_bgr, source_size: tuple[int, int]) -> list[Detection]:
        """frame_bgr: HxWx3 uint8 array in OpenCV BGR order (training order).

        source_size: the frame's (width, height).
        """
        with self._lock:
            inp, params = self._preprocess(frame_bgr)
            outputs = self._session.run(None, {self._input_name: inp})
        return decode_detections(outputs[0], self.config, source_size, params)

    def _preprocess(self, frame_bgr):
        import numpy as np

        canvas, scale, pad_x, pad_y = letterbox(frame_bgr, self.config.input_size)
        # BGR->RGB then HWC->CHW, matching ultralytics DetectionPredictor.
        chw = canvas[..., ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return chw[np.newaxis, ...], (scale, pad_x, pad_y)
