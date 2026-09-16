# Local Runtime API

Current documentation sync: 2026-09-16, code baseline `main@cb90d02`. The product is FastAPI + Uvicorn over the existing OpenClaw runtime. The user has confirmed edge deployment and generally normal overnight operation; this is not a separate acceptance result for every endpoint or workload.

## Product boundary

```text
Vue / client -> FastAPI -> TaskService admission
 -> independent OpenClaw runtime + model + skills
 -> native CLI outcome -> persisted text/status/sources/audit
```

FastAPI owns transport/validation/error mapping/static serving, not diagnosis or a second agent loop. `scopex/api/http.py` and legacy report paths remain regression/compatibility references, not the default product entrypoint.

Use [deployment.md](../deployment.md) and `deploy/edge/compose.yaml` for current deployment. Do not mix historical host/systemd startup commands with edge Compose.

Current constraints:

- One API process owns one data-root, protected by a file lock. Do not share the in-memory admission queue across Uvicorn workers.
- Product CLI defaults: 2 active tasks, 16 queued tasks, 600-second queue timeout. Active slots are configurable from 1 to 4. Direct TaskService construction retains legacy defaults of one slot/no queue.
- Paused tasks keep their logical slot. Queued tasks have no Runtime/Sandbox/host snapshot until admission.
- Default bind is loopback. An explicit IP with `--allow-trusted-network-bind` is supported; edge Compose supplies the trusted VPN IP. This is not application authentication or permission to expose the API publicly.
- ScheduleService is present; no database, Redis or broker is introduced.
- Events use incremental polling, not SSE/WebSocket. Completed history is readable after restart, but unfinished execution is not automatically resumed/replayed.
- Follow-up and cross-Run conversation recovery redesign remains deferred; compatibility interfaces do not establish full conversational product support.

Pinned transport dependencies remain in `requirements-api.txt`. Runtime image creation installs them at build time; do not install packages through an Agent task.

## Main endpoints

```http
GET /health
POST /runs
Content-Type: application/json

{"message":"分析指定时间窗的编码器运动与数据异常"}
```

`POST /runs` returns 202 with an auto-mode task snapshot and uses the same runtime for conversation and business requests. Native publication does not require a fixed ScopeX business JSON schema or a second report model.

```http
GET /activity
GET /tasks
GET /tasks/{task_id}
GET /tasks/{task_id}/events?after=0
GET /tasks/{task_id}/evidence
GET /tasks/{task_id}/result
```

`/activity` is global live metadata, not filtered by the selected calendar date. `/tasks` accepts mode/day and schedule_id/limit/offset; limit is 1..200 when supplied. Filtering precedes pagination. Existing file enumeration remains; response pagination is not a disk index. Consult generated OpenAPI for full parameter shapes.

## Controls

```http
POST /tasks/{task_id}/stop
{"message":"先暂停"}
POST /tasks/{task_id}/resume
{"message":"继续"}
POST /tasks/{task_id}/steer
{"message":"缩小调查范围"}
POST /tasks/{task_id}/cancel-queued
```

Control compatibility is retained. Stop acts at the safe model-request boundary, not an immediate kill of an already-running tool. Cancel-queued applies only before execution. State conflicts return 409; stopping/resuming one task must not affect another task's Runtime.

Explicit `/tasks`, `/conversations`, and `/conversations/{task_id}/messages` routes remain available for compatibility. They do not introduce a separate engine or change the deferred follow-up scope.

## Native result contract

The product factory enables `native_answers=True`. TaskService selects `finish_native_answer()` before the historical budget-to-finalizer branch.

```text
normal native execution + complete visible answer
 -> COMPLETED, execution_status=completed, report_meta.status=complete

budget/runtime guard/native error/length/process failure
 -> FAILED, execution_status=incomplete
 -> partial if valid native visible text exists, otherwise unavailable
```

Existing Evidence or non-empty text cannot erase native execution failures. Framework error notices are not treated as assistant drafts; an explicit native visible draft is retained. No report model is added on failure. The old TextReportComposer completion rule is not the current product path.

`result.json`: version=2, report_text, report_meta, execution_status, investigation_reasons; report.md/final.txt/report-meta.json are also saved. `postprocess_model_calls=0`; producer is normally openclaw, with scopex_no_data for the verified Locator no-data terminal. Identity-checkable sources do not certify semantic correctness.

Publication remains ordered: once COMPLETED is externally visible, persisted result text/status must already be readable. Cleanup may continue afterward; shutdown waits for known workers within its configured boundary. Restart records queued tasks as expired and other unfinished tasks as interrupted, without executing their old actions.

## Schedule, feedback and data

```http
GET /schedules
POST /schedules
PATCH /schedules/{schedule_id}/enabled
POST /schedules/{schedule_id}/run
DELETE /schedules/{schedule_id}
GET /tasks/{task_id}/evaluation
POST /tasks/{task_id}/evaluation
GET /tasks/{task_id}/export
GET /tasks/{task_id}/data-package
POST /tasks/{task_id}/data-package
GET /tasks/{task_id}/data-package/download
DELETE /tasks/{task_id}
```

Schedules trigger ordinary tasks. Online capacity may queue them; a still-active/queued run from the same schedule prevents accumulation. Offline misses are skipped, not caught up. Schedule history uses `/tasks?schedule_id=...` across dates, including run-now records; the UI route is `/schedules/:id/history`.

Review export is distinct from explicitly collecting original source files. Collection has size/file budgets and records missing/changed/limited data. Terminal deletion removes ScopeX-owned assets only, never external business source files. Feedback does not automatically rewrite prompts/skills.

## Request guards and checks

JSON bodies remain limited to 64KiB; duplicate keys and unexpected request fields are rejected. Validation maps to 400, missing objects/routes to 404, lifecycle/capacity conflicts to 409, oversized bodies to 413. Error bodies remain machine-readable. Generated OpenAPI is available at `/docs` on the configured API address.

CLI compaction defaults to disabled, while current edge Compose explicitly enables it. Both use native OpenClaw mechanisms. Do not change runtime architecture or configuration to match obsolete documentation.

```bash
python3 -m unittest discover -s tests -v
(cd frontend && npm run build)
```

Code baseline CI: [35043132700](https://github.com/saaassin13/scopex/actions/runs/35043132700), 650 Python tests and the existing build/hygiene checks passed. See [current contract](../12-native-answers-and-skill-refinement.md) and [acceptance status](../02-delivery-and-acceptance.md) for the limits of that evidence.

## Optional result labels

See [task result assessment](../13-task-result-assessment.md) for the native-answer footer contract. No business templates, threshold parser or default second model call is introduced. A missing/invalid label does not fail native answer publication.

`POST /runs` accepts optional boolean `assessment_enabled` (default false); schedule creation defaults it to true and `PATCH /schedules/{id}/assessment` updates only future triggers. The task message remains the criterion snapshot. `GET /tasks` accepts `state`, `assessment_status`, `push_decision` and filters before pagination.

`GET /tasks/{id}/assessment` reads metadata. `POST` on the same path requires a terminal task, reuses an existing label where possible, and only with explicit `allow_model=true` may classify bounded saved text once. Duplicate requests are idempotent unless `retry=true`; a pending request is never duplicated. It does not read images or Evidence, rerun the original Agent, change execution state, or send notifications.
