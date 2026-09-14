# Complex Task Validation and Next Plan

状态：**2026-09-14 当前有效**。

This document records what has actually been demonstrated on Spark, what is now implemented in the product, what real-business failures exposed, and the remaining acceptance order. Capability, trust, usability and deployment are tracked separately.

## Frozen architecture boundary

> **OpenClaw owns execution. ScopeX owns product control and trust.**

OpenClaw + the local model own investigation order, tool choice, execution, verification and stopping. ScopeX provides data/capabilities, task scope, permissions, product lifecycle, Evidence projection, hard runtime boundaries, audit and trusted result composition.

Do not add a second workflow/decision/action engine in ScopeX.

---

## Step 6 validation status — frozen

| Step | Question | Result |
|---|---|---|
| 6A | Can a long task survive context pressure/compaction without losing critical structured state? | **PASS** |
| 6B | Can large CSV/image inputs be handled through a bounded working set instead of being dumped into context? | **PASS** |
| 6C | Are hard request/time budgets owned by one enforcement layer with trustworthy partial finalization? | **PASS** |
| 6D | Can runaway tool loops be stopped by OpenClaw without ScopeX rebuilding loop detection? | **PASS** |
| 6E | Can the local Agent complete a genuinely complex multi-source task including a constrained action and post-action verification? | **CAPABILITY PASS** |
| 6F | Can the same complex task complete inside the product default 600 s / 16-request budget without weakening the task? | **PASS** |

### 6A — Context / compaction

Validated native OpenClaw compaction and structured state retention in one persistent session. Memory Search remains disabled; compaction is current-task working memory, not durable memory.

### 6B — Working set

Validated:

- 120k-row CSV processing without putting the raw dataset into model context;
- task-local writable `/task-scratch` while external data remains read-only;
- 48-image investigation with deterministic preprocessing plus bounded `view_image` confirmation;
- final claim-grade image set limited to 1–4 read-only originals;
- Fresh Finalizer re-opens/hash-validates original image Evidence;
- grouped finalizer Evidence avoids repeated metadata expansion.

### 6C — Hard budget semantics

Hard `max_requests` / turn timeout are enforced by ModelProxy/OpenClaw/Runner, not duplicated inside ScopeX Convergence.

- budget reached + existing Evidence -> Fresh Finalizer from observed facts;
- budget reached + no Evidence -> explicit failure;
- request/time budgets are per OpenClaw turn; task totals are audit metrics.

### 6D — Native loop convergence

OpenClaw native `tools.loopDetection` is enabled. ScopeX does not maintain a second result-fingerprint/stale-loop detector.

Runtime-control messages remain in Trace/Progress but are filtered out of claim-grade Evidence.

### 6E / 6F — Integrated complex task and product-default budget

Real Spark task combined:

- 120,000 telemetry rows;
- 15,000+ log lines with a historical distractor;
- 48 original images;
- a read-only recovery capability and task-local mutable device state.

The Agent correlated the current overload condition, telemetry window, safety stop and relevant images; executed recovery exactly once; then independently queried status and observed the real post-action state.

Initial capability run: about `1008.5 s / 23 requests`.

Product-default rerun:

```text
timeout = 600 s
max_requests = 16
wall time ≈ 371.1 s
forwarded requests = 14
within_product_default_budget = true
```

Correctness Gate stayed unchanged: large data remained bounded, source data stayed read-only, recovery executed once, post-action state was independently verified, and Fresh Finalizer remained valid.

**Conclusion:** Step 6 capability and product-default budget Gate are proven and frozen.

---

## Step 7 current implementation status

Step 7 is no longer only a plan. Product-answer/UI code and several real-task hardening changes now exist, but the current combined revision still needs full Spark regression before any new PASS label.

| Step | Goal | Current status |
|---|---|---|
| 7A | Constrained Product Answer over Validated Claims | **IMPLEMENTED / ACCEPTANCE PENDING** |
| 7B | Result-first UI | **IMPLEMENTED / ACCEPTANCE PENDING** |
| 7C | Real Spark FastAPI + Vue integration | **IN PROGRESS** |
| 7D | Real business product acceptance | **PENDING** |
| 7E | Offline/edge deployment baseline | **IMPLEMENTED / SMOKE PENDING** |

