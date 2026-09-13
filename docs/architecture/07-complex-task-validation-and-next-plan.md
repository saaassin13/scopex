# Complex Task Validation and Next Plan

This document is the current checkpoint after Step 6A–6E. It records what has
actually been demonstrated on Spark, what is still only planned, and the order
of the next work. It is intentionally product-oriented: capability, trust and
usability are tracked separately.

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

### 6E — Integrated complex task gate

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

**Conclusion:** complex-task capability is proven. The system is no longer
limited to simple daily Q&A or short diagnostics.

## Usability result: not yet product-pass

The 6E run took about 1008.5 s and 23 forwarded model requests, exceeding the
current product default of 600 s / 16 requests.

Offline profiling of that run shows:

- model requests consumed about 908.8 s, roughly 90.1% of task wall time;
- prompt tokens summed to about 257k, completion tokens to 6.7k;
- request duration is strongly associated with completion length, while prompt
  length is a weak predictor in this run;
- effective decode rate is only about 7.4 completion tokens/s;
- vLLM prefix cache is enabled and has real cache hits, so "cache disabled" is
  not the primary explanation;
- the image phase also wasted rounds because the validated sandbox lacked a
  common image library, causing runtime package-install attempts and hand-written
  PNG processing.

The current bottleneck is therefore **multi-round decode/output cost plus generic
toolbox gaps**, not a 32k-context hard wall.

## Next plan

### 6F-1 — Generic execution-efficiency pass

Status: **in progress on `step6f-complex-task-usability`; not yet merged.**

1. Build a lightweight analysis sandbox on top of the validated sandbox image.
2. Preinstall only the generic dependency actually shown missing by the 6E
   trace (Pillow); do not add pandas/OpenCV/etc. without evidence.
3. Runtime sandbox remains `network=none`; package installation is build-time
   only.
4. Tell the Agent that runtime networking is unavailable so it does not waste
   turns attempting `pip`/`apt`/`npm` installs.
5. Keep the investigation result handoff concise because the Fresh Finalizer is
   the user-facing trusted output layer.
6. Rerun the *same* 6E fixture under the real product budget: **600 s / 16
   requests**. Do not loosen the Gate.

Acceptance:

```text
PASS_STEP6E_COMPLEX_TASK_CAPABILITY
within_product_default_budget = true
forwarded_requests <= 16
total_wall_s <= 600
recovery_exactly_once = true
verification_after_recovery = true
published_valid = true
runtime_limit = null
runtime_guard = null
```

### 6F-2 — vLLM decode-throughput experiments

Only enter this step if 6F-1 preserves correctness but still misses the product
budget.

Controlled experiments, one factor at a time:

- measure decode tokens/s and time-to-first-token with the production prompt;
- inspect current vLLM launch parameters and GPU/CPU/memory utilization;
- evaluate supported speculative decoding / draft-model options for this exact
  served Qwen configuration;
- compare throughput, correctness and memory pressure under the same complex-task
  fixture;
- keep prefix caching enabled and measure per-run deltas rather than relying on
  global cumulative metrics.

Do not improve the benchmark by reducing required diagnosis/action/verification
semantics.

### Step 7 — Product answer and UI quality

After complex-task usability is acceptable:

1. Add a constrained Answer Composer over validated Claims.
2. Keep deterministic rendering as the audit/trust fallback.
3. Make the primary UI result-oriented:
   - conclusion;
   - concise explanation;
   - action/execution result;
   - recommendation/next action;
   - expandable Evidence.
4. Evidence/Finding is supporting material, not the main product surface.
5. Complete real Spark FastAPI + Vue integration and then consider SSE instead
   of polling.

Target presentation:

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

`main` contains only validated runtime behavior. Step 6A–6E are eligible to be
merged. Step 6F optimization experiments remain on their branch until the
600 s / 16-request rerun passes or produces a clearly documented new finding.
