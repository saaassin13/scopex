# Local Runtime API

ScopeX Runtime MVP has passed real OpenClaw + local-vLLM integration and the
line-level stored-trace refinalization. The product boundary is now a
loopback-only **FastAPI + Uvicorn** HTTP service over the frozen runtime core.

## Product boundary

```text
Vue 3 Web UI / local client
        ↓ HTTP on loopback
FastAPI transport
        ↓
TaskService
        ↓
InvestigationCoordinator
        ↓
OpenClaw + local model + sandbox
        ↓
AuditStore
```

FastAPI only owns request validation, status/error mapping, OpenAPI and static
frontend serving. It does not implement diagnosis logic, tool routing, evidence
rules, task lifecycle or model prompts. Route handlers call `TaskService`; they
never call OpenClaw or `InvestigationCoordinator` directly.

`scopex/api/http.py` is retained temporarily as a standard-library regression
reference. The product entrypoint no longer uses it.

Current constraints:

- single user;
- one non-terminal major task at a time;
- a PAUSED task still owns the slot;
- loopback bind only;
- no database, Redis, broker or scheduler;
- events use incremental polling, not SSE/WebSocket yet;
- historical tasks remain readable after restart but are not resumable after the
  ScopeX process is restarted.

## Dependencies

Pinned product transport dependencies live in `requirements-api.txt`:

```text
fastapi==0.141.1
uvicorn==0.52.4
httpx==0.28.1
```

`httpx` is included for FastAPI transport tests. Install once on Spark:

```bash
python3 -m pip install -r requirements-api.txt
```

## Endpoints

### Health

```http
GET /health
```

### Tasks

```http
POST /tasks
Content-Type: application/json

{"message":"分析今天 10:15 左右任务失败"}
```

Returns `202` with the created Task snapshot. Execution continues in a
background task worker.

```http
GET /tasks
GET /tasks/{task_id}
```

### Controls

```http
POST /tasks/{task_id}/stop
{"message":"先暂停"}

POST /tasks/{task_id}/resume
{"message":"继续，优先检查 system.log"}

POST /tasks/{task_id}/steer
{"message":"先别查机器人，先检查 system 侧"}
```

Controls return `202`. Invalid lifecycle operations return `409`.

Stop remains a safe model-request boundary rather than a hard process kill.
Steering keeps the Task RUNNING and starts the pending instruction in the same
OpenClaw session after the interrupted turn unwinds.

### Progress

```http
GET /tasks/{task_id}/events?after=0
```

Response includes `next_after`. The first Vue MVP polls using that sequence
number. Only observable runtime actions are exposed; hidden reasoning is never
exposed.

### Evidence and result

```http
GET /tasks/{task_id}/evidence
GET /tasks/{task_id}/result
```

Evidence uses runtime-owned exact refs such as `E11 system.log:L3`.

Publication invariant:

> Once a Task is externally visible as `COMPLETED`, `result.json` and the final
> rendered result have already been atomically persisted and are immediately
> readable.

Sandbox cleanup may finish just after the business terminal state. Process
shutdown therefore waits for all known worker threads to quiesce before the
audit directory can be removed/unmounted.

## Request/error invariants

Migration to FastAPI does not weaken the previous transport guards:

- JSON bodies are limited to 64 KiB;
- duplicate JSON keys are rejected;
- Pydantic request models reject unexpected fields;
- query/body validation is normalized to `400 invalid_request`;
- lifecycle conflicts remain `409`;
- unknown API routes remain machine readable.

Error shape:

```json
{
  "error": {
    "code": "task_busy",
    "message": "active task ... is RUNNING"
  }
}
```

Main status codes:

- `400 invalid_request`
- `404 task_not_found` / `route_not_found`
- `409 task_busy` / `task_conflict`
- `413 request_too_large`
- `500 internal_error`

FastAPI OpenAPI UI:

```text
http://127.0.0.1:8787/docs
```

## Investigation completion rule

The API service must not treat "some Evidence exists" as proof that the
investigation completed successfully.

```text
normal OpenClaw turn
(returncode=0, no runtime stop reason, parsed CLI outcome completed)
        +
non-empty Evidence
        ↓
goal_satisfied
        ↓
Fresh Finalizer

abnormal OpenClaw turn
(timeout / transport / non-zero exit / malformed CLI outcome)
        +
non-empty Evidence
        ↓
Generic ConvergencePolicy
        ├─ budget/convergence reached → Fresh Finalizer from existing evidence
        └─ not reached → FAILED + investigation-error.json

user Stop / Steering
        ↓
control path only; never auto-finalize
```

## Start on Spark

The product path does not import POC code. `--sandbox-image` is supplied
explicitly. For the first integration run it is acceptable to read the already
validated image reference from the passed POC02 audit; the server itself has no
POC dependency.

```bash
cd /home/yanlan/workspaces/code/scopex

IMAGE=$(python3 - <<'PY'
import json
from pathlib import Path
p = Path(
    ".local/poc02/prepare-20260911T055921Z-042d152b/"
    "poc03-20260912T060813Z-a61cf7f6/result.json"
)
pf = Path(json.loads(p.read_text())["security_basis"]["poc02_preflight"])
cfg = json.loads((pf / "openclaw.json").read_text())
print(cfg["agents"]["defaults"]["sandbox"]["docker"]["image"])
PY
)

python3 scripts/runtime_api.py \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1 \
  --workspace tests/fixtures/poc04 \
  --sandbox-image "$IMAGE"
```

Default endpoint:

```text
http://127.0.0.1:8787
```

Uvicorn handles process signals and FastAPI lifespan invokes
`TaskService.shutdown()` so shutdown waits for Runtime workers and sandbox/audit
cleanup.

If `frontend/dist/` exists, it is mounted at `/`; otherwise the server starts in
API-only mode.

## Regression gate

After installing `requirements-api.txt`:

```bash
python3 -m unittest \
  tests.test_runtime_api_service \
  tests.test_fastapi_app \
  -v

python3 -m unittest discover -s tests -v
```

Do not diagnose API failures by weakening Runtime evidence/finalizer semantics.
Classify failures as API lifecycle/transport, Runtime execution, model output, or
validation logic first.