### 7A — Claim-bounded Product Answer

Current flow:

```text
Evidence
  -> Fresh Structured Finalizer
  -> Validated Claims
  -> deterministic trust renderer
  -> revalidation from persisted claims.json + evidence.json
  -> Product Answer
```

Product Answer currently exposes:

```text
conclusion
explanation
execution
recommendations
```

Every Answer item retains `claim_ids`. Runtime audit writes `answer.json` and includes the structured answer in `result.json`; `final.txt` remains the deterministic trust fallback.

Important current limitation:

- the `execution` section only includes claims backed by explicit `evidence_role=action_verification` provenance;
- generic `command_line` Evidence is deliberately not interpreted as “business action succeeded”.

This is safer than guessing execution semantics from shell command text, but generic action provenance remains a known follow-up.

### 7B — Result-first UI

The Vue task page now prioritizes:

1. conclusion;
2. explanation;
3. execution state;
4. recommendation;
5. expandable Progress / Evidence / deterministic fallback.

This reflects the product principle that users care first about the result. Evidence remains available for audit but no longer dominates the main page.

### 7C — Real Spark integration: findings so far

Real API/business-data runs have already produced useful failures. These failures are retained as product evidence rather than dismissed as model randomness.

#### Finding A — budget finalization could still fail inside the Finalizer

Observed real path:

```text
Agent reaches 16-request hard boundary
    ↓
existing Evidence available
    ↓
Fresh Finalizer starts
    ↓
structured JSON output hits finish_reason=length
    ↓
structured_finalizer_truncated
    ↓
Task FAILED
```

This showed that “6C hard budget semantics PASS” did not automatically prove that Finalizer serialization was robust on large real Evidence sets.

Current fix:

- Finalizer prompt requires a small Claim set;
- each Claim has a bounded number of Evidence refs;
- only `finish_reason=length` triggers one bounded no-tool retry over the **same Evidence**;
- retry asks for a shorter JSON shape and may use a larger finalizer token budget;
- `result.json` records `finalizer_retry_count`.

This is transport/serialization recovery, not a second investigation turn.

#### Finding B — single-image task could over-expand scope

Observed real behavior:

- user explicitly asked to inspect one image / use direct visual ability;
- Agent still inspected other data files and continued making model requests;
- the behavior was not a literal repeated-tool loop, so native loopDetection was not the right control.

Root cause is not proven to be one component only. The product lacked three supporting layers:

1. a generic scope/stop contract;
2. a properly provisioned built-in Skill path in the production workspace;
3. a complete common analysis toolbox, so the model did not repeatedly probe for missing packages.

Current fix keeps model autonomy but adds product constraints:

```text
explicit user target/source/scope = task boundary
        ↓
minimal sufficient evidence path
        ↓
expand only when needed to answer the original question
        ↓
stop when evidence is sufficient
```

This rule is generic to images/logs/CSV/point cloud tasks and is not a business Workflow Engine.

### Built-in Skill provisioning

Runtime API now provisions built-in repository Skills into `<workspace>/skills` before OpenClaw starts and allowlists them.

Default built-ins:

```text
cow-disinfect-diagnosis
image-quality-diagnosis
```

`image-quality-diagnosis` explicitly distinguishes direct visual inspection from optional quantitative metrics. A single explicit image task should normally inspect the specified original and stop when the visual evidence is sufficient; it should not scan sibling logs/JSON/images unless the user asks for correlation or the original scope is genuinely insufficient.

### Stable image-quality script

The image Skill includes a small deterministic metric script for explicit image paths. It does not scan directories and does not output a business root cause; it only provides objective measurements such as blur/gradient/brightness/contrast indicators.

---

## Analysis Sandbox baseline

The Step 6F image only added Pillow. Real business runs showed repeated dependency probes are a concrete usability cost, so the Step 7 image now targets a reusable offline analysis baseline:

```text
numpy
scipy
pandas
cv2
Pillow
scikit-image
matplotlib
openpyxl
PyYAML
psutil
scikit-learn
Open3D when available from the current ARM64 apt distribution
```

The image writes `/opt/scopex/toolbox.json` so actual availability is inspectable.

