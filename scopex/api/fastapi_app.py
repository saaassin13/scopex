from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from scopex.api.calibrations import CalibrationService
from scopex.api.monitor_proxy import register_monitor_routes
from scopex.api.schedules import ScheduleNotFoundError, ScheduleService
from scopex.api.service import (
    TaskBusyError,
    TaskConflictError,
    TaskNotFoundError,
    TaskService,
)


MAX_BODY = 64 * 1024


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=32768)


class RunRequest(MessageRequest):
    assessment_enabled: StrictBool = False


class AssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_model: StrictBool = False
    retry: StrictBool = False


class AssessmentEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_enabled: StrictBool


class OptionalMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=32768)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    tags: list[str] = Field(default_factory=list, max_length=12)
    note: str = Field(default="", max_length=4000)


class DataPackageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modes: list[Literal["image_evidence", "image_window", "encoder_window", "encoder_source_files", "working_plots"]] = Field(min_length=1, max_length=5)


class CalibrationFrameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_b64: str = Field(min_length=32)


class CalibrationSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]
    note: str = Field(default="", max_length=200)
    frame_id: str | None = None


class CalibrationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    result: Literal["pending", "passed", "failed"]
    note: str = Field(default="", max_length=200)


class ScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=32768)
    kind: Literal["interval", "daily", "once"]
    interval_minutes: int | None = Field(default=None, ge=1, le=10080)
    daily_time: str | None = Field(default=None, max_length=5)
    run_at: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    assessment_enabled: StrictBool = True


class ScheduleEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _strict_object(raw: bytes) -> None:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object")


