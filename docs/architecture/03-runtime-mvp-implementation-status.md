# Runtime MVP Implementation Status

Production code lives under `scopex/`. POC01–POC06 remain frozen regression
baselines; Step 6A–6E are now the current validated Runtime checkpoint.

## Current validated chain

```text
Goal / Trigger
  -> OpenClaw + local qwen3.8-27b-nvfp4
  -> autonomous investigation / tool use / action / verification
  -> ScopeX Trace observation
  -> claim-grade Evidence projection
  -> Fresh Structured Finalizer
  -> Generic Claim Validator
  -> deterministic Renderer
  -> Runtime Audit
  -> Task COMPLETED
```

The Runtime MVP core chain, long-context handling, large-data/multi-image working
set, hard budgets, native loop convergence and one integrated complex task have
all passed real Spark validation.

## Step 6 status

| Step | Result | What is proven |
|---|---|---|
| 6A Context / Compaction | **PASS** | one long-running OpenClaw session can compact and preserve critical structured state |
| 6B Working Set | **PASS** | large CSV + many images can be reduced through tools/scratch without raw-data context dumping |
| 6C Hard Budget | **PASS** | request/time limits have one enforcement path and budget exhaustion finalizes only from observed Evidence |
| 6D Native Loop Convergence | **PASS** | OpenClaw owns repeated-tool detection/recovery/terminal guard; ScopeX only maps framework terminal semantics |
| 6E Complex Task Gate | **CAPABILITY PASS** | diagnosis + multi-source correlation + constrained action + independent post-action verification works end to end |

6E used 120k telemetry rows, 15k+ log lines and 48 images. The Agent correctly
identified the current overload incident, ignored a historical distractor,
executed the permitted recovery exactly once, and independently re-queried the
state until `RUNNING / NONE / generation=1` was observed. Fresh Finalizer then
published a valid trusted result.

## Runtime ownership

### OpenClaw owns

- Agent Loop and model-driven investigation order;
- file / shell / process / image tools;
- Skills and tool transcript;
- sandbox execution semantics;
- native tool-loop detection and recovery;
- compaction of long current-task context.

### ScopeX owns

- product Task / Session / Stop / Resume / Steering;
- hard product request/time boundaries around one OpenClaw turn;
- immutable Evidence identity/provenance;
- filtering OpenClaw runtime-control annotations out of claim-grade Evidence;
- Fresh Finalizer + Claim validation;
- audit/result persistence and API/UI surface.

ScopeX must not grow into a second Decision Engine, Action Engine or Workflow
Engine.

## Evidence / trust checkpoint

Current production path supports:

- exact read-line Evidence;
- bounded exec-line Evidence with full-result digest;
- bounded 1–4 original-image claim-grade Evidence;
- SHA verification and image re-open in Fresh Finalizer;
- large image sets as investigation working sets without promoting every frame;
- grouped finalizer Evidence representation so hundreds of E refs do not repeat
  long command metadata;
- OpenClaw loop warning/critical/recovery text retained in Trace but excluded
  from business Evidence.

Trust stack:

```text
Raw Tool Result / Original Image
        ↓
Evidence Snapshot
        ↓
Validated Claim
        ↓
Product conclusion / explanation
```

## Budget / convergence checkpoint

Hard request/time budgets are not ScopeX Convergence signals. ModelProxy /
OpenClaw / Runner enforce the resource boundary. ScopeX reacts only after that
boundary:

```text
budget reached + Evidence     -> Fresh Finalizer
budget reached + no Evidence  -> explicit failure
```

OpenClaw native loop detection remains inside the Agent Loop. ScopeX does not
maintain a second result-fingerprint loop detector.

## Current product gap

Complex-task **capability is proven**, but complex-task **usability is not yet
product-pass**.

The passing 6E task took about 1008.5 s and 23 forwarded model requests, versus
the current product default of 600 s / 16 requests. Offline profiling shows
about 90% of wall time in model requests and strong dependence on completion
length. The largest prompt was still below 19k tokens and no compaction occurred,
so the immediate bottleneck is not a 32k context wall.

Step 6F therefore remains an experiment branch. It is intentionally not part of
this validated main checkpoint until the same 6E Gate passes within the product
budget.

## Regression

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only
python3 -m unittest discover -s tests -v
```

High-value Step 6 real probes live under `scripts/step6*.py`. They are validation
harnesses, not production workflow logic.

## Next product phase

See [Complex Task Validation and Next Plan](07-complex-task-validation-and-next-plan.md).

Order:

1. 6F generic execution-efficiency pass under the unchanged 600 s / 16-request Gate;
2. if still slow, controlled vLLM decode-throughput / speculative-decoding tests;
3. Step 7 constrained Answer Composer and result-first UI;
4. real Spark FastAPI + Vue product integration and, only if useful, SSE.