Build-time Ubuntu/Debian APT sources are rewritten to Tsinghua TUNA. Runtime networking remains disabled.

This is capability provisioning, not Agent-loop logic.

---

## Step 7E — edge/offline deployment baseline

Added deployment assets:

```text
docs/09-zero-to-one-build-and-offline-deployment.md
scripts/export_offline_bundle.sh
scripts/install_offline_bundle.sh
deploy/systemd/scopex-runtime.service
deploy/systemd/runtime.env.example
```

Offline bundle design:

```text
fixed-commit source archive
prebuilt frontend/dist
ARM64/Python-compatible wheelhouse
scopex-sandbox-analysis Docker image
manifest + SHA256SUMS
```

OpenClaw, vLLM and model weights are currently treated as device-base assets instead of being bundled into every ScopeX update.

Systemd is preferred for the host Runtime API because ScopeX itself launches Docker sandboxes through the host daemon; Docker-in-Docker is intentionally avoided.

---

## Remaining acceptance order

Do not branch into unrelated architecture work before these Gates are complete.

### Gate 1 — Python regression

```bash
python3 -m unittest discover -s tests -v
```

Expected: all tests pass on the current integrated revision.

### Gate 2 — frontend build

```bash
cd frontend
npm run build
```

### Gate 3 — ARM64 sandbox build

Build `scopex-sandbox-analysis:step7`, then verify all required imports and `/opt/scopex/toolbox.json`.

### Gate 4 — explicit single-image scope task

Use one known image and an explicit task boundary such as:

```text
只检查 <one image>。
直接使用视觉能力判断模糊/起雾/镜头脏污特征。
不要读取日志、JSON、其他图片或目录信息。
如果无法确认物理原因，直接说明不确定。
```

Acceptance:

- the specified original image is actually viewed;
- no excluded sibling data is read;
- no unnecessary directory-wide scan;
- task stops in a small number of model requests rather than running to the 16-request hard limit;
- uncertainty is allowed instead of endless exploration;
- final Claims/Product Answer remain traceable.

The exact request count is an observation metric, not a new hardcoded per-task workflow budget.

### Gate 5 — real complex business task

Run a non-synthetic business task through the actual FastAPI/UI path.

Acceptance includes:

- correct result;
- understandable explanation;
- trustworthy execution/verification presentation;
- Evidence traceability;
- no hidden business workflow in ScopeX;
- acceptable progress/latency behavior.

### Gate 6 — control/reconnect UX

Validate:

- Stop;
- Resume;
- Steering;
- browser refresh/reconnect;
- Evidence/result reloading.

### Gate 7 — offline deployment smoke

On an ARM64 online build host:

1. build frontend and analysis sandbox;
2. export an offline bundle;
3. verify bundle SHA256;
4. install into a new empty directory without package-network access;
5. `docker load` image;
6. start Runtime API from the offline-installed `.venv`;
7. health/smoke task;
8. verify rollback to previous release directory/image.

---

## Known gaps after this merge

These are tracked, not silently treated as solved:

- generic business action-verification provenance is not yet generalized beyond explicit Evidence metadata;
- frontend has no npm lockfile yet; offline deployment therefore ships prebuilt `frontend/dist`, while fully reproducible source rebuild remains a future cleanup;
- Open3D is optional until the current ARM64 base distribution is proven to provide a usable package;
- OpenClaw/vLLM/model are not inside the ScopeX offline update bundle;
- task-scratch retention/cleanup is still simple;
- mixed Gateway/Sandbox tasks sharing scratch still need a real-run check;
- more than four simultaneously claim-grade original images are not generalized;
- compaction remains a pressure valve, not a normal desired path.

---

## Merge / status policy

`main` is the unique current integrated baseline.

A feature may be merged to `main` as **implemented** when the integrated code/documentation baseline needs to move forward, but only real test/run evidence may upgrade it to **PASS**. This avoids both extremes:

- keeping validated Step 6 code frozen forever while product work accumulates elsewhere;
- declaring new product behavior proven merely because code was merged.

For future changes:

- use small feature branches;
- keep architecture boundary unchanged unless evidence requires change;
- record real failures and control-variable fixes;
- update handoff docs only after a meaningful stage, not every minor edit.
