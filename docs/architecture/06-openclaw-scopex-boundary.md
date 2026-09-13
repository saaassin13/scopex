# OpenClaw / ScopeX Runtime Boundary

## Purpose

This document freezes the post-POC07 architecture boundary so ScopeX does not
slowly reimplement OpenClaw.

Core rule:

> OpenClaw owns execution. ScopeX owns product control and trust.

```text
                 OpenClaw
       ┌────────────────────────┐
       │ Agent loop             │
       │ Session transcript     │
       │ Tools / Skills         │
       │ Sandbox / exec host    │
       │ Exec approvals         │
       │ progress_card          │
       └───────────┬────────────┘
                   │ trace
                   ▼
┌──────────────── ScopeX ────────────────┐
│ 1. Task Runtime                        │
│    task / stop / resume / steer        │
│                                        │
│ 2. Observation Bridge                  │
│    OpenClaw trace -> product events    │
│                                        │
│ 3. Evidence Projection                 │
│    stable E refs + minimal snapshot    │
│                                        │
│ 4. Convergence                         │
│    enough / stale / finalize           │
│                                        │
│ 5. Trust Pipeline                      │
│    Fresh Finalizer / Claim Validator   │
│                                        │
│ 6. Product                             │
│    Answer Composer / API / UI          │
└────────────────────────────────────────┘
```

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
- exec allowlist/approval semantics;
- durable progress plan state when `progress_card` is available.

If an OpenClaw capability is sufficient, ScopeX should configure, observe or
project it rather than replace it.

## ScopeX-owned state

### Task Runtime

ScopeX owns the product task lifecycle and user control operations. This is not
an OpenClaw conversation replacement.

ScopeX Session stores only product control history:

```text
USER
STEER
STOP
RESUME
```

OpenClaw remains the source of truth for the full agent/tool transcript.

### Progress

Two different concepts must remain separate:

```text
Current plan/status
-> OpenClaw progress_card when available

Historical activity timeline
-> ScopeX projection of real Tool Call / Tool Result / control events
```

ScopeX must not create a second plan state machine.

### Permissions

Commands executed through OpenClaw `exec` use OpenClaw's own host policy,
allowlist and approval mechanisms.

ScopeX permission abstractions are reserved for product/business actions outside
OpenClaw exec, for example future robot/device/config APIs.

Do not enforce the same shell command through two independent approval systems.

## Evidence is not a second transcript

OpenClaw trace answers:

> What did the agent do and see?

ScopeX Evidence answers:

> What exact immutable source is claim C3 allowed to cite?

Therefore Evidence remains necessary, but it must be a **projection**, not a
copy of the transcript.

```text
OpenClaw Trace  <- execution source of truth
      │
      │ project only claim-grade source material
      ▼
Evidence Snapshot  <- final-answer trust source of truth
```

Evidence projection rules:

1. Do not execute tools.
2. Do not infer business meaning.
3. Do not save a second full transcript.
4. Freeze only the minimum material required for stable citation.
5. Preserve provenance back to OpenClaw tool-call identity.

### Read evidence

A log/text line may be frozen directly because the underlying file may later
rotate, change or disappear.

```text
E1
kind = file_line
source = /agent/app.log
line = 123
raw = "task failed ..."
tool_call_id = ...
```

### Exec evidence

Do not copy arbitrarily large stdout into Evidence.

```text
E4
kind = command_output
host = gateway
command = "free -h"
excerpt = bounded output
result_sha256 = digest of full tool result
original_chars = ...
truncated = true|false
tool_call_id = ...
```

The complete tool result remains in OpenClaw/audit trace.

### Image evidence

The image itself is evidence. A model description of the image is not raw
image evidence.

```text
E7
kind = image
source = /agent-data/.../frame.jpg
sha256 = ...
byte_size = ...
media_type = image/jpeg
tool_call_id = ...
```

Do **not** freeze `"image is blurry"` as raw evidence merely because the
investigation Agent said it. That would make the finalizer validate one model's
claim using the same model's earlier prose.

For strong visual claims, the Fresh Finalizer must receive the immutable image
itself and inspect it again.

## Fresh Finalizer rule

Text evidence can be passed as a compact evidence directory.

Image evidence must be re-attached to the Fresh Finalizer from the configured
read-only host data root, and its current SHA-256 must match the Evidence
snapshot before the finalizer sees it.

If the image changed or can no longer be resolved, finalization must not silently
use the earlier Agent description as a substitute.

vLLM's OpenAI-compatible chat endpoint supports multimodal `image_url` content,
including base64 data URLs. ScopeX may use that protocol in the Fresh Finalizer;
this is transport, not a new image-analysis tool.

## Renderer / Answer Composer split

The current deterministic renderer remains the trust-safe fallback and audit
view. It should not become the only end-user response format.

Target flow:

```text
Evidence
  -> Fresh Finalizer
  -> Validated Claims
  -> deterministic trust rendering
  -> constrained Answer Composer
```

`Claims` define what may be said. `Answer Composer` controls how it is said.
The composer must not introduce uncited new factual or causal claims.

Until the composer exists, deterministic rendering remains authoritative.

## Budget convergence

Avoid independent duplicated budget sources.

Hard budgets should have one configuration source and be propagated to the
components that enforce them:

```text
timeout
model-request count
exec timeout
model/context limits
```

ScopeX Convergence should increasingly focus on product-level stopping signals:

```text
goal satisfied
no new claim-grade evidence
stale rounds
finalize requested / hard budget reached
```

Do not build a second token/context estimator when OpenClaw/vLLM already expose
usable context-budget information.

## ModelProxy status

The local ModelProxy currently remains because it provides validated product
behaviour that OpenClaw does not yet expose to ScopeX in an equally stable hook:

- deterministic Stop/Steer boundary before the next model request;
- exact request count budget;
- wire audit used by current regression tests.

Freeze its scope. Do not turn it into a second model gateway with routing,
fallback, prompt rewriting, caching or tool injection.

If OpenClaw later exposes stable before-request/cancel hooks covering these
requirements, remove the proxy rather than expanding it.

## POC07 implementation sequence after boundary freeze

### Step 5A — Trace -> Evidence Projection

- replace the production `ReadLineExtractor` plugin path with one generic
  OpenClaw Evidence Projector;
- project `read`, `exec`, and `view_image` provenance only;
- retain legacy extractor classes only for compatibility/regression until no
  production path depends on them;
- do not project `progress_card` into Evidence.

### Step 5B — Multimodal Fresh Finalizer

- re-resolve image Evidence only through configured read-only data binds;
- verify SHA-256 before finalization;
- attach image bytes to the existing Fresh Finalizer request;
- validate visual facts against image E refs;
- never substitute investigation-Agent prose for missing image bytes.

### Step 6 — Budget convergence cleanup

- collapse hard budget configuration;
- remove duplicated context-char budget when OpenClaw context status is usable;
- keep stale/no-new-evidence convergence in ScopeX.

### Step 7 — End-user answer composition

- keep Validated Claims authoritative;
- add a constrained natural-language Answer Composer;
- preserve exact Evidence refs and epistemic strength;
- keep deterministic rendering available as audit/trust view.

## Remaining uncertainty

Only implementation details remain, not architectural uncertainty:

1. Maximum useful image count for one Fresh Finalizer request with the current
   served Qwen/vLLM configuration must be measured on Spark.
2. A stable OpenClaw API for reading the current durable `progress_card` outside
   the transcript should be verified before ScopeX depends on it for refresh
   recovery.

Neither blocks Step 5A. Step 5B can be implemented behind strict integrity
checks and then validated with the existing three-image POC07 fixture before it
is marked PASS.
