from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from scopex.api.service import (
    TaskBusyError,
    TaskConflictError,
    TaskNotFoundError,
    TaskService,
)


MAX_BODY = 64 * 1024


def _loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RuntimeApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, service: TaskService):
        host, _port = address
        if not _loopback_host(host):
            raise ValueError("Runtime API may bind only to loopback")
        self.service = service
        super().__init__(address, RuntimeApiHandler)


class RuntimeApiHandler(BaseHTTPRequestHandler):
    server: RuntimeApiServer

    def log_message(self, *_):
        pass

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("transfer encoding is not accepted")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if not 0 < length <= MAX_BODY:
            raise ValueError("JSON body must be between 1 and 65536 bytes")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("short request body")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        value = json.loads(raw, object_pairs_hook=pairs)
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    @staticmethod
    def _message(body: dict[str, Any], *, required: bool = True) -> str:
        value = body.get("message", "")
        if not isinstance(value, str):
            raise ValueError("message must be a string")
        if required and not value.strip():
            raise ValueError("message is required")
        return value

    def do_GET(self) -> None:
        try:
            parsed = urlsplit(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            if parts == ["health"]:
                self._json(
                    200,
                    {
                        "status": "ok",
                        "active_task_id": self.server.service.active_task_id,
                    },
                )
                return
            if parts == ["tasks"]:
                self._json(200, {"tasks": self.server.service.list_tasks()})
                return
            if len(parts) >= 2 and parts[0] == "tasks":
                task_id = parts[1]
                if len(parts) == 2:
                    self._json(200, self.server.service.get_task(task_id))
                    return
                if len(parts) == 3 and parts[2] == "events":
                    query = parse_qs(parsed.query, keep_blank_values=True)
                    raw_after = query.get("after", ["0"])[-1]
                    try:
                        after = int(raw_after)
                    except ValueError as exc:
                        raise ValueError("after must be an integer") from exc
                    events = self.server.service.get_events(task_id, after=after)
                    next_after = max(
                        [after]
                        + [row["seq"] for row in events if isinstance(row.get("seq"), int)]
                    )
                    self._json(
                        200,
                        {
                            "task_id": task_id,
                            "after": after,
                            "next_after": next_after,
                            "events": events,
                        },
                    )
                    return
                if len(parts) == 3 and parts[2] == "evidence":
                    self._json(200, self.server.service.get_evidence(task_id))
                    return
                if len(parts) == 3 and parts[2] == "result":
                    self._json(200, self.server.service.get_result(task_id))
                    return
            self._error(404, "route_not_found", "route not found")
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:
        try:
            parsed = urlsplit(self.path)
            if parsed.query:
                raise ValueError("POST routes do not accept query parameters")
            parts = [part for part in parsed.path.split("/") if part]
            body = self._body()
            if parts == ["tasks"]:
                task = self.server.service.create_task(self._message(body))
                self._json(202, task)
                return
            if len(parts) == 3 and parts[0] == "tasks":
                task_id, action = parts[1], parts[2]
                if action == "stop":
                    task = self.server.service.stop(
                        task_id,
                        self._message(body, required=False),
                    )
                    self._json(202, task)
                    return
                if action == "resume":
                    task = self.server.service.resume(task_id, self._message(body))
                    self._json(202, task)
                    return
                if action == "steer":
                    task = self.server.service.steer(task_id, self._message(body))
                    self._json(202, task)
                    return
            self._error(404, "route_not_found", "route not found")
        except Exception as exc:
            self._handle_exception(exc)

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, TaskNotFoundError):
            self._error(404, "task_not_found", str(exc.args[0]))
            return
        if isinstance(exc, TaskBusyError):
            self._error(409, "task_busy", str(exc))
            return
        if isinstance(exc, TaskConflictError):
            self._error(409, "task_conflict", str(exc))
            return
        if isinstance(exc, (ValueError, json.JSONDecodeError)):
            self._error(400, "invalid_request", str(exc))
            return
        self._error(500, "internal_error", type(exc).__name__)
