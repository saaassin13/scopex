---
name: encoder-health
description: Assess encoder motion over a requested window, distinguishing allowed forward/reverse motion, stops, rebound and counter resets from off-trend jumps and unusual motion processes.
user-invocable: true
---

# Encoder observation and motion diagnosis

Decide first whether the supplied data contains unexplained deviations, then keep
that observation separate from whether the motion matched a command and from any
physical fault diagnosis. Forward motion, sustained reverse motion, stops, rebound,
and resetting the counter to zero followed by renewed accumulation can all be
valid operations. A possible valid explanation prevents unsupported fault
attribution; it does not prove that a materially unusual observation is normal.

## Analyze once

Resolve explicit hourly/rotated logs using data-locator for the requested window.
Then run the motion report (substitute the actual paths and timestamps):

```bash
python3 {baseDir}/scripts/encoder_health.py /agent-data/logs/<file1> /agent-data/logs/<file2> \
  --start "YYYY-MM-DD HH:MM:SS:000" --end "YYYY-MM-DD HH:MM:SS:000" \
  --motion-report --events-out /task-scratch/encoder-motion.json
```

The report includes coverage, process count and three prioritized processes.
Normal starts/stops are included, not labeled faults. Omitted processes still
exist; use `episode_summary` and aggregate facts before answering for the whole
window. The shortlist is not exhaustive evidence that the rest is normal.

## Interpret the evidence

- Check invalid samples and gaps first. Do not interpret discontinuous coverage
  as mechanical movement or certify an empty window as normal.
- `counter_boundaries` marks possible resets, including low counts reaching zero.
  Resetting and accumulating again, or staying stopped at zero, are allowed.
  Exclude the cross-boundary difference from reverse displacement/speed. A
  rapid drop then jump back to the old trend is different: inspect off-trend
  evidence. Smooth reverse travel to zero can also occur; counts alone do not
  prove a reset. Insufficient follow-up is uncertainty, not an anomaly.
- `off_trend_return_counts` identifies an isolated point leaving and rejoining a
  locally consistent trend. It is stronger evidence of a transient recording
  deviation than a large increment alone, but does not identify hardware cause.
- Each process groups reversals and nearby starts/stops/rate transitions, with
  context before and after. Inspect count/time order, duration, drawdown and
  negative-lobe amplitudes. Say the shape supports rebound only when
  `rebound_supported=true`. `lobes_decreasing=false` forbids describing that
  process as diminishing oscillation or normal rebound.
- A persistent forward rate transition is also included. Determine whether its
  shape supports an ordinary start/change of speed or an unexplained discontinuity.
- Inspect `reported_speed_mm_s`, signed-delta extrema, significant reverse and
  positive-spike counts, direction changes, and raw/filtered context. These are
  observable deviation evidence, not hidden device limits or proof of root cause.
- `motion_pattern` describes observed direction/shape, not the commanded
  operating mode. Never compare long reverse travel against small rebound and
  call the larger displacement abnormal. Reverse distance/duration are descriptive
  only; the script no longer scores them as statistical faults. Off-trend
  comparisons, when available, are unverified references, not hardware limits.
- Diagnose observable data problems (gaps, invalid readings, isolated off-trend
  jumps, and motion materially unlike the nearby stable behavior). Whether a
  smooth forward/reverse/stop action was intended requires command/business-state
  evidence. Absence of that evidence is not a fault, but it also cannot establish
  normality; use an unresolved distinction when intent changes the answer.
- The 2000ms grouping/context horizon is an analysis setting, not a business
  threshold. `context_complete=false` means a boundary prevents that context;
  even true does not prove the full physical start/stop cycle is captured.
- Trace is a full-span min/max envelope, not a prefix. It preserves endpoints and
  bin extrema but can omit fine oscillation and must not be treated as every sample.
- Raw/filtered context covers this process, not whole-hour totals. Those streams
  and reported speed are related measurements, not independent mechanical proof.

## Resolve a specific uncertainty

If the initial trace is insufficient, request the relevant process ID. It returns a
more detailed full-span trace from saved data without scanning logs again:

```bash
python3 {baseDir}/scripts/encoder_health.py \
  --inspect-events /task-scratch/encoder-motion.json --episode M1
```

Use an ID actually returned. Do not re-query the old candidate list or repeatedly
shrink raw-log windows. Do not use inline Python, grep or sed to dump the saved
event file or raw encoder trace. The motion report includes bounded follow-up observations for counter
boundaries; distinguish a return toward the previous level from continued low
counts, and treat insufficient follow-up as unresolved; a hardware cause is outside this data-only check. Query additional IDs only for distinct unresolved questions that could change the
conclusion. Do not repeat an unchanged query. Once available evidence resolves the
question, or further queries provide no new information, answer with any remaining
uncertainty.

## Answer

Lead with observed unexplained deviations, behavior consistent with ordinary
operation, or a specific unresolved distinction. Report the important process
times, measured shape and comparison evidence. Use time + phenomenon in the
answer; M IDs are optional lookup references, not fault codes. Group one motion
process once. Do not headline "significant anomaly" and then admit the only
evidence is a possibly normal reverse journey or reset. Do not certify omitted
processes as normal.
Do not equate candidate counts with anomaly counts or diagnose hardware from
these counts alone. Use counts and counts/s unless calibration is verified.

Match the conclusion to the user's question:

- For whether the **encoder data contains abnormal behavior**, multiple strong
  local deviations, rapid direction changes, or corroborating raw/filtered
  changes may establish an abnormal observed pattern even when the physical
  cause is unresolved.
- For whether motion was **expected** or the device is **faulty**, require command,
  business-state, or verified operating-limit evidence. Material deviations with
  missing intent evidence require review; they are not normal by default.
- Use normal only when coverage is sufficient and the requested scope has no
  material unexplained deviation. A possible normal explanation or a three-item
  shortlist is insufficient.

A clean shortlist does not certify all motion as normal: slow drift, faulty
same-window references and unobserved processes remain limitations. Mention only
limitations that materially affect this task. No images or repair are required.
