# Local Runtime API

ScopeX Runtime MVP has passed real OpenClaw + local-vLLM integration. The next
product boundary is a loopback-only HTTP API over the proven runtime.

## Product boundary

The API is deliberately thin:

```text
Web UI / local client
        ↓ HTTP on loopback
TaskService
        ↓
InvestigationCoordinator
        ↓
OpenClaw + local model + sandbox
        ↓
AuditStore
```

The HTTP layer does not implement diagnosis logic, tool routing, evidence rules
or model prompts.

Current constraints:

- single user;
- one non-terminal major task at a time;
- a PAUSED task still owns the slot;
- loopback bind only;
- no database;
- events use incremental polling, not SSE/WebSocket yet;
- historical tasks remain readable after restart but are not resumable after the
  ScopeX process is restarted.

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

Returns `202` with the created Task snapshot.

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

### Progress

```http
GET /tasks/{task_id}/events?after=0
```

Response includes `next_after`. A UI polls again using that sequence number.
Only observable runtime actions are exposed; hidden reasoning is never exposed.

### Evidence and result

```http
GET /tasks/{task_id}/evidence
GET /tasks/{task_id}/result
```

Evidence uses runtime-owned exact refs such as `E11 system.log:L3`. Result is
unavailable until finalization completes.

## Error shape

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
- `500 internal_error`

## Start the API on Spark

The product path does not import POC code. `--sandbox-image` is supplied
explicitly. For the first integration run it is acceptable to read the already
validated image reference from the passed POC02 audit, but the server itself
has no POC dependency.

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

## First real API verification

Create a task:

```bash
curl -sS -X POST http://127.0.0.1:8787/tasks \
  -H 'Content-Type: application/json' \
  -d '{"message":"分析 2026-09-12 10:15 左右任务执行失败。请实际读取 /agent/app.log、/agent/system.log、/agent/robot.log；不要把 status=137 自动等同于 OOM，也不要把当前窗口未见机器人异常扩大成全局健康结论。"}'
```

Copy the returned `id`, then poll:

```bash
TASK_ID=<returned-id>
curl -sS "http://127.0.0.1:8787/tasks/$TASK_ID"
curl -sS "http://127.0.0.1:8787/tasks/$TASK_ID/events?after=0"
curl -sS "http://127.0.0.1:8787/tasks/$TASK_ID/evidence"
curl -sS "http://127.0.0.1:8787/tasks/$TASK_ID/result"
```

Expected terminal state is `COMPLETED`, with line-level evidence and the same
evidence-calibrated rendered result already validated by Runtime MVP refinalize.
