from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import time
import uuid

from monitor.cow_fall.zones import point_in_polygon

_CAMERA_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_FRAME_RE = re.compile(r"^[a-f0-9]{8,12}$")
_MAX_FRAME_BYTES = 8 * 1024 * 1024


class CalibrationError(ValueError):
    pass


def _polygon_self_check(polygon: list[list[float]]) -> str | None:
    """Cheap sanity: ordered, finite coordinates, non-degenerate extent."""
    if len(polygon) < 3:
        return "多边形至少需要 3 个顶点"
    for point in polygon:
        if len(point) != 2:
            return "顶点必须是 [x, y]"
        x, y = point
        if not (isinstance(x, (int, float)) and isinstance(y, (int, float))):
            return "顶点坐标必须是数字"
        if x != x or y != y:  # NaN
            return "顶点坐标不能为 NaN"
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    if max(xs) - min(xs) < 2 or max(ys) - min(ys) < 2:
        return "多边形退化（几乎是一条线）"
    return None


def _polygon_area(polygon: list[list[float]]) -> float:
    total = 0.0
    count = len(polygon)
    for i in range(count):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % count]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _overlap_ratio(inner: list[list[float]], outer: list[list[float]],
                   samples: int = 16) -> float:
    """Fraction of sampled points of `inner` that fall inside `outer`."""
    xs = [p[0] for p in inner]
    ys = [p[1] for p in inner]
    hits = 0
    total = 0
    for row in range(1, samples + 1):
        y = min(ys) + (max(ys) - min(ys)) * row / (samples + 1)
        for col in range(1, samples + 1):
            x = min(xs) + (max(xs) - min(xs)) * col / (samples + 1)
            if point_in_polygon((x, y), inner):
                total += 1
                if point_in_polygon((x, y), outer):
                    hits += 1
    return hits / total if total else 0.0


def validate_config(config: dict) -> tuple[list[str], list[str]]:
    """Structural validation shared by the API and tests.

    Returns (errors, warnings): errors reject the save, warnings are recorded
    with the version and shown in the UI.
    """
    errors: list[str] = []
    warnings: list[str] = []
    zones = config.get("zones")
    if not isinstance(zones, dict):
        return (["缺少 zones 配置"], warnings)
    frame_size = config.get("frame_size")
    if (not isinstance(frame_size, list) or len(frame_size) != 2
            or not all(isinstance(v, int) and v > 0 for v in frame_size)):
        errors.append("frame_size 必须是 [宽, 高] 的正整数")
        width = height = None
    else:
        width, height = frame_size

    carousel = zones.get("carousel")
    if not isinstance(carousel, list):
        errors.append("缺少转盘多边形 carousel")
    else:
        problem = _polygon_self_check(carousel)
        if problem:
            errors.append(f"转盘多边形：{problem}")
        if width is not None:
            outside = [p for p in carousel
                       if p[0] < -10 or p[1] < -10 or p[0] > width + 10 or p[1] > height + 10]
            if outside:
                errors.append("转盘多边形顶点超出画面范围")

    forbidden = zones.get("forbidden_zones")
    if forbidden in (None, []):
        warnings.append("未设置显式禁区：将监视转盘外全部区域，仅适用于画面内没有其它运动结构的机位")
    elif isinstance(forbidden, list):
        if width is not None:
            for index, zone in enumerate(forbidden):
                problem = _polygon_self_check(zone)
                if problem:
                    errors.append(f"禁区 {index + 1}：{problem}")
                    continue
                overlap = _overlap_ratio(zone, carousel)
                if overlap > 0.10:
                    warnings.append(f"禁区 {index + 1} 有 {round(overlap * 100)}% 落在转盘多边形内，请确认")
        if width is not None and isinstance(forbidden, list) and forbidden:
            area = sum(_polygon_area(zone) for zone in forbidden
                       if isinstance(zone, list) and len(zone) >= 3)
            ratio = area / (width * height)
            if ratio < 0.005:
                warnings.append("禁区面积不足画面的 0.5%，可能太小")
            elif ratio > 0.60:
                warnings.append("禁区面积超过画面的 60%，误报风险高，建议缩小")

    line = zones.get("platform_line")
    if line is not None:
        if (not isinstance(line, list) or len(line) != 2
                or _polygon_self_check([line[0], line[1], [line[0][0], line[1][1]]])):
            errors.append("平台线必须是两个不同的端点")
    for key, low, high in (("min_on_carousel_ratio", 0.0, 1.0),
                           ("min_conf", 0.0, 1.0)):
        value = zones.get(key, 0.5)
        if not isinstance(value, (int, float)) or not low < value <= high:
            errors.append(f"{key} 必须在 ({low}, {high}] 内")
    margin = zones.get("platform_line_margin_px", 8.0)
    if not isinstance(margin, (int, float)) or margin < 0:
        errors.append("platform_line_margin_px 不能为负")
    area_ratio = config.get("min_area_ratio", 0.02)
    if not isinstance(area_ratio, (int, float)) or not 0.0 < area_ratio < 1.0:
        errors.append("min_area_ratio 必须在 (0, 1) 内")
    sustain = config.get("trigger_sustain_s", 30.0)
    if not isinstance(sustain, (int, float)) or sustain <= 0:
        errors.append("trigger_sustain_s 必须为正数")
    return errors, warnings


