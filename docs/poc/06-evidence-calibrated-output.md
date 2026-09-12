# POC06 — Evidence-Calibrated Output

## Goal

Validate one capability only:

> Convert already-observed investigation evidence into claims whose epistemic
> strength is explicit and runtime-checkable before user-facing rendering.

POC06 does **not** rerun OpenClaw, tools, Stop/Resume, or autonomous
investigation. Those are already covered by POC03–POC05.

## Why this POC exists

Free-form final answers repeatedly exposed a class of errors that cannot be
reliably solved with Chinese-text regexes:

- `status=137` was upgraded to `OOM` without direct evidence.
- a robot log window with no observed fault was upgraded to “robot fully
  healthy / robot excluded”.
- temporal adjacency was upgraded to “direct cause”.
- quoted or negated statements were misclassified by text graders.

The fix is to make epistemic structure primary and prose secondary.

## Architecture

```text
stored investigation tool evidence
        |
        v
Evidence Catalog (E1...En)
        |
        v
fresh no-tool structured finalizer
        |
        v
claims[]
  - kind
  - relation
  - scope
  - confidence
  - evidence_refs
  - topic
        |
        v
Generic Runtime Validator
        |
        +--> POC-specific semantic grader
        |
        v
Deterministic Runtime Renderer
```

## Claim schema

```json
{
  "id": "C1",
  "kind": "fact|inference|unknown",
  "topic": "short label",
  "evidence_refs": ["E1"],
  "confidence": "high|medium|low|unknown",
  "scope": "event|time_window|component|global|unknown",
  "relation": "observed|temporal_association|causal_hypothesis|unknown"
}
```

### Generic validation rules

These are runtime rules and contain no CowDisinfect / POC06 fixture answer:

- `fact`
  - requires at least one valid evidence ref;
  - relation must be `observed`;
  - confidence must be `high|medium`.
- `inference`
  - requires evidence;
  - relation must be `temporal_association|causal_hypothesis`;
  - confidence must be `medium|low`;
  - temporal association requires at least two evidence refs.
- `unknown`
  - relation must be `unknown`;
  - confidence and scope must be `unknown`.
- evidence refs must exist in the runtime-owned catalog.
- malformed model output returns validation errors; it must not crash the
  validator.

## Important rendering rule

The model's `topic` does **not** become fact prose.

For `fact`, runtime renders the exact evidence lines referenced by the claim.
For `temporal_association`, runtime renders a fixed sentence that explicitly
states that temporal association does not prove causation.

This prevents a model from producing:

```json
{
  "kind": "fact",
  "topic": "GPU OOM caused the worker exit",
  "relation": "observed",
  "evidence_refs": ["E9"]
}
```

and smuggling that unsupported causal statement into the final user-visible
fact. Runtime ignores that topic for fact rendering and expands E9 exactly.

`topic` is only user-visible for `unknown` and `causal_hypothesis`, where the
renderer already labels the epistemic status explicitly.

## POC-specific grade

The fixture-specific grader is intentionally separate from the generic runtime
validator. For the current stored POC05 evidence it requires:

- the `status=137` event represented as an observed fact;
- the app failure represented as an observed fact;
- their relationship represented only as `temporal_association` inference;
- robot evidence scoped to `time_window`;
- the status-137 trigger mechanism represented as `unknown`;
- no `global` claim;
- no `causal_hypothesis` for this fixture.

Changing this POC grader must not weaken the generic validator.

## Pass condition

```text
PASS_POC06_EVIDENCE_CALIBRATION
```

Requires all of:

1. stored POC05 control mechanics remain eligible;
2. fresh finalizer ends normally with `finish_reason=stop`;
3. JSON parses;
4. generic validator returns zero errors;
5. POC-specific semantic grade passes;
6. deterministic renderer produces the final answer.

## Expected product conclusion

If POC06 passes, ScopeX should keep this responsibility split:

```text
Model:
  evidence interpretation + epistemic classification

Runtime:
  evidence identity + schema validation + scope/strength constraints
  + deterministic rendering of facts / temporal relationships
```

Do not return to post-hoc free-text regex grading as the primary safety layer.
