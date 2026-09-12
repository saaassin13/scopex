# POC04-A — Interactive steering at a tool-round boundary

## Goal

Validate that one local OpenClaw task can be redirected by a new user instruction without losing already collected evidence.

This POC does **not** re-test POC03 root-cause quality. It tests session continuity and steering behavior.

## Scenario

Workspace contains three independent sources:

- `app.log` — application-side failure evidence and an early vision-looking symptom.
- `robot.log` — robot controller remains healthy around the same time.
- `system.log` — inference worker misses heartbeat, exits with status 137, restarts, and becomes ready.

Turn 1 starts from `app.log`. The recorder waits until at least two real tool results, including app evidence, are already in the model-request history. It then blocks the **next** inference request. No tool/process is interrupted mid-command.

Turn 2 reuses the exact same unique OpenClaw `session-key` and sends a steering message:

- stop reading `app.log`;
- preserve previous app evidence;
- inspect `robot.log` and `system.log`;
- do not keep extending the early “vision itself is broken” hypothesis;
- synthesize after the new evidence is collected.

## Why session-key

POC04 intentionally uses one explicit unique `session-key` for both turns. It does not combine `--agent` with `--session-id`, because that combination has had routing ambiguity in OpenClaw. The audit must prove continuity from the actual second-turn request history, not merely from the CLI arguments.

## Hard PASS checks

The run passes only if all of these are true:

1. Phase 1 reached the safe boundary after at least two completed real tool calls.
2. Phase 1 actually touched `app.log`.
3. Phase 1 had not already touched `robot.log` or `system.log`.
4. The first request of Phase 2 contains the original user turn.
5. The first request of Phase 2 contains the steering user turn.
6. At least one Phase-1 tool result is still present in the Phase-2 context.
7. New post-steer tool use touches `robot.log`.
8. New post-steer tool use touches `system.log`.
9. No new post-steer tool call touches `app.log`.
10. Robot evidence is actually returned by a tool.
11. System evidence is actually returned by a tool.
12. The second turn completes with a visible answer.

Final status: `PASS_POC04A_STEERING`.

## Run

First run unit tests:

```bash
python3 -m unittest tests/test_poc04_steering.py -v
```

Reuse the validated POC02 preflight already referenced by the successful POC03 run:

```bash
POC03_RUN=.local/poc02/prepare-20260911T055921Z-042d152b/poc03-20260912T060813Z-a61cf7f6
PF=$(python3 - <<'PY'
import json
from pathlib import Path
p = Path(".local/poc02/prepare-20260911T055921Z-042d152b/poc03-20260912T060813Z-a61cf7f6/result.json")
print(json.loads(p.read_text())["security_basis"]["poc02_preflight"])
PY
)

python3 scripts/poc04_steering.py \
  --preflight "$PF" \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1
```

## Audit artifacts

A run creates a private audit directory next to POC02/03 artifacts:

- `phase1/wire-*-request.json`
- `phase1/wire-*-response.bin`
- `phase2/wire-*-request.json`
- `phase2/wire-*-response.bin`
- `event-trace.json`
- `result.json`

The sandbox container prefix is unique per run and is cleaned in `finally`.

## Not covered here

POC04-A deliberately does not test:

- SIGKILL/cancel of a running shell process;
- concurrent users or tasks;
- multi-agent coordination;
- reboot/session recovery;
- scheduled jobs;
- GUI interaction.

POC04-B will reuse this harness to test correction of an already formed wrong hypothesis.
