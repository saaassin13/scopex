# POC -> Runtime Migration Map

This document controls extraction from the validated POC harnesses into product
code. The rule is **extract generic behaviour, never import POC modules from
`scopex/`**.

## Already extracted

| POC source concept | Product module | Status |
|---|---|---|
| POC04/05 same-session control | `scopex.runtime.task/session/controller` | done |
| POC05 ProgressTracker concept | `scopex.events.progress` + `events.observer` | done |
| POC05 safe tool-round stop | `scopex.runtime.stop.SafeStopGate` | done |
| POC04/05 tool-call/result parsing | `scopex.agent.trace` | done |
| POC03/06 runtime-owned evidence refs | `scopex.evidence.catalog` | done |
| POC06 evidence progress | `scopex.evidence.collector` | done |
| POC06 structured claim schema | `scopex.finalizer.claims` | done |
| POC06 generic validation | `scopex.finalizer.validator` | done |
| POC06 deterministic rendering | `scopex.finalizer.renderer` | done |
| POC06 validate->render pipeline | `scopex.finalizer.service` | done |
| POC02/04 session-key CLI invocation | `scopex.agent.openclaw.OpenClawCommandBuilder` | done |
| local audit files | `scopex.storage.audit` | done |

## Next extraction: OpenClaw execution adapter

### From `scripts/poc02_run.py`

Extract generic pieces only:

- loopback endpoint validation;
- exact request-body forwarding;
- wire request/response audit records;
- request budget and deadline;
- CLI outcome parsing;
- process-group termination on timeout;
- no automatic replay after uncertain upstream cancellation.

Do **not** migrate:

- baseline-suite assumptions;
- POC02 reference hash comparison;
- POC-specific pass/fail labels;
- expected prompt/output grading.

Target modules:

```text
scopex/agent/model_proxy.py
scopex/agent/openclaw_runner.py
scopex/agent/outcome.py
```

### From `scripts/poc02_preflight.py`

Extract only product security configuration construction:

- private OpenClaw home/state/config;
- sandbox `network=none`;
- read-only root;
- tmpfs;
- cap drop;
- uid/gid;
- resource limits;
- approved tool surface.

Keep native preflight checks as regression tests. Do not make every product task
rerun the full POC02 preflight.

Target module:

```text
scopex/agent/openclaw_config.py
```

## Then: Investigation coordinator

After the execution adapter is stable, create:

```text
scopex/runtime/investigation.py
```

It will wire:

```text
TaskController
+ OpenClaw runner
+ AgentProgressObserver
+ SafeStopGate
+ EvidenceCollector
+ ConvergencePolicy
```

The coordinator may decide **when** to stop/finalize, but never hard-code the
business investigation path.

## Then: Fresh structured finalizer client

The HTTP streaming transport from POC03 is generic and can move to:

```text
scopex/finalizer/client.py
```

Product pipeline:

```text
EvidenceCatalog
 -> fresh no-tool model request
 -> JSON claim payload
 -> FinalizationService
 -> audit persistence
```

No POC-specific semantic grader is part of this path.

## Removal policy

Do not delete the main POC01–POC06 scripts while extraction is ongoing. A POC
may be removed only after:

1. all generic behaviour it uniquely proves has a product regression test;
2. the integrated Runtime MVP repeats the same capability end-to-end;
3. its historical result/audit requirements are documented.

Obsolete compatibility branches without unique evidence may be removed earlier.
