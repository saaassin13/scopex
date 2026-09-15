---
name: encoder-health
description: Assess whether encoder behavior departs from normal motion, distinguishing starts, stops and rebound from suspicious spikes, reversals and sampling gaps.
user-invocable: true
---

# Encoder health

Answer whether the requested count/time data departs from normal operating patterns.
Finding negative increments or exceeding a screening threshold is not that answer.
Normal turntable operation includes acceleration, deceleration, stationary periods
and mechanical rebound. Do not require a separate “why” request to consider them.

## Data and tools

Use application `EncoderVal` first; raw/filtered are supporting streams, or the
primary stream when application data is absent. Reported speed may be derived
from these same counts, so it is not independent proof of physical motion.

Locate only the requested window in `cowdisinfect_logs` (`/agent-data/logs`):

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs --start "2026-09-14 13:00:00" --end "2026-09-14 14:00:00"
```

Analyze the returned rotation files once, using the actual requested timestamps:

```bash
python3 {baseDir}/scripts/encoder_health.py /agent-data/logs/<file1> /agent-data/logs/<file2> \
  --start "2026-09-14 13:00:00:000" --end "2026-09-14 14:00:00:000" \
  --events-out /task-scratch/encoder-events.json
```

The compact JSON contains coverage, `facts`, `top_candidates`, separate
`stationary_intervals` and interpretation limits. Candidate details include
actual dt, rates in count/s and bounded neighboring motion. Normal stops never
compete for the candidate detail slots. Counts summarize all candidates, not
only the displayed examples; they are not confirmed anomaly counts.

If more detail is necessary, query the saved events without rerunning analysis
or writing inline Python that prints every event:

```bash
python3 {baseDir}/scripts/encoder_health.py --inspect-events /task-scratch/encoder-events.json \
  --start "2026-09-14 13:22:00:000" --end "2026-09-14 13:23:00:000" --top-events 4
```

## Decide what matters

1. **Check coverage and continuity.** Missing/invalid data, reset and timestamp
   boundaries are not motion. A gap in logged samples is not proof of lost
   hardware acquisition. Zero parsed samples is not a normal result.
2. **Compare equal time scales.** Compare `delta/dt`, duration and surrounding
   motion. A larger increment over a proportionally longer interval is not a
   spike. A multi-second total drop cannot be compared directly with a normal
   single-step increment to establish abnormality.
3. **Interpret the motion episode.** Is it steady running, acceleration,
   deceleration, near-stop rebound, or an abrupt discontinuity? Neighbor summaries
   cover at most two seconds each side; expand locally if that does not include
   the full process. Group neighboring reversals/recoveries in one start/stop
   episode rather than reporting each fluctuation as an independent fault.
4. **Compare with relevant normal behavior.** Use verified site examples or
   comparable surrounding episodes. Do not invent a universal allowable rebound
   amplitude. Without a normal reference or motion command, describe the observed
   process and leave its acceptability uncertain. Do not automatically dismiss
   every stop-adjacent reversal either.
5. **Resolve meaningful candidates.** Reuse the motion context already returned;
   querying the same saved event again does not add raw samples. If the motion
   shape remains unclear, inspect one event first with `log-context`, using its
   source file and exact timestamp, `--keyword EncoderVal --before 0 --after 0
   --max-lines 20 --max-chars 6000` and a narrow `--center`/`--window-s` window.
   File paths are positional, not `--files`. A broad unfiltered window can fill
   its line budget before reaching the event. Check returned timestamps and
   truncation before interpreting it; then narrow or inspect the missing part.
   Query reset/read-error terms separately only when that distinction matters.
   Compare raw/filtered near the same event where useful. Whole-hour raw/filtered
   negative totals do not prove that a specific event propagated.

An isolated drop and catch-up is a data-glitch/reversal candidate; smooth
multi-sample reverse motion is a different pattern. `recovered` checks 80%
catch-up within a bounded elapsed-time window, stopping at the next reverse or
continuity boundary. It proves neither full physical recovery nor permanent
failure. `recovery_observed_ms` states how much was actually inspected.

Thresholds only shortlist events. Do not stop at “N candidates, please check
your equipment” if the available local context can answer the user's question.
Conversely, do not invent causes such as wiring faults to make the answer decisive.

## Deliver and stop

Lead with the result: explainable operating behavior, specific unexplained
deviations, or a concrete unresolved distinction. Separate those categories in
plain Chinese; do not call all candidates “异常” and add only “非硬件故障” later.

Show the few important episodes with time, before/after counts, actual dt/rate,
duration, local comparison, and observed recovery. Check any claimed maximum
against the whole-window summary, not just the first displayed examples. Keep
normal stationary periods as context rather than the headline anomaly.

Use count and count/s unless verified distance calibration is supplied. Include
material coverage limits once. Suggest further checks only for unresolved
questions; an encoder data check does not require pictures, broad production
tracing or repair. Stop once the requested distinction is supported or the
specific missing evidence is identified.
