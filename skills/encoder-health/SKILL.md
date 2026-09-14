---
name: encoder-health
description: Analyze encoder sample continuity and raw-count health for missing data, unstable jumps, backsteps, large negative jumps and flat periods, then use logs to explain context.
user-invocable: true
---

# Encoder health

Use this skill when the user asks whether encoder data is missing, unstable, spiking, going backward, stuck, resetting, or otherwise abnormal.

## Primary source

Use the global data source `cowdisinfect_logs`:

- sandbox path: `/agent-data/logs`
- host path: `/opt/ScalingRobotics/CowDisinfect/Log`
- files: `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`
- files are produced per hour and an hour may contain `.1/.2/...` rotation files.

Do **not** inspect `/agent-data/left-camera` for an encoder-only question. Do not recursively enumerate `/agent-data`.

The stable tool recognizes:

`Get EncoderVal, raw[...], filtered[...]`

and analyzes the whole requested time window across all relevant rotated logs in one call.

## Preferred invocation

For a time-window request, directly run the existing script. Do not `cd ... && python`, do not use heredoc/inline Python, and do not inspect the script source first.

```bash
python3 {baseDir}/scripts/encoder_health.py \
  --log-dir /agent-data/logs \
  --start "2026-09-14 03:00:00:000" \
  --end   "2026-09-14 04:00:00:000" \
  --events-out /task-scratch/encoder-events.json
```

The stdout is intentionally compact `scopex_role=business_facts`. Full candidate details, when needed, go to `/task-scratch/encoder-events.json` and should only be read for specific follow-up investigation.

## First-version checks

- invalid/read-failure samples under the configured raw-value rule;
- timestamp/sample gaps, including across rotated-file boundaries;
- count/distribution of observed negative raw changes;
- statistically unusual negative-jump candidates;
- configured large negative-jump candidates;
- statistically unusual positive-delta candidates;
- long unchanged raw periods;
- raw-vs-filtered divergence summary.

Do not automatically label a raw decrease as encoder damage or a flat period as stall.

Small negative changes are summarized statistically rather than emitted one-by-one. A high `negative_jump_count` alone is not enough to call the encoder abnormal; inspect magnitude distribution and significant candidates.

## Threshold discipline

Historical scripts contain different assumptions and thresholds. Treat current CLI defaults as an explicit analysis profile, not universal hardware truth.

- `invalid_min` must ultimately come from real protocol/firmware semantics;
- `large_negative_pulses` is only a configured candidate separator;
- MAD outliers identify unusual deltas, not physical impossibility;
- no speed/distance conversion is performed in V1, so physical motion limits are not silently assumed.

## Log context

Only after the encoder summary identifies an important candidate, use `log-context` over a small window around that event when explanation is needed. Relevant context may include task stop, encoder reset, Modbus/read errors, restart or actual reverse motion.

Example boundary:

- raw decreases at 07:21:13 = observed fact;
- reset-related log event in the same small window = observed context;
- “this backstep was caused by reset” remains a causal conclusion and needs ordering/semantics support.

## Scope and stop

- If the user asks only whether data has gaps/backsteps, run the encoder tool once for the requested window and answer from the compact facts.
- Do not automatically analyze cow perception, images or system load.
- Do not recursively scan mounted roots.
- Expand to small log context only for requested/necessary explanation.
- Stop when the requested encoder-health question is supported.
