# POC Baselines

POC01–POC06 are experimental evidence for ScopeX architecture decisions. They
are **not production modules**.

## Current status

| POC | Capability | Status | Product conclusion |
|---|---|---|---|
| POC01 | local model/tool baseline | historical baseline | local model + tool calling is viable; thinking=false is important |
| POC02 | native OpenClaw wire/sandbox/security preflight | PASS basis | keep OpenClaw sandbox/wire checks as the execution security basis |
| POC03 | autonomous Skill-driven investigation + fresh finalizer | PASS | separate Investigation from Finalization; runtime owns exact evidence identity |
| POC04-A | mid-task steering | PASS | same session can preserve evidence and change future investigation direction |
| POC04-B | correcting an early hypothesis | PASS | user correction can re-plan without losing prior evidence/context |
| POC05 | progress + deterministic stop + resume + re-steer | PASS control plane | progress comes from runtime events; stop is a runtime action; resume reuses session |
| POC06 | evidence-calibrated structured output | PASS | claims are structured/validated before deterministic rendering |

## Freeze rules

1. Product code under `scopex/` must not import `scripts/poc*.py`.
2. POC scripts may import one another because they preserve historical test
   harnesses; product modules must not depend on that graph.
3. A POC is changed only to repair a regression test/harness bug or preserve a
   reproducible historical result. New product functionality belongs under
   `scopex/`.
4. POC-specific semantic graders stay in POC code/tests. Generic runtime
   validators must not contain CowDisinfect, status-137, camera, robot or other
   fixture answers.
5. Local audit data remains outside Git. Do not commit real logs, images,
   secrets, model weights or production configuration.

## Removed obsolete branches

The following compatibility experiments were superseded by the validated main
POCs and were removed during Runtime MVP cleanup:

- `scripts/poc03_run_v2.py`
- `scripts/poc03_run_v3.py`
- `scripts/poc04_correction_v2.py`

Their architectural lessons are already represented by POC03–POC06 and the
runtime modules under `scopex/`.

## Regression role

POC03–POC06 remain the end-to-end regression suite for the Runtime MVP:

```text
OpenClaw investigation
  -> runtime control/events
  -> evidence catalog
  -> structured finalizer
  -> generic claim validation
  -> deterministic rendering
```

The next milestone is not another POC number. It is an integrated Runtime MVP
that reuses these proven boundaries.
