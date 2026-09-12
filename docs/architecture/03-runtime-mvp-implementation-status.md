# Runtime MVP Implementation Status

This is the engineering checkpoint after POC01-POC06 were frozen as regression
baselines and production code began moving under `scopex/`.

## Implemented

### M1 — Runtime domain/control

- Task state machine;
- same-task Session control history;
- thread-safe TaskController;
- runtime Progress events;
- generic ConvergencePolicy;
- permission policy;
- deterministic SafeStopGate with resume reset;
- local AuditStore.

### M2 — OpenClaw execution boundary

- same-session `--session-key` command builder;
- POC02-derived private OpenClaw environment;
- POC02-derived sandbox/security config builder;
- enforced `thinking=false` and approved tool surface;
- exact-body loopback ModelProxy with local bearer token;
- wire request/response/meta audit;
- request policy validation before forwarding;
- Progress observer from real assistant/tool transcript state;
- deterministic stop before model forwarding;
- CLI process-group timeout termination;
- CLI outcome parser and no-auto-replay warning;
- task-scope sandbox cleanup;
- `OpenClawTaskRuntime` preserving HOME/STATE/session-key across turns.

### M3 — Investigation coordinator (initial)

`scopex.runtime.investigation.InvestigationCoordinator` currently wires:

```text
TaskController
+ OpenClawTaskRuntime
+ SafeStopGate
+ EvidenceCatalog/Collector
+ ConvergencePolicy
+ Structured Finalization
```

Important control semantics already implemented:

- stop gate is armed before `PAUSING` is exposed, removing a model-forward race;
- safe-stop callback and API control share a control lock;
- Resume waits for the stopped OpenClaw turn to fully unwind before resetting the
  stop gate and starting the next turn;
- evidence stale-round accounting happens only at an explicit evidence
  checkpoint after extractors run, not prematurely at turn return.

### M4 — Structured finalization core

- loopback streaming Fresh Finalizer client;
- generic evidence-calibration prompt;
- strict JSON/fence parser;
- Claim schema;
- generic validator;
- deterministic renderer;
- one-call `StructuredFinalizer` orchestrator;
- no automatic retry.

## Deliberately not implemented yet

### Automatic evidence extraction

The runtime can assign stable evidence refs, but it does not yet decide which
arbitrary bytes from a tool result are meaningful evidence.

This is deliberate: putting log/business heuristics in `EvidenceCollector`
would recreate hard-coded Handlers.

Next design step is an extractor interface that can use source/tool metadata and
Skill/domain adapters while keeping `EvidenceCatalog` generic.

### Pending-steer queue

POC04 proves same-session steering. The current production coordinator covers
Start/Stop/Resume/Finalize. Mid-turn Steering needs the same safe-boundary
mechanism plus a pending control-message queue. It should be added after the
current control path passes Spark tests rather than mixing another concurrency
feature into the first extraction.

### Local API / Web UI

Not started. The UI should be built only after the runtime path passes a real
OpenClaw + local-vLLM integration run.

## Regression tests added

Production modules now have tests for:

- task/session/controller transitions;
- progress events;
- stop reset and resume semantics;
- convergence and permissions;
- evidence identity/deduplication;
- malformed claim payload handling;
- deterministic rendering;
- Fresh Finalizer SSE transport;
- OpenClaw sandbox config invariants;
- private process environment;
- exact-body model proxy;
- request policy and safe-stop hook;
- CLI process/outcome handling;
- same-session multi-turn task runtime using fake OpenClaw/model;
- sandbox cleanup;
- coordinator evidence checkpoints/convergence;
- in-memory end-to-end runtime smoke flow.

## Required Spark checkpoint

Before extracting more functionality, run on the Spark checkout:

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only

python3 -m unittest \
  tests/test_runtime_mvp_core.py \
  tests/test_agent_runtime_bridge.py \
  tests/test_finalizer_client.py \
  tests/test_openclaw_config.py \
  tests/test_openclaw_environment.py \
  tests/test_model_proxy.py \
  tests/test_proxy_control.py \
  tests/test_openclaw_runner_outcome.py \
  tests/test_openclaw_task_runtime.py \
  tests/test_sandbox_manager.py \
  tests/test_investigation_coordinator.py \
  tests/test_structured_finalizer.py \
  tests/test_runtime_end_to_end_smoke.py \
  -v
```

Then run the full historical regression suite:

```bash
python3 -m unittest discover -s tests -v
```

Do not start a live OpenClaw/vLLM Runtime MVP run until these are clean.

## Next after tests pass

1. Fix any extraction regressions exposed by the full suite.
2. Add the generic EvidenceExtractor boundary.
3. Add pending Steering at the same safe boundary used by Stop.
4. Persist Task/Session/Events/Evidence/Claims/Result through AuditStore.
5. Run one real integrated Runtime MVP task against the existing local vLLM and
   OpenClaw using the known three-log fixture.
6. Only after that, start the local API/Web UI.
