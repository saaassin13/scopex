from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
from typing import Any


class AuditStore:
    """Filesystem audit store for one-task-per-directory local operation."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
            raise ValueError("invalid task id")
        path = self.root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, task_id: str, name: str, value: Any) -> Path:
        if "/" in name or "\\" in name or not name.endswith(".json"):
            raise ValueError("audit JSON name must be a simple .json filename")
        target = self.task_dir(task_id) / name
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self._atomic_write(target, data)
        return target

    def write_text(self, task_id: str, name: str, value: str) -> Path:
        if "/" in name or "\\" in name:
            raise ValueError("audit text name must be a simple filename")
        target = self.task_dir(task_id) / name
        self._atomic_write(target, value)
        return target

    def append_jsonl(self, task_id: str, name: str, value: Any) -> Path:
        if "/" in name or "\\" in name or not name.endswith(".jsonl"):
            raise ValueError("audit JSONL name must be a simple .jsonl filename")
        target = self.task_dir(task_id) / name
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return target

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".scopex-", dir=target.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise
