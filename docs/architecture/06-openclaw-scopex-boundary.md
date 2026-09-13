# OpenClaw / ScopeX Runtime Boundary

## Purpose

This document freezes the architecture boundary so ScopeX does not slowly
reimplement OpenClaw.

> **OpenClaw owns execution. ScopeX owns product control and trust.**

> **OpenClaw + 模型负责自主调查、决策、执行和验证；我们只提供能力、权限、上下文和审计，不重新实现 Agent Loop。**

> **Evidence 模型只解决“Agent 为什么这么做”的可信依据；绝不能把系统架构收缩成 Evidence Viewer。**

## Ownership

```text
Goal / Trigger
    ↓
┌──────────── OpenClaw + Model ────────────┐
│ Agent Loop                               │
│ investigation order                     │
│ file / exec / process / image tools     │
│ Skills                                   │
│ tool-loop detection / recovery           │
│ compaction                               │
│ action + verification decision           │
└──────────────────┬───────────────────────┘
                   │ trace
                   ▼
┌──────────────── ScopeX ──────────────────┐
│ Task / Session / Stop / Resume / Steer   │
│ hard product runtime boundary            │
│ Trace -> Evidence projection             │
│ permission / capability boundary         │
│ Fresh Finalizer / Claim Validator        │
│ Audit / API / UI                         │
└──────────────────────────────────────────┘
```

Litmus test:

- if ScopeX code contains lots of `Agent must do X next`, the architecture is
  drifting into a second Agent;
- capability, permission, risk, Evidence, audit and product lifecycle code is
  appropriate ScopeX responsibility.

## What must stay in OpenClaw

ScopeX must not implement a second version of:

- generic Shell execution;
- file search/read semantics;
- process management;
- image viewing;
- model-driven investigation order;
- Skill loading;
- full assistant/tool transcript;
- sandbox lifecycle semantics already provided by OpenClaw;
- native tool-loop detection/recovery;
- current-task compaction;
- exec allowlist/approval semantics already provided by OpenClaw.

If an OpenClaw capability is sufficient, ScopeX configures, observes or projects
it rather than replacing it.

## ScopeX-owned product state

ScopeX Task/Session state is product control history, not a conversation clone.
It stores only what the product must own, such as:

```text
USER
STEER
STOP
RESUME
Task state / audit state
```

OpenClaw remains the source of truth for the full Agent/tool transcript.

## Progress

Keep two concepts separate:

```text
Agent's current execution / tool activity
-> OpenClaw trace, optionally progress_card

Historical product timeline
-> ScopeX projection of real Tool Call / Tool Result / control events
```

ScopeX must not create a second plan state machine.

## Permissions and actions

Commands executed through OpenClaw `exec` use OpenClaw's tool/sandbox policy.
ScopeX permission abstractions are for product/business capabilities outside that
shell surface, for example future robot/device/config APIs.

Do not enforce one action through two unrelated approval engines.

A successful command is not the same thing as successful recovery:

```text
execute action
   ↓
query independent business state
   ↓
verify expected outcome
   ↓
only then claim recovery
```

Step 6E validated this pattern end to end.

## Evidence is not a second transcript

OpenClaw Trace answers:

> What did the Agent do and see?

ScopeX Evidence answers:

> What exact immutable source is a final claim allowed to cite?

```text
OpenClaw Trace
      │
      │ project only claim-grade source material
      ▼
Evidence Snapshot
      │
      ▼
Fresh Finalizer / Validated Claims
```

Projection rules:

1. Do not execute tools.
2. Do not infer business meaning.
3. Do not copy the full transcript.
4. Freeze only enough material for stable citation.
5. Preserve provenance back to tool-call identity.
6. Runtime-control feedback is not business Evidence.

### Read Evidence

Freeze exact bounded source lines. A rotating log may change later, so the
observed raw line belongs in Evidence.

### Exec Evidence

One command can report several independent facts. Freeze bounded non-empty
output lines as separate E refs and retain one digest for the complete tool
result.

### Image Evidence

The original image is Evidence; the investigation Agent's description is not.
Strong visual claims require the Fresh Finalizer to re-open the original from a
configured read-only data root and re-check its SHA-256.

Large image sets are an investigation working set. A final `view_image` call of
1–4 read-only originals may promote those originals to claim-grade Evidence.
Scratch-derived contact sheets/previews are useful for investigation but are not
strong original-image Evidence.

### Runtime-control feedback

OpenClaw may append warning/block/recovery text to Tool Results, for example
native loop-detection messages. Those lines stay in Trace/Progress but must not
become claim-grade Evidence for the device/system being diagnosed.

## Fresh Finalizer

Target trust flow:

```text
Evidence
  -> Fresh Finalizer
  -> Validated Claims
  -> deterministic trust rendering
  -> constrained Answer Composer
```

`Claims` define what may be said. The future Answer Composer controls how it is
said and must not add new uncited factual/causal claims.

Until the composer is complete, deterministic rendering remains the trusted
fallback/audit view.

## Context, working set and memory

Context window is working memory, not storage.

- raw logs / CSV / image collections stay in read-only external data roots;
- the Agent uses tools and `/task-scratch` to create bounded intermediate
  results;
- OpenClaw native compaction handles current-task context pressure;
- Memory Search remains disabled until a real cross-task recall requirement
  exists.

Step 6A/6B validated this split.

## Budget vs convergence

Keep the concepts separate:

```text
Budget
= this constrained resource may not be consumed further

Convergence
= the product has a reason the investigation no longer needs to continue
```

Hard request/time budgets have one enforcement path through ModelProxy /
OpenClaw / Runner. ScopeX does not independently estimate those same budgets in
Convergence.

When a hard limit is reached:

```text
Evidence exists     -> Fresh Finalizer from observed facts
No Evidence         -> explicit failure
```

Native repeated-tool convergence belongs to OpenClaw `tools.loopDetection`.
ScopeX does not maintain a second result-fingerprint detector; it only maps the
observed OpenClaw terminal shape to product finalization when necessary.

## ModelProxy scope

The local ModelProxy remains because it currently provides stable product hooks
not exposed equivalently by OpenClaw:

- deterministic Stop/Steer boundary before the next model request;
- exact per-turn model request budget;
- wire audit used by regression and performance profiling.

Freeze its scope. Do not add routing, fallback orchestration, prompt rewriting,
business tool injection or caching. If OpenClaw later exposes stable equivalent
hooks, remove the proxy rather than expanding it.

## Validated Step 6 checkpoint

- **6A PASS** — native compaction + structured state retention;
- **6B PASS** — task scratch + large-data/multi-image bounded working set;
- **6C PASS** — hard-budget single source + graceful evidence-grounded exit;
- **6D PASS** — OpenClaw native loop convergence + runtime-control Evidence filtering;
- **6E CAPABILITY PASS** — multi-source diagnosis + constrained action + independent recovery verification.

This proves complex-task capability. It does **not** yet prove acceptable product
latency.

## Next work

Step 6F usability optimization remains on an experiment branch until the same
6E task passes within the current 600 s / 16-request product budget. After that,
Step 7 is the constrained Answer Composer and result-first UI.

The authoritative order, acceptance conditions and known non-blocking gaps are
maintained in [07-complex-task-validation-and-next-plan.md](07-complex-task-validation-and-next-plan.md).
