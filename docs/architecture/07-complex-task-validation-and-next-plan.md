# Complex Task Validation and Next Plan

This document is the current checkpoint after Step 6A–6F. It records what has
actually been demonstrated on Spark, what is still only planned, and the order
of the next work. Capability, trust and usability are tracked separately.

## Frozen architecture boundary

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + the local model own investigation order, tool choice, execution,
verification and stopping. ScopeX provides data/capabilities, permissions,
product lifecycle, Evidence projection, hard runtime boundaries, audit and the
Fresh Finalizer. Do not add a second workflow/decision/action engine in ScopeX.

## Step 6 validation status

| Step | Question | Result |
|---|---|---|
| 6A | Can a long task survive context pressure/compaction without losing critical structured state? | **PASS** |
| 6B | Can large CSV/image inputs be handled through a bounded working set instead of being dumped into context? | **PASS** |
| 6C | Are hard request/time budgets owned by one enforcement layer with trustworthy partial finalization? | **PASS** |
| 6D | Can runaway tool loops be stopped by OpenClaw without ScopeX rebuilding loop detection? | **PASS** |
| 6E | Can the local Agent complete a genuinely complex multi-source task including a constrained action and post-action verification? | **CAPABILITY PASS** |
| 6F | Can the same complex task complete inside the product default 600 s / 16-request budget without weakening the task? | **PASS** |

### 6A — Context / compaction

Validated native OpenClaw compaction and structured state retention in one
persistent session. Memory Search remains disabled; compaction is for the current
long task, not durable memory.

### 6B — Working set

Validated:

- 120k-row CSV processing without putting the raw dataset into model context;
- task-local writable `/task-scratch` while external data remains read-only;
- 48-image investigation with deterministic preprocessing plus bounded
  `view_image` confirmation;
- final claim-grade image set limited to 1–4 read-only originals;
- Fresh Finalizer re-opens/hash-validates original image Evidence;
- grouped finalizer Evidence representation avoids repeated metadata expansion.

### 6C — Hard budget semantics

Hard `max_requests` / turn timeout are enforced by ModelProxy/OpenClaw/Runner,
not duplicated inside ScopeX Convergence.

- budget reached + existing Evidence -> Fresh Finalizer from observed facts;
- budget reached + no Evidence -> explicit failure;
- request/time budgets are per OpenClaw turn; task totals are audit metrics.

### 6D — Native loop convergence

OpenClaw native `tools.loopDetection` is enabled. ScopeX does not maintain a
second result-fingerprint/stale-loop detector.

Validated warning -> critical block -> model recovery/stop, plus the second
terminal shape where repeated recovery is stopped by OpenClaw. ScopeX only maps
that framework terminal into product finalization semantics.

OpenClaw runtime-control messages are retained in Trace/Progress but filtered
out of claim-grade Evidence.

### 6E — Integrated complex task capability

Real Spark task combined:

- 120,000 telemetry rows;
- 15,000+ log lines with a historical distractor;
- 48 original images;
- a read-only recovery capability and task-local mutable device state.

The Agent correctly correlated the current `PUMP_OVERLOAD`, telemetry overload
window, safety stop and relevant images; executed recovery exactly once; then
performed an independent status query and observed `RUNNING / NONE /
generation=1`. Fresh Finalizer published a valid result. Original `/agent-data`
remained unchanged.

The initial capability run completed in about **1008.5 s / 23 requests**. That
proved the task was possible but not yet inside the product default budget.

### 6F — Complex-task usability

The same task requirements were rerun with the product default hard budget:

```text
timeout = 600 s
max_requests = 16
```

Result:

```text
PASS_STEP6E_COMPLEX_TASK_CAPABILITY
within_product_default_budget = true
```

Observed metrics:

- wall time: **371.1 s**;
- forwarded requests: **14**;
- prompt tokens: **126,412**;
- completion tokens: **2,055**;
- largest prompt: **12,292 tokens**;
- `compaction_count = 0`;
- `runtime_limit = null`;
- `runtime_guard = null`.

Compared with the initial 6E capability run:

- wall time reduced by about **63%**;
- model requests reduced by about **39%**;
- prompt-token processing reduced by about **51%**;
- completion tokens reduced by about **69%**.

The trust/correctness Gate stayed unchanged:

