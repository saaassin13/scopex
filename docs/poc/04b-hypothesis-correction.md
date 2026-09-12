# POC04-B — Hypothesis correction in the same Agent session

## Goal

Validate that a local OpenClaw Agent can revise an early hypothesis after the user explicitly challenges it, while preserving prior evidence and continuing in the same session.

POC04-A already validates tool-round steering. POC04-B validates a different failure mode: anchoring on the Agent's own previous conclusion.

## Scenario

Phase 1:
- Same fixed POC04 fixture.
- Agent may inspect only `/agent/app.log`.
- It must produce a short provisional hypothesis and state that the single-source evidence does not prove root cause.

Phase 2, same `session-key`:
- User says the visual hypothesis is under-supported and may be only a symptom.
- Agent must retain Phase-1 evidence.
- It must inspect `/agent/robot.log` and `/agent/system.log` without rereading `app.log`.
- It must revise or downgrade the earlier hypothesis when the new evidence supports a different explanation.

## Hard checks

The runner does not grade only the final prose. It verifies:

1. Phase 1 completed and used `app.log` only.
2. Phase 2 first model request contains:
   - the original user turn;
   - the Phase-1 tool result(s);
   - the Phase-1 assistant answer;
   - the correction user turn.
3. New Phase-2 tool calls inspect both `robot.log` and `system.log`.
4. No new Phase-2 tool call touches `app.log`.
5. Tool results actually contain:
   - robot healthy evidence (`joint_fault_code=0` or `controller heartbeat=ok`);
   - system failure evidence (`inference-worker exited status=137`).
6. Final visible answer:
   - mentions robot-side normal/healthy evidence;
   - mentions the inference-worker failure;
   - acknowledges revision of the earlier hypothesis;
   - does not assert visual itself is the proven root cause.

## PASS

All checks must pass:

`PASS_POC04B_CORRECTION`

This POC intentionally does not validate hard cancellation of a running process, multi-user concurrency, or restart recovery.
