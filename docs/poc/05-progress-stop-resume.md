# POC05 — Progress visibility, stop, resume, and re-steer

## Goal

Validate product-control behavior for one local OpenClaw task:

1. ScopeX exposes meaningful progress from observable runtime events.
2. A user STOP prevents the next model inference at a safe tool-round boundary.
3. No additional phase-1 model/tool work occurs after STOP.
4. A later RESUME uses the same OpenClaw session and preserves prior tool results.
5. RESUME may also change the next investigation priority.

POC05 does **not** expose hidden model reasoning. Progress is derived from model-request, tool-call, and tool-result lifecycle data.

## Progress event surface

The runner writes `progress.jsonl` and prints the same events to stdout:

- `TASK_STARTED`
- `MODEL_REQUEST`
- `TOOL_CALL`
- `TOOL_RESULT`
- `USER_STOP`
- `SAFE_STOP`
- `USER_RESUME`
- `TASK_COMPLETED` / `TASK_ENDED_WITH_ERROR`

Tool progress contains the tool name, file targets when deterministically visible in arguments, and result size. It does not contain chain-of-thought.

## Stop semantics

STOP is a runtime control action, not a prompt sent to the model.

For this POC, STOP is applied at a **tool-round boundary**: completed tool results are already persisted, and ScopeX blocks the next model request before forwarding it to vLLM. A currently running shell/process is not hard-killed in this POC.

The stopped request must have `forwarded=false` in its recorder metadata, and there must be no later phase-1 request.

## Resume semantics

RESUME is a new user turn sent with the exact same unique `session-key`.

The first resumed request must contain:

- the original user task,
- prior completed tool results,
- the new resume/steering message.

The resume instruction tells the Agent to keep the existing `app.log` evidence and investigate `system.log` before `robot.log`, without rereading `app.log`.

## PASS criteria

`PASS_POC05_PROGRESS_STOP_RESUME` requires all of the following:

- progress events exist before STOP, including real `app.log` tool results;
- STOP occurs after at least two completed tool results;
- the stop-point model request is recorded but not forwarded;
- no phase-1 request exists after the stop point;
- the resumed turn preserves prior tool-result IDs in the same session;
- resumed investigation does not reread `app.log`;
- the first new file target is `system.log`;
- both `system.log` and `robot.log` are actually inspected and return evidence;
- the resumed turn completes with a visible final answer;
- a terminal `TASK_COMPLETED` progress event is emitted;
- staged input files remain unchanged.

## Explicitly out of scope

This POC does not yet validate:

- hard cancellation of a shell/Python process while it is executing;
- process-tree SIGTERM/SIGKILL semantics;
- restart/crash recovery across ScopeX process restarts;
- multiple concurrent tasks;
- UI rendering or WebSocket/SSE transport to a frontend;
- diagnosis correctness or evidence-strength calibration.

Those should be validated separately after the control loop itself is proven.
