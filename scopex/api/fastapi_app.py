from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from scopex.api.service import (
    TaskBusyError,
    TaskConflictError,
    TaskNotFoundError,
    TaskService,
)


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=32768)


class OptionalMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=32768)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    service: TaskService,
    *,
    static_dir: Path | None = None,
    shutdown_timeout_s: float = 310.0,
) -> FastAPI:
    """Build the thin FastAPI transport over the proven TaskService boundary."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        service.shutdown(timeout_s=shutdown_timeout_s)

    app = FastAPI(
        title="ScopeX Runtime API",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found(_request: Request, exc: TaskNotFoundError):
        return _error(404, "task_not_found", str(exc.args[0]))

    @app.exception_handler(TaskBusyError)
    async def task_busy(_request: Request, exc: TaskBusyError):
        return _error(409, "task_busy", str(exc))

    @app.exception_handler(TaskConflictError)
    async def task_conflict(_request: Request, exc: TaskConflictError):
        return _error(409, "task_conflict", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(value) for value in first.get("loc", ()))
        message = str(first.get("msg", "invalid request"))
        if location:
            message = f"{location}: {message}"
        return _error(400, "invalid_request", message)

    @app.exception_handler(ValueError)
    async def invalid_value(_request: Request, exc: ValueError):
        return _error(400, "invalid_request", str(exc))

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "active_task_id": service.active_task_id}

    @app.get("/tasks")
    def list_tasks() -> dict[str, Any]:
        return {"tasks": service.list_tasks()}

    @app.post("/tasks", status_code=202)
    def create_task(body: MessageRequest) -> dict[str, Any]:
        return service.create_task(body.message)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        return service.get_task(task_id)

    @app.get("/tasks/{task_id}/events")
    def get_events(
        task_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        events = service.get_events(task_id, after=after)
        next_after = max(
            [after]
            + [row["seq"] for row in events if isinstance(row.get("seq"), int)]
        )
        return {
            "task_id": task_id,
            "after": after,
            "next_after": next_after,
            "events": events,
        }

    @app.get("/tasks/{task_id}/evidence")
    def get_evidence(task_id: str) -> dict[str, Any]:
        return service.get_evidence(task_id)

    @app.get("/tasks/{task_id}/result")
    def get_result(task_id: str) -> dict[str, Any]:
        return service.get_result(task_id)

    @app.post("/tasks/{task_id}/stop", status_code=202)
    def stop(task_id: str, body: OptionalMessageRequest) -> dict[str, Any]:
        return service.stop(task_id, body.message)

    @app.post("/tasks/{task_id}/resume", status_code=202)
    def resume(task_id: str, body: MessageRequest) -> dict[str, Any]:
        return service.resume(task_id, body.message)

    @app.post("/tasks/{task_id}/steer", status_code=202)
    def steer(task_id: str, body: MessageRequest) -> dict[str, Any]:
        return service.steer(task_id, body.message)

    resolved_static = Path(static_dir).resolve() if static_dir is not None else None
    if resolved_static is not None and resolved_static.is_dir():
        app.mount("/", StaticFiles(directory=resolved_static, html=True), name="web")
    else:
        @app.get("/")
        def root() -> dict[str, str]:
            return {"service": "scopex-runtime-api", "ui": "not-built"}

    return app
