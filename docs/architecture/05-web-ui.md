# ScopeX Web UI

The v0.1 UI is a thin Vue 3 client over the Local Runtime API.

## Stack

```text
Vue 3
+ TypeScript
+ Vite
+ Vue Router (hash history)
+ native fetch
```

No Pinia, axios, component framework or WebSocket is required for the first
product pass. Add them only when the observed UI complexity justifies them.

## Boundary

```text
Vue
 ├─ create/select Task
 ├─ poll Progress events
 ├─ display Evidence
 ├─ display rendered Result
 └─ Stop / Resume / Steer
        ↓
FastAPI
        ↓
TaskService / ScopeX Runtime
```

The UI must not:

- parse OpenClaw hidden/internal transcripts;
- infer root cause from raw logs on its own;
- rewrite Evidence content;
- upgrade inference to fact;
- implement task lifecycle rules independently from the API;
- directly call vLLM or OpenClaw.

## Routes

Hash history is used so the existing API paths remain unchanged:

```text
/#/                  dashboard / create task
/#/tasks/{task_id}   task workspace
```

The browser therefore never conflicts with API routes such as
`/tasks/{task_id}`.

## Task workspace

The first workspace exposes four product surfaces:

```text
Task header
  state + original request

Progress
  observable Runtime events only

Evidence
  E ref + source:line + exact raw evidence

Result
  deterministic rendered diagnosis
```

Control actions:

- RUNNING: `Steer`, `Stop`;
- PAUSED: `Resume`;
- terminal: read-only.

## Progress transport

v0.1 polls:

```http
GET /tasks/{id}/events?after=<seq>
```

at roughly 1.5 seconds and advances `next_after`. Evidence/result/task snapshots
are refreshed in the same loop.

This is intentionally simpler than SSE while the product interaction model is
still changing. Once the UI behavior is stable, replace only the Progress
transport with SSE; TaskService and Runtime semantics remain unchanged.

## Development

```bash
cd frontend
npm install
npm run dev
```

Vite listens on `127.0.0.1:5173` and proxies Runtime API paths to
`127.0.0.1:8787`.

Type-check + production build:

```bash
npm run build
```

The output is `frontend/dist/`. `scripts/runtime_api.py` automatically mounts
that directory at `/` when it exists, so the current single-machine deployment
does not require Nginx.
