---
name: encoder-health
description: Analyze encoder sample continuity and raw-count health for missing data, unstable jumps, backsteps, large negative jumps and flat periods, then use logs to explain context.
user-invocable: true
---

# Encoder health

Use this skill when the user asks whether encoder data is missing, unstable, spiking, going backward, stuck, resetting, or otherwise abnormal.

## Primary source

Use `cowdisinfect_logs` at `/agent-data/logs`.
Host source: `/opt/ScalingRobotics/CowDisinfect/Log`.
Files use `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]` and a file group may start at a non-round clock time such as `10:23:36`, so **do not select files by natural-hour string matching yourself**.

Do **not** inspect `/agent-data/left-camera` for an encoder-only question. Do not recursively enumerate `/agent-data`.

## Preferred bounded path

1. Resolve the requested window with the existing data-locator script:

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs \
  --start "2026-09-14 03:00:00" \
  --end   "2026-09-14 04:00:00"
```

2. Pass exactly the returned log files to the encoder tool in one call:

```bash
python3 {baseDir}/scripts/encoder_health.py \
  /agent-data/logs/<file1> \
  /agent-data/logs/<file2> \
  --start "2026-09-14 03:00:00:000" \
  --end   "2026-09-14 04:00:00:000" \
  --events-out /task-scratch/encoder-events.json
```

Do not `cd ... && python`, do not use heredoc/inline Python, and do not inspect the script source first.

The stdout is compact `scopex_role=business_facts`. Full significant candidate details, when needed, go to `/task-scratch/encoder-events.json` and should only be read for targeted follow-up.

`--log-dir` remains a helper/test convenience but the product path should prefer data-locator because real hourly files need not start exactly at natural-hour boundaries.

## First-version checks

- invalid/read-failure samples under the configured raw-value rule;
- timestamp/sample gaps, including across the explicitly selected rotated files;
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

## Scope and stop

- If the user asks only whether data has gaps/backsteps, resolve the target files and run the encoder tool once.
- Do not automatically analyze cow perception, images or system load.
- Do not recursively scan mounted roots.
- Expand to small log context only for requested/necessary explanation.
- Stop when the requested encoder-health question is supported.
