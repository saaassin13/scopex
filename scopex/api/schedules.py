from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time
import uuid
from typing import Any

from scopex.api.service import TaskBusyError, TaskService


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


class ScheduleNotFoundError(KeyError):
    pass


class ScheduleService:
    """Tiny time trigger layer that creates ordinary ScopeX tasks.

    This is intentionally not a workflow engine. A schedule stores one normal
    task message plus a simple clock rule. When due, it calls TaskService and
    the resulting run follows the exact same OpenClaw/Skill/Audit path as a
    manual task.
    """

    def __init__(self, root: Path, tasks: TaskService, *, poll_s: float = 2.0) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "schedules.json"
        self.runs_path = self.root / "schedule-runs.jsonl"
        self.tasks = tasks
        self.poll_s = max(0.5, float(poll_s))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._items: dict[str, dict[str, Any]] = {}
        self._load()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="scopex-scheduler", daemon=True)
            self._thread.start()

    def shutdown(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_s))

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [dict(value) for value in self._items.values()]
        rows.sort(key=lambda row: str(row.get("created_at", "")))
        return rows

    def get(self, schedule_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._items.get(schedule_id)
            if row is None:
                raise ScheduleNotFoundError(schedule_id)
            return dict(row)

    def create(
        self,
        *,
        name: str,
        message: str,
        kind: str,
        interval_minutes: int | None = None,
        daily_time: str | None = None,
        run_at: str | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        name = name.strip()
        message = message.strip()
        if not name:
            raise ValueError("schedule name is required")
        if not message:
            raise ValueError("schedule message is required")
        if len(name) > 120 or len(message) > 32768:
            raise ValueError("schedule name or message is too long")
        rule = self._normalize_rule(kind, interval_minutes, daily_time, run_at)
        created = now_local()
        row: dict[str, Any] = {
            "id": "schedule-" + uuid.uuid4().hex[:10],
            "name": name,
            "message": message,
            "kind": kind,
            **rule,
            "enabled": bool(enabled),
            "created_at": iso(created),
            "updated_at": iso(created),
            "next_run_at": iso(self._next_after(created, kind, rule)) if enabled else None,
            "last_run_at": None,
            "last_status": None,
            "last_task_id": None,
        }
        with self._lock:
            self._items[row["id"]] = row
            self._persist_locked()
        return dict(row)

    def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            row = self._items.get(schedule_id)
            if row is None:
                raise ScheduleNotFoundError(schedule_id)
            row["enabled"] = bool(enabled)
            row["updated_at"] = iso(now_local())
            if enabled and not row.get("next_run_at"):
                row["next_run_at"] = iso(self._next_after(now_local(), row["kind"], row))
            if not enabled:
                row["next_run_at"] = None
            self._persist_locked()
            return dict(row)

    def delete(self, schedule_id: str) -> None:
        with self._lock:
            if schedule_id not in self._items:
                raise ScheduleNotFoundError(schedule_id)
            del self._items[schedule_id]
            self._persist_locked()

    def run_now(self, schedule_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._items.get(schedule_id)
            if row is None:
                raise ScheduleNotFoundError(schedule_id)
            planned = iso(now_local())
        return self._trigger(schedule_id, planned)

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_s):
            self._tick()

    def _tick(self) -> None:
        current = now_local()
        due: list[tuple[str, str]] = []
        with self._lock:
            for schedule_id, row in self._items.items():
                if not row.get("enabled") or not row.get("next_run_at"):
                    continue
                try:
                    due_at = parse_iso(str(row["next_run_at"]))
                except ValueError:
                    continue
                if due_at <= current:
                    due.append((schedule_id, str(row["next_run_at"])))
        for schedule_id, planned in due:
            self._trigger(schedule_id, planned)

    def _trigger(self, schedule_id: str, planned: str) -> dict[str, Any]:
        with self._lock:
            row = self._items.get(schedule_id)
            if row is None:
                raise ScheduleNotFoundError(schedule_id)
            snapshot = dict(row)

        triggered_at = now_local()
        status = "TRIGGERED"
        task_id: str | None = None
        reason: str | None = None
        try:
            task = self.tasks.create_task(
                snapshot["message"],
                mode="task",
                trigger_type="schedule",
                schedule_id=schedule_id,
                scheduled_for=planned,
            )
            task_id = task["id"]
        except TaskBusyError as exc:
            status = "SKIPPED_BUSY"
            reason = str(exc)

        run = {
            "schedule_id": schedule_id,
            "scheduled_for": planned,
            "triggered_at": iso(triggered_at),
            "status": status,
            "task_id": task_id,
            "reason": reason,
        }
        self._append_run(run)

        with self._lock:
            row = self._items.get(schedule_id)
            if row is not None:
                row["last_run_at"] = run["triggered_at"]
                row["last_status"] = status
                row["last_task_id"] = task_id
                if row["kind"] == "once":
                    row["enabled"] = False
                    row["next_run_at"] = None
                else:
                    base = parse_iso(planned)
                    next_run = self._next_after(base, row["kind"], row)
                    while next_run <= triggered_at:
                        next_run = self._next_after(next_run, row["kind"], row)
                    row["next_run_at"] = iso(next_run)
                row["updated_at"] = iso(triggered_at)
                self._persist_locked()
        return run

    def _normalize_rule(
        self,
        kind: str,
        interval_minutes: int | None,
        daily_time: str | None,
        run_at: str | None,
    ) -> dict[str, Any]:
        if kind == "interval":
            value = int(interval_minutes or 0)
            if value < 1 or value > 10080:
                raise ValueError("interval_minutes must be between 1 and 10080")
            return {"interval_minutes": value, "daily_time": None, "run_at": None}
        if kind == "daily":
            text = (daily_time or "").strip()
            try:
                hour_text, minute_text = text.split(":", 1)
                hour, minute = int(hour_text), int(minute_text)
            except (ValueError, AttributeError):
                raise ValueError("daily_time must use HH:MM") from None
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("daily_time must use a valid HH:MM")
            return {"interval_minutes": None, "daily_time": f"{hour:02d}:{minute:02d}", "run_at": None}
        if kind == "once":
            if not run_at:
                raise ValueError("run_at is required for once schedule")
            target = parse_iso(run_at)
            if target <= now_local():
                raise ValueError("run_at must be in the future")
            return {"interval_minutes": None, "daily_time": None, "run_at": iso(target)}
        raise ValueError("kind must be interval, daily or once")

    def _next_after(self, base: datetime, kind: str, row: dict[str, Any]) -> datetime:
        if kind == "interval":
            return base + timedelta(minutes=int(row["interval_minutes"]))
        if kind == "daily":
            hour, minute = (int(value) for value in str(row["daily_time"]).split(":", 1))
            candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= base:
                candidate += timedelta(days=1)
            return candidate
        if kind == "once":
            return parse_iso(str(row["run_at"]))
        raise ValueError("unsupported schedule kind")

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(value, list):
            return
        for row in value:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                self._items[row["id"]] = dict(row)

    def _persist_locked(self) -> None:
        temp = self.path.with_suffix(".tmp")
        rows = [dict(value) for value in self._items.values()]
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def _append_run(self, row: dict[str, Any]) -> None:
        with self.runs_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
