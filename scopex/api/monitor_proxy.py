"""Thin proxy from the ScopeX API to the loopback cow-fall daemon.

The daemon (127.0.0.1:8790) lives in the scopex-monitor container which,
like this runtime, runs on the host network - so the proxy is a plain
loopback hop. Routes are `def` (not async): the upstream call blocks and
Starlette runs sync routes in a threadpool, keeping the event loop free.

The daemon does its own path validation; the patterns here mirror
monitor/cow_fall/daemon.py so malformed paths never leave the box.
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request

from fastapi import APIRouter, Request, Response
from fastapi.exceptions import HTTPException

EVENT_ID_RE = re.compile(r"^fall-[A-Za-z0-9_-]+$")
FRAME_NAME_RE = re.compile(r"^(pre|post)-\d{5}\.jpg$")


class MonitorUnavailableError(RuntimeError):
    """The monitor daemon is not reachable (container stopped or absent)."""


def register_monitor_routes(app, base_url: str = "http://127.0.0.1:8790") -> None:
    router = APIRouter(prefix="/monitor", tags=["monitor"])

    def _proxy(method: str, path: str, timeout_s: float = 8.0) -> Response:
        request = urllib.request.Request(base_url + path, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as upstream:
                status, body, headers = upstream.status, upstream.read(), upstream.headers
        except urllib.error.HTTPError as exc:
            status, body, headers = exc.code, exc.read(), exc.headers
        except (urllib.error.URLError, OSError) as exc:
            raise MonitorUnavailableError(str(exc)) from exc
        media_type = headers.get("Content-Type", "application/json; charset=utf-8")
        cache = headers.get("Cache-Control")
        return Response(content=body, status_code=status, media_type=media_type,
                        headers={"Cache-Control": cache} if cache else {})

    @router.get("/status")
    def monitor_status() -> Response:
        return _proxy("GET", "/status", timeout_s=5.0)

    @router.post("/start", status_code=202)
    def monitor_start() -> Response:
        return _proxy("POST", "/start", timeout_s=10.0)

    @router.post("/stop", status_code=202)
    def monitor_stop() -> Response:
        return _proxy("POST", "/stop", timeout_s=10.0)

    @router.get("/events")
    def monitor_events(limit: int = 50) -> Response:
        limit = max(1, min(limit, 500))
        return _proxy("GET", f"/events?limit={limit}")

    @router.post("/events/{event_id}/confirm", status_code=202)
    def monitor_confirm(event_id: str) -> Response:
        if not EVENT_ID_RE.fullmatch(event_id):
            raise HTTPException(status_code=400, detail="invalid event id")
        return _proxy("POST", f"/events/{event_id}/confirm")

    @router.get("/events/{event_id}/frames/{name}")
    def monitor_frame(event_id: str, name: str) -> Response:
        if not EVENT_ID_RE.fullmatch(event_id) or not FRAME_NAME_RE.fullmatch(name):
            raise HTTPException(status_code=400, detail="invalid event or frame name")
        return _proxy("GET", f"/events/{event_id}/frames/{name}", timeout_s=30.0)

    @app.exception_handler(MonitorUnavailableError)
    async def monitor_unavailable(_request: Request, exc: MonitorUnavailableError):
        from scopex.api.fastapi_app import _error

        return _error(503, "monitor_unavailable",
                      "监控服务未运行或不可达；请检查 scopex-monitor 容器状态")

    app.include_router(router)