def create_app(
    service: TaskService,
    *,
    schedules: ScheduleService | None = None,
    calibrations: "CalibrationService | None" = None,
    static_dir: Path | None = None,
    shutdown_timeout_s: float = 310.0,
) -> FastAPI:
    """Build the thin FastAPI transport over TaskService and time triggers."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if schedules is not None:
            schedules.start()
        yield
        if schedules is not None:
            schedules.shutdown()
        service.shutdown(timeout_s=shutdown_timeout_s)

    app = FastAPI(
        title="ScopeX Runtime API",
        version="0.3.0",
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def strict_json_guard(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            raw = await request.body()
            # Calibration frames are base64 images (~MBs); everything else
            # stays under the strict JSON limit.
            limit = 8 * 1024 * 1024 if request.url.path.startswith("/calibrations/") else MAX_BODY
            if len(raw) > limit:
                return _error(413, "request_too_large", f"JSON body exceeds {limit} bytes")
            if raw:
                try:
                    _strict_object(raw)
                except (ValueError, json.JSONDecodeError) as exc:
                    return _error(400, "invalid_request", str(exc))
        return await call_next(request)

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found(_request: Request, exc: TaskNotFoundError):
        return _error(404, "task_not_found", str(exc.args[0]))

    @app.exception_handler(ScheduleNotFoundError)
    async def schedule_not_found(_request: Request, exc: ScheduleNotFoundError):
        return _error(404, "schedule_not_found", str(exc.args[0]))

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

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error(404, "route_not_found", "route not found")
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(_request: Request, exc: Exception):
        return _error(500, "internal_error", type(exc).__name__)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "active_task_id": service.active_task_id}

    # Unified user entry. ScopeX resolves auto -> conversation/task from actual
    # execution behavior; no separate router model is called.
    @app.get("/activity")
    def activity() -> dict[str, Any]:
        return service.activity()

    @app.post("/tasks/{task_id}/cancel-queued", status_code=202)
    def cancel_queued(task_id: str) -> dict[str, Any]:
        return service.cancel_queued(task_id)

    @app.post("/runs", status_code=202)
    def create_run(body: RunRequest) -> dict[str, Any]:
        if body.assessment_enabled:
            return service.create_auto_run(body.message, assessment_enabled=True)
        return service.create_auto_run(body.message)

    @app.get("/tasks")
    def list_tasks(
        mode: Annotated[str | None, Query()] = None,
        day: Annotated[str | None, Query()] = None,
        schedule_id: Annotated[str | None, Query()] = None,
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        state: str | None = None,
        assessment_status: str | None = None,
        push_decision: str | None = None,
        query: Annotated[str | None, Query(max_length=200)] = None,
        trigger_type: Literal["manual", "schedule"] | None = None,
        attention: bool = False,
        include_preview: bool = False,
    ) -> dict[str, Any]:
        filters = {key: value for key, value in {"state": state, "assessment_status": assessment_status, "push_decision": push_decision}.items() if value is not None}
        filters.update({key: value for key, value in {"query": query, "trigger_type": trigger_type}.items() if value is not None})
        if attention:
            filters["attention"] = True
        if include_preview:
            filters["include_preview"] = True
        return {"tasks": service.list_tasks(mode=mode, day=day, schedule_id=schedule_id, limit=limit, offset=offset, **filters)}

    @app.get("/tasks/calendar")
    def task_calendar(month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]) -> dict[str, Any]:
        return service.calendar_month(month)

    # Compatibility/explicit-control endpoints remain for tests and advanced API use.
    @app.post("/tasks", status_code=202)
    def create_task(body: MessageRequest) -> dict[str, Any]:
        return service.create_task(body.message, mode="task", trigger_type="manual")

    @app.post("/conversations", status_code=202)
    def create_conversation(body: MessageRequest) -> dict[str, Any]:
        return service.create_task(body.message, mode="conversation", trigger_type="manual")

    @app.post("/conversations/{task_id}/messages", status_code=202)
    def continue_conversation(task_id: str, body: MessageRequest) -> dict[str, Any]:
        return service.continue_conversation(task_id, body.message)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        return service.get_task(task_id)

    @app.delete("/tasks/{task_id}")
    def delete_task(task_id: str) -> dict[str, Any]:
        return service.delete_task(task_id)

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
        events = service.get_events(task_id, after=after)
        next_after = max([after] + [row["seq"] for row in events if isinstance(row.get("seq"), int)])
        return {"task_id": task_id, "after": after, "next_after": next_after, "events": events}

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

    @app.get("/tasks/{task_id}/assessment")
    def get_assessment(task_id: str) -> dict[str, Any]:
        return service.get_assessment(task_id)

    @app.post("/tasks/{task_id}/assessment", status_code=202)
    def assess(task_id: str, body: AssessmentRequest) -> dict[str, Any]:
        return service.request_assessment(task_id, allow_model=body.allow_model, retry=body.retry)

    @app.get("/tasks/{task_id}/evaluation")
    def get_evaluation(task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "evaluation": service.get_evaluation(task_id)}

    @app.post("/tasks/{task_id}/evaluation")
    def set_evaluation(task_id: str, body: EvaluationRequest) -> dict[str, Any]:
        return service.set_evaluation(task_id, rating=body.rating, tags=body.tags, note=body.note)

    @app.get("/tasks/{task_id}/export")
    def export_task(task_id: str):
        path = service.export_review_bundle(task_id)
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @app.get("/tasks/{task_id}/data-package")
    def get_data_package(task_id: str) -> dict[str, Any]:
        return service.get_data_package(task_id)

    @app.get("/tasks/{task_id}/plots")
    def task_plots(task_id: str) -> dict[str, Any]:
        service.get_task(task_id)
        rows = service.data_packages.preview_plots(task_id)
        return {"plots": [{"index": i, "filename": row["filename"], "candidate_id": row["candidate_id"],
                           "kind": row["kind"]} for i, row in enumerate(rows)]}

    @app.get("/tasks/{task_id}/plots/{index}")
    def task_plot(task_id: str, index: int) -> Response:
        service.get_task(task_id)
        return Response(service.data_packages.preview_plot_bytes(task_id, index), media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/tasks/{task_id}/data-package")
    def build_data_package(task_id: str, body: DataPackageRequest) -> dict[str, Any]:
        return service.build_data_package(task_id, body.modes)

    @app.get("/tasks/{task_id}/data-package/download")
    def download_data_package(task_id: str):
        path = service.data_package_file(task_id)
        return FileResponse(path, media_type="application/zip", filename=f"scopex-data-{task_id}.zip")

    if schedules is not None:
        @app.get("/schedules")
        def list_schedules() -> dict[str, Any]:
            return {"schedules": schedules.list()}

        @app.post("/schedules", status_code=201)
        def create_schedule(body: ScheduleCreateRequest) -> dict[str, Any]:
            return schedules.create(
                name=body.name,
                message=body.message,
                kind=body.kind,
                interval_minutes=body.interval_minutes,
                daily_time=body.daily_time,
                run_at=body.run_at,
                enabled=body.enabled,
                assessment_enabled=body.assessment_enabled,
            )

        @app.patch("/schedules/{schedule_id}/enabled")
        def set_schedule_enabled(schedule_id: str, body: ScheduleEnabledRequest) -> dict[str, Any]:
            return schedules.set_enabled(schedule_id, body.enabled)

        @app.patch("/schedules/{schedule_id}/assessment")
        def set_schedule_assessment(schedule_id: str, body: AssessmentEnabledRequest) -> dict[str, Any]:
            return schedules.set_assessment_enabled(schedule_id, body.assessment_enabled)

        @app.post("/schedules/{schedule_id}/run", status_code=202)
        def run_schedule_now(schedule_id: str) -> dict[str, Any]:
            return schedules.run_now(schedule_id)

        @app.delete("/schedules/{schedule_id}", status_code=204)
        def delete_schedule(schedule_id: str):
            schedules.delete(schedule_id)
            return None

    if calibrations is not None:
        @app.get("/calibrations")
        def list_calibrations() -> dict[str, Any]:
            return {"cameras": calibrations.list_cameras()}

        @app.get("/calibrations/{camera}")
        def calibration_detail(camera: str) -> dict[str, Any]:
            return calibrations.camera_detail(camera)

        @app.get("/calibrations/{camera}/versions/{version}/config")
        def calibration_version_config(camera: str, version: int) -> dict[str, Any]:
            return {"camera": camera, "version": version,
                    "config": calibrations.version_config(camera, version)}

        @app.post("/calibrations/{camera}/frames", status_code=201)
        def upload_calibration_frame(camera: str, body: CalibrationFrameRequest) -> dict[str, Any]:
            return {"frame_id": calibrations.save_frame(camera, body.image_b64)}

        @app.get("/calibrations/{camera}/frames/{frame_id}")
        def calibration_frame(camera: str, frame_id: str):
            return FileResponse(calibrations.frame_path(camera, frame_id))

        @app.post("/calibrations/{camera}", status_code=201)
        def save_calibration(camera: str, body: CalibrationSaveRequest) -> dict[str, Any]:
            return calibrations.save_version(camera, body.config, body.note, body.frame_id)

        @app.post("/calibrations/{camera}/versions/{version}/activate")
        def activate_calibration(camera: str, version: int) -> dict[str, Any]:
            return calibrations.activate(camera, version)

        @app.post("/calibrations/{camera}/versions/{version}/verify")
        def verify_calibration(camera: str, version: int, body: CalibrationVerifyRequest) -> dict[str, Any]:
            return calibrations.record_verify(camera, version, body.result, body.note)

    # Cow-fall monitor proxy is always mounted: when the optional monitor
    # container is absent the proxy answers 503 and the page shows why.
    register_monitor_routes(app)

    resolved_static = Path(static_dir).resolve() if static_dir is not None else None
    if resolved_static is not None and resolved_static.is_dir():
        app.mount("/", StaticFiles(directory=resolved_static, html=True), name="web")
    else:
        @app.get("/")
        def root() -> dict[str, str]:
            return {"service": "scopex-runtime-api", "ui": "not-built"}

    return app