class CalibrationService:
    """Versioned zone-calibration store under one root directory.

    Layout: <root>/<camera>/zones-vN.json + frames/ + meta.json. The saved
    file is a complete monitor config (same shape as
    docs/monitoring/zones-camera-96-v1.json), directly usable by the
    foreground scan and the monitoring session.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _camera_dir(self, camera: str) -> Path:
        if not isinstance(camera, str) or not _CAMERA_RE.fullmatch(camera):
            raise CalibrationError("相机标识只能是 1–32 位小写字母、数字、- 或 _")
        return self.root / camera

    def list_cameras(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        result = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir():
                continue
            meta = self._read_meta(directory.name)
            if meta is None:
                continue
            result.append({"camera": directory.name,
                           "active_version": meta.get("active"),
                           "versions": meta.get("versions", [])})
        return result

    def camera_detail(self, camera: str) -> dict:
        directory = self._camera_dir(camera)
        meta = self._read_meta(camera)
        if meta is None:
            raise CalibrationError(f"没有相机 {camera} 的标定记录")
        return {"camera": camera, "active_version": meta.get("active"),
                "versions": meta.get("versions", [])}

    def version_config(self, camera: str, version: int) -> dict:
        """The saved zones config for one version, to load into the editor."""
        meta = self._read_meta(camera)
        if meta is None:
            raise CalibrationError(f"没有相机 {camera} 的标定记录")
        if not any(item.get("version") == version for item in meta.get("versions", [])):
            raise CalibrationError(f"版本 v{version} 不存在")
        path = self._camera_dir(camera) / f"zones-v{version}.json"
        if not path.is_file():
            raise CalibrationError(f"版本 v{version} 的配置文件缺失")
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_meta(self, camera: str) -> dict | None:
        path = self._camera_dir(camera) / "meta.json"
        if not path.is_file():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return meta if isinstance(meta, dict) else None

    def _write_meta(self, camera: str, meta: dict) -> None:
        directory = self._camera_dir(camera)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "meta.json"
        temporary = directory / ".meta.json.tmp"
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(path)

    def save_frame(self, camera: str, image_b64: str) -> str:
        directory = self._camera_dir(camera) / "frames"
        try:
            data = base64.b64decode(image_b64, validate=True)
        except Exception as exc:
            raise CalibrationError("参考帧不是有效的 base64 数据") from exc
        if len(data) > _MAX_FRAME_BYTES:
            raise CalibrationError("参考帧超过 8 MB")
        if not (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"):
            raise CalibrationError("参考帧必须是 JPEG 或 PNG 图片")
        frame_id = uuid.uuid4().hex[:10]
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if data[:4] == b"\x89PNG" else ".jpg"
        (directory / f"{frame_id}{suffix}").write_bytes(data)
        return frame_id

    def frame_path(self, camera: str, frame_id: str) -> Path:
        if not isinstance(frame_id, str) or not _FRAME_RE.fullmatch(frame_id):
            raise CalibrationError("无效的帧编号")
        directory = self._camera_dir(camera) / "frames"
        for candidate in directory.glob(f"{frame_id}.*"):
            return candidate
        raise CalibrationError("找不到该参考帧")

    def save_version(self, camera: str, config: dict, note: str,
                     frame_id: str | None) -> dict:
        if not isinstance(config, dict):
            raise CalibrationError("config 必须是对象")
        errors, warnings = validate_config(config)
        if errors:
            raise CalibrationError("；".join(errors))
        meta = self._read_meta(camera) or {"active": None, "versions": []}
        used = {item.get("version") for item in meta["versions"]}
        version = 1
        while version in used:
            version += 1
        directory = self._camera_dir(camera)
        directory.mkdir(parents=True, exist_ok=True)
        config_path = directory / f"zones-v{version}.json"
        temporary = directory / f".zones-v{version}.tmp"
        temporary.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
        temporary.replace(config_path)
        entry = {"version": version,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "note": str(note or "")[:200],
                 "frame_id": frame_id,
                 "warnings": warnings,
                 "verify": {"status": "pending", "note": "",
                            "command": ("python3 scripts/scan_foreground.py <静止场景录像> "
                                        f"--config {config_path}")}}
        meta["versions"].append(entry)
        if meta.get("active") is None:
            meta["active"] = version
        self._write_meta(camera, meta)
        return {"camera": camera, "version": version, "warnings": warnings,
                "verify": entry["verify"], "active_version": meta["active"]}

    def activate(self, camera: str, version: int) -> dict:
        meta = self._read_meta(camera)
        if meta is None:
            raise CalibrationError(f"没有相机 {camera} 的标定记录")
        if not any(item.get("version") == version for item in meta["versions"]):
            raise CalibrationError(f"版本 v{version} 不存在")
        meta["active"] = version
        self._write_meta(camera, meta)
        return {"camera": camera, "active_version": version}

    def record_verify(self, camera: str, version: int, result: str,
                      note: str) -> dict:
        if result not in ("pending", "passed", "failed"):
            raise CalibrationError("验证结果只能是 pending、passed 或 failed")
        meta = self._read_meta(camera)
        if meta is None:
            raise CalibrationError(f"没有相机 {camera} 的标定记录")
        for item in meta["versions"]:
            if item.get("version") == version:
                item["verify"] = {"status": result, "note": str(note or "")[:200],
                                  "command": item.get("verify", {}).get("command", "")}
                self._write_meta(camera, meta)
                return {"camera": camera, "version": version, "verify": item["verify"]}
        raise CalibrationError(f"版本 v{version} 不存在")
