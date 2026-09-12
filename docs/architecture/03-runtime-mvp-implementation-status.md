# Runtime MVP Implementation Status

POC01-POC06 are frozen regression baselines. Production code lives under
`scopex/`. The first real end-to-end Runtime MVP integration against OpenClaw +
local vLLM has now passed.

## Validated checkpoint

Real Spark integration result:

```text
PASS_RUNTIME_MVP_SMOKE
```

Validated in one production Runtime path:

```text
Task
  -> OpenClaw Investigation
  -> local ModelProxy / vLLM
  -> real read/exec tools
  -> EvidenceExtractionPipeline
  -> EvidenceCatalog
  -> Fresh Structured Finalizer
  -> Generic Claim Validator
  -> Deterministic Renderer
  -> Runtime Audit
  -> Task COMPLETED
  -> task-scope sandbox cleanup
```

The passing run proved all three fixture files were actually read, Evidence was
created automatically, the fresh finalizer ended with `finish_reason=stop`, the
structured output validated, the Task reached `COMPLETED`, and the task-owned
sandbox container was cleaned without warnings.

This closes the Runtime MVP **core execution-chain feasibility** milestone. Do
not reopen it for output-format refinements unless a later regression provides
new evidence that the control chain itself is broken.

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
- session-key agent identity validation;
- POC02-derived private OpenClaw environment;
- POC02-derived sandbox/security config builder;
- enforced `thinking=false` and approved tool surface;
- exact-body loopback ModelProxy with local bearer token;
- atomic wire request/response/meta audit;
- request policy validation before forwarding;
- Progress observer from real assistant/tool transcript state;
- deterministic boundary before model forwarding;
- CLI process-group timeout termination;
- CLI outcome parser and no-auto-replay warning;
- task-scope sandbox cleanup;
- `OpenClawTaskRuntime` preserving HOME/STATE/session-key across turns.

### M3 — Investigation coordinator

`scopex.runtime.investigation.InvestigationCoordinator` wires:

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
- bounded opt-in `ReadResultExtractor` for whole-result evidence;
- opt-in `ReadLineExtractor` for exact line/event evidence without business
  parsing;
- repeated equal lines at different observed positions keep distinct evidence
  identities;
- loopback streaming Fresh Finalizer client;
- compact generic evidence-calibration prompt;
- explicit truncation/incomplete-stream detection;
- Claim schema;
- generic validator;
- duplicate user-visible claim rejection;
- deterministic concise renderer;
- one-call `StructuredFinalizer` orchestrator;
- no automatic retry.

### M5 — Audit

One task directory can contain:

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
- no business-specific error strings or CowDisinfect logic in generic Runtime;
- the Investigation Agent's free final prose is not the product diagnosis; only
  validated structured claims are rendered to the user.

## Output-quality follow-up after core PASS

The first passing smoke exposed two presentation-quality issues without
invalidating the core Runtime chain:

1. whole-file Evidence made each fact expand an entire log;
2. the model could emit two structurally equivalent facts that rendered the same
   evidence twice.

The product path now supports line-level evidence and rejects duplicate rendered
claim identities. `scripts/runtime_mvp_refinalize.py` can validate these changes
against a previously passed wire trace without rerunning OpenClaw or tools.

## Fast regression for evidence/output changes

```bash
cd /home/yanlan/workspaces/code/scopex
git pull --ff-only

python3 -m unittest \
  tests/test_runtime_mvp_core.py \
  tests/test_evidence_extractor.py \
  tests/test_structured_finalizer.py \
  tests/test_runtime_end_to_end_smoke.py \
  -v

python3 -m unittest discover -s tests -v
```

For a previously passed Runtime smoke, validate only the new evidence/finalizer
path without another Investigation:

```bash
python3 scripts/runtime_mvp_refinalize.py \
  --run <existing .local/runtime-mvp-smoke/... directory> \
  --model qwen3.8-27b-nvfp4 \
  --base-url http://127.0.0.1:18002/v1
```

Expected:

```text
PASS_RUNTIME_MVP_REFINALIZE
```

## Next product phase

After the evidence/output regression is clean:

1. implement the local Runtime API: task create/status/control/events/evidence/result;
2. build the minimal local Web UI over that API;
3. then validate a real CowDisinfect task through the product surface, not
   through POC scripts;
4. performance tuning is measured separately: the core smoke is a complex
   diagnostic path and should not redefine the simple-task ~120s target.
