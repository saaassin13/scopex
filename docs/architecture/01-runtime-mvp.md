# ScopeX Runtime MVP Architecture

## Product definition

ScopeX v0.1 is a local interactive diagnostic Agent for DGX Spark.

A user gives a natural-language task. OpenClaw performs autonomous investigation
with Skills and tools. ScopeX owns lifecycle/control, evidence provenance,
convergence and trustworthy final output.

Core rule:

> Model owns understanding, investigation and judgement. Runtime owns control,
> permissions, evidence identity, convergence and output strength.

## Runtime data flow

```text
User / Web UI
    |
    v
Task + Session
    |
    v
Task Controller ---------------------- Progress Events
    |
    v
OpenClaw Investigation Agent
    |
    v
Tool Gateway / Sandbox
    |
    v
Evidence Catalog (E1..En)
    |
    +---- Convergence Guard
    |           |
    |           v
    |      end Investigation
    v
Structured Fresh Finalizer
    |
    v
Generic Claim Validator
    |
    v
Deterministic Renderer
    |
    v
Final result + exact evidence refs + local audit
```

## Module responsibilities

### `scopex.runtime.task`

Owns the product task state machine only:

```text
CREATED -> RUNNING -> PAUSING -> PAUSED -> RUNNING
                         |                    |
                         |                    v
                         +-------------> FINALIZING -> COMPLETED
```

`FAILED` and `CANCELLED` are terminal states. Business diagnosis logic does not
belong in the state machine.

### `scopex.runtime.session`

Stores ScopeX-owned user/control history (`USER`, `STEER`, `STOP`, `RESUME`).
OpenClaw remains the owner of the full agent/tool transcript. ScopeX must not
build a second competing conversation engine.

### `scopex.runtime.controller`

Owns start/stop/resume/steer/finalize transitions and emits observable progress
events. It does not decide which log to grep or which tool to call.

### `scopex.events.progress`

Runtime-derived product events, not model chain-of-thought:

```text
TASK_STARTED
MODEL_REQUEST
TOOL_CALL
TOOL_RESULT
EVIDENCE_ADDED
USER_STEER
USER_STOP
SAFE_STOP
USER_RESUME
INVESTIGATION_COMPLETED
FINALIZATION_STARTED
FINALIZATION_COMPLETED
TASK_COMPLETED
```

The Web UI should render these events into user-facing progress.

### `scopex.runtime.convergence`

Generic investigation guard. Inputs are budgets and evidence progress, not
business strings. Current signals include model request count, tool count,
elapsed time, context size, stale rounds and caller-provided `goal_satisfied`.

The model is not trusted to stop reliably by itself.

### `scopex.runtime.permissions`

Default product permission model:

| Action | Default |
|---|---|
| read/query | AUTO |
| analysis/temp workspace | AUTO |
| real config write | CONFIRM |
| service restart | CONFIRM |
| delete | CONFIRM |
| device/robot control | CONFIRM |

Tool implementations must classify the requested action before execution.

### `scopex.agent`

Boundary around the agent-loop engine. OpenClaw is the current engine. ScopeX
should reuse OpenClaw session/tool/Skill/agent-loop behaviour instead of
reimplementing it.

The current MVP starts with a stable `--session-key` CLI command builder. Native
execution/recorder/sandbox integration will be migrated next from POC02/03.

### `scopex.evidence.catalog`

Runtime owns exact evidence identity. Each evidence item records at least:

```text
ref (E1...)
task_id
session_key
source
raw content
tool_call_id
observed_at
metadata
```

The model can reference `E1`, but cannot redefine what `E1` means.

### `scopex.finalizer.claims`

Structured epistemic representation:

```text
kind       fact | inference | unknown
relation   observed | temporal_association | causal_hypothesis | unknown
scope      event | time_window | component | global | unknown
confidence high | medium | low | unknown
evidence_refs
topic
```

### `scopex.finalizer.validator`

Generic structural/epistemic validation. No fixture-specific expected answer is
allowed here.

Examples:

- fact -> direct evidence + observed relation;
- temporal association -> at least two evidence refs;
- causal hypothesis -> medium/low only;
- unknown -> confidence unknown;
- all evidence refs must exist in the runtime catalog.

### `scopex.finalizer.renderer`

Deterministic rendering prevents model prose from silently upgrading evidence.

- observed fact -> runtime expands exact E lines; model topic is ignored;
- temporal association -> fixed wording explicitly says causation is not proven;
- causal hypothesis -> visibly labelled as unproven;
- unknown -> visibly labelled unknown.

### `scopex.storage.audit`

Local one-task-per-directory audit store. Target product layout:

```text
data/tasks/<task_id>/
  task.json
  session.json
  events.jsonl
  evidence.json
  claims.json
  result.json
  final.txt
```

## What remains in OpenClaw

ScopeX does **not** reimplement:

- model-driven agent loop;
- native tool calling;
- Skill loading;
- full conversation/tool transcript;
- generic file/Shell tool semantics;
- sandbox execution mechanics already provided by OpenClaw.

ScopeX wraps those capabilities with product-level control and evidence rules.

## MVP implementation order

### M1 — Core domain/control (current)

- Task/Session state;
- progress events;
- stop/resume/steer semantics;
- convergence budgets;
- permissions;
- evidence catalog;
- structured claims/validator/renderer;
- audit store.

### M2 — OpenClaw execution adapter

Extract from POC02/03 only the proven generic pieces:

- clean private runtime/config;
- same-session CLI execution;
- exact wire recording;
- safe tool-round stop boundary;
- sandbox lifecycle/cleanup;
- tool-call/result event extraction.

Do not migrate POC-specific graders into production.

### M3 — Investigation coordinator

Connect controller + OpenClaw adapter + Evidence Catalog + Convergence Guard.
First product acceptance scenario should reuse the known three-log fixture, but
production coordinator must contain no file-name-specific flow.

### M4 — Structured finalization service

Fresh no-tool context -> structured claims -> generic validation -> deterministic
rendering -> audit persistence.

### M5 — Local API / Web UI

Minimal product surface:

- task list/history;
- chat/input;
- live progress;
- Stop / Resume;
- evidence viewer;
- structured final result.

One user and one primary active task at a time for v0.1.

## Explicitly out of v0.1

- multi-Agent orchestration;
- concurrent primary tasks;
- Kubernetes/microservices;
- Prometheus/Grafana as a requirement;
- vector database/RAG unless a concrete later task requires it;
- automatic real configuration writes;
- automatic service restart;
- automatic robot/device control;
- workflow editor that hard-codes investigation paths.

## Acceptance target for the integrated MVP

A single end-to-end task must prove:

1. natural-language task starts OpenClaw investigation;
2. progress is visible from real runtime events;
3. user can steer, stop and resume the same session;
4. tool evidence becomes runtime-owned E refs;
5. generic convergence ends investigation without relying on model self-stop;
6. fresh finalizer emits structured claims;
7. validator rejects invalid epistemic structure;
8. deterministic renderer produces user-visible output;
9. every final claim can be traced back to task/session/tool/evidence audit data.