- logs, telemetry and original images were all used;
- working set remained bounded;
- recovery executed exactly once;
- post-recovery status was queried independently;
- actual final state was `RUNNING / NONE / generation=1`;
- source `/agent-data` remained unchanged;
- Fresh Finalizer remained valid.

The generic fixes that produced this improvement were:

1. add a lightweight analysis sandbox layer with Pillow available at runtime;
2. keep runtime sandbox networking disabled and tell the Agent not to waste
   turns attempting package installation;
3. keep the Investigation Agent's terminal handoff concise because the Fresh
   Finalizer is the trusted product-output layer;
4. use profiling evidence to target actual decode/output and generic-toolbox
   waste instead of increasing budgets or writing business workflows.

**Conclusion:** complex-task capability and the current product-default usability
Gate are both proven. Step 6 is frozen.

## Current main phase — Step 7 Product Answer + Result-first UI

Step 7 should now improve the product surface without weakening the trust model.
The user cares first about the conclusion and what happened; Evidence supports
that result and should not dominate the interface.

### 7A — Constrained Answer Composer

Target flow:

```text
Evidence
  -> Fresh Finalizer
  -> Validated Claims
  -> deterministic trust rendering
  -> constrained Answer Composer
```

Rules:

- Validated Claims remain authoritative;
- the Composer may reorganize, summarize and improve wording;
- it must not add uncited factual or causal claims;
- deterministic rendering remains available as audit/trust fallback;
- no second diagnosis/planning model loop is introduced.

Primary output structure:

```text
诊断结果

结论
<用户最需要知道的结果>

说明
- <关键原因/观察>
- <关键原因/观察>

执行情况
<是否执行动作，以及真实验证结果>

建议
<下一步>

相关证据 >
```

### 7B — Result-first UI

The primary task page should emphasize:

1. conclusion;
2. concise explanation;
3. action/execution result;
4. recommendation/next action;
5. expandable Evidence / Findings.

Evidence/Finding is supporting material. Do not spend disproportionate product
space on an Evidence viewer when the user primarily needs the result.

### 7C — Real Spark product integration

Validate the actual product path, not only probe scripts:

- FastAPI task create/status/control/events/evidence/result;
- Vue result page and task controls;
- live Progress readability during a multi-minute task;
- Stop / Resume / Steering through the product surface;
- final result + Evidence expansion;
- refresh/reconnect behavior.

Keep polling initially if it is adequate. Introduce SSE only when measured UX or
network behavior justifies it.

### 7D — Real business acceptance task

After the product surface is integrated, run at least one real diagnostic task
from the intended business environment through the API/UI path. Do not use the
synthetic 6E fixture as the only product-acceptance evidence.

Acceptance should include:

- correct final conclusion;
- understandable explanation;
- trustworthy action/verification presentation;
- Evidence traceability;
- no hidden business workflow in ScopeX;
- acceptable user-visible latency/progress behavior.

## Optional performance track — not blocking Step 7

The original profile showed local decode/output cost is still substantial. vLLM
throughput work is now optional rather than the next mandatory phase because the
600 s / 16-request Gate passed.

Only reopen this track if real product tasks expose a concrete SLA issue. Then
use controlled experiments:

- decode tokens/s and TTFT;
- vLLM launch parameters and resource utilization;
- supported speculative decoding / draft-model options for this exact Qwen
  configuration;
- prefix-cache per-run deltas;
- correctness and memory-pressure comparison under an unchanged task.

Do not improve a benchmark by weakening diagnosis/action/verification semantics.

## Known non-blocking gaps

Track these, but do not pre-emptively overbuild them:

- mixed Gateway/Sandbox tasks sharing `/task-scratch` need a real-run check;
- task-scratch retention/cleanup policy is still simple host retention;
- tasks requiring more than 4 simultaneous final claim-grade originals are not
  generalized yet;
- sandbox CPU/memory limits may need tuning if future real tasks require heavier
  local processing;
- compaction itself is relatively expensive and should remain a pressure valve,
  not the normal path;
- Memory Search remains out of scope until a real cross-task recall requirement
  appears.

## Merge policy

`main` contains validated runtime/product behavior. Step 6A–6F are frozen as the
current baseline. Step 7 work should proceed in small branches with explicit
product acceptance criteria; do not mix unrelated UI experiments or speculative
performance work into the trusted runtime baseline.
