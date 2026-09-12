# Runtime MVP Implementation Status

This is the engineering checkpoint after POC01-POC06 were frozen as regression
baselines and production code moved under `scopex/`.

## Implemented

### M1 — Runtime domain/control

- Task state machine;
- same-task Session control history;
- thread-safe TaskController;
- runtime Progress events;
- generic ConvergencePolicy;
- permission policy;
- deterministic safe-boundary gate with next-turn reset;
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
- deterministic boundary before model forwarding;
- CLI process-group timeout termination;
- CLI outcome parser and no-auto-replay warning;
- task-scope sandbox cleanup;
- `OpenClawTaskRuntime` preserving HOME/STATE/session-key across turns.

### M3 — Investigation coordinator

`scopex.runtime.investigation.InvestigationCoordinator` now wires:

```text
TaskController
+ OpenClawTaskRuntime
+ SafeStopGate
+ Pending Steering
+ Evidence Extraction/Catalog
+ ConvergencePolicy
+ Structured Finalization
+ Runtime Audit
```

Implemented control semantics:

- stop gate is armed before `PAUSING` is exposed, removing a model-forward race;
- safe-boundary callback and API controls share a control lock;
- Resume waits for the stopped OpenClaw turn to unwind before resetting the gate;
- mid-turn Steering interrupts at the same model-request boundary but keeps the
  Task RUNNING, then injects accumulated steering in the same session;
- explicit Stop clears stale pending Steering;
- multiple Steering instructions are preserved in order and newer instructions
  are declared authoritative on conflict;
- evidence stale-round accounting happens only after extraction completes;
- configured evidence extractors run automatically against the turn wire audit;
- no extractor is installed implicitly, avoiding hidden business Handler logic.

### M4 — Evidence + structured finalization

- stable E1/E2/... EvidenceCatalog with exact provenance;
- generic `EvidenceExtractor` protocol;
- deterministic `EvidenceExtractionPipeline` preserving tool-call order;
- bounded opt-in `ReadResultExtractor` for smoke/basic file workflows;
- loopback streaming Fresh Finalizer client;
- generic evidence-calibration prompt;
- strict JSON/fence parser;
- Claim schema;
- generic validator;
- deterministic renderer;
- one-call `StructuredFinalizer` orchestrator;
- no automatic retry.

### M5 — Audit

One task directory can now contain:

```text
task.json
session.json
events.jsonl
evidence.json
claims.json
result.json
final.txt
```

`RuntimeAudit` owns task/session/evidence/claim/result snapshots and
`AuditEventSink` persists progress events while optionally forwarding them to a
live UI sink.

## Important engineering boundaries

- production `scopex/` modules must not import `scripts/poc*.py`;
- Skills/domain adapters may decide what evidence is meaningful;
- Runtime owns evidence identity/provenance and epistemic validation;
- no automatic retry after ambiguous OpenClaw/model failures;
- fact prose is rendered from runtime-owned evidence, not model free text;
- no business-specific error strings or CowDisinfect logic in generic Runtime.

## Regression coverage

Production modules now have tests for:

- task/session/controller transitions;
- Progress, Stop and Resume semantics;
- pending Steering and same-task next-turn continuation;
- safe-boundary reset and control races;
- convergence and permissions;
- OpenClaw private environment and sandbox config;
- exact-body model proxy and request policy;
- CLI process/outcome handling;
- same-session multi-turn OpenClaw task runtime with fake CLI/model;
- sandbox cleanup;
- generic evidence extraction, dedupe and deterministic E ordering;
- turn-audit trace aggregation;
- unified Runtime audit persistence;
- coordinator extraction + audit + convergence;
- malformed claim payload handling;
- deterministic rendering;
- Fresh Finalizer SSE transport;
- structured finalizer orchestration;
- in-memory end-to-end runtime flow.

## Current checkpoint: run Spark regression

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
  tests/test_evidence_extractor.py \
  tests/test_runtime_audit.py \
  tests/test_investigation_audit_extraction.py \
  tests/test_steering_queue.py \
  -v

python3 -m unittest discover -s tests -v
```

Do not run the real Runtime MVP smoke until both are clean.

## Real Runtime MVP smoke

A real integration runner now exists:

```text
scripts/runtime_mvp_smoke.py
```

It uses only production `scopex/` runtime modules for execution. It stages the
known app/system/robot fixture, runs real OpenClaw against the existing local
vLLM, extracts read evidence, invokes one fresh structured finalizer and writes a
complete audit directory.

The POC02 preflight is used only to retrieve the already-validated sandbox image
reference; no POC runner/grader is imported.

Expected pass status:

```text
PASS_RUNTIME_MVP_SMOKE
```

## After the real smoke passes

1. replace the smoke-only `ReadResultExtractor` with domain/Skill extractor
   adapters for real device tasks;
2. add the local Runtime API (task create/control/events/result);
3. build the minimal local Web UI over that API;
4. then validate a real CowDisinfect task through the product surface, not
   through POC scripts.
