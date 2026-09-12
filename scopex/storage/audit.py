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

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
            raise ValueError("invalid task id")

    @staticmethod
    def _validate_name(name: str, *, suffix: str | None = None) -> None:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("audit filename must be a simple name")
        if suffix is not None and not name.endswith(suffix):
            raise ValueError(f"audit filename must end with {suffix}")

    def task_dir(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        path = self.root / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def existing_task_dir(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        path = self.root / task_id
        if not path.is_dir():
            raise FileNotFoundError(task_id)
        return path

    def list_task_ids(self) -> tuple[str, ...]:
        rows = [path.name for path in self.root.iterdir() if path.is_dir()]
        return tuple(sorted(rows))

    def write_json(self, task_id: str, name: str, value: Any) -> Path:
        self._validate_name(name, suffix=".json")
        target = self.task_dir(task_id) / name
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        self._atomic_write(target, data)
        return target

    def write_text(self, task_id: str, name: str, value: str) -> Path:
        self._validate_name(name)
        target = self.task_dir(task_id) / name
        self._atomic_write(target, value)
        return target

    def append_jsonl(self, task_id: str, name: str, value: Any) -> Path:
        self._validate_name(name, suffix=".jsonl")
        target = self.task_dir(task_id) / name
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return target

    def read_json(self, task_id: str, name: str) -> Any:
        self._validate_name(name, suffix=".json")
        path = self.existing_task_dir(task_id) / name
        return json.loads(path.read_text(encoding="utf-8"))

    def read_text(self, task_id: str, name: str) -> str:
        self._validate_name(name)
        path = self.existing_task_dir(task_id) / name
        return path.read_text(encoding="utf-8")

    def read_jsonl(self, task_id: str, name: str) -> tuple[Any, ...]:
        self._validate_name(name, suffix=".jsonl")
        path = self.existing_task_dir(task_id) / name
        if not path.exists():
            return ()
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return tuple(rows)

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
