---
name: encoder-health
description: Analyze encoder sample continuity and raw-count health for missing data, unstable jumps, backsteps, large negative jumps and flat periods, then use logs to explain context.
user-invocable: true
---

# Encoder health

Use this skill when the user asks whether encoder data is missing, unstable, spiking, going backward, stuck, resetting, or otherwise abnormal.

## Primary source

The encoder raw sample stream is primary evidence. For the current CowDisinfect logs, `{baseDir}/scripts/encoder_health.py` recognizes:

`Get EncoderVal, raw[...], filtered[...]`

The tool deliberately reports **candidate events and measurements**, not business root causes.

## First-version checks

- invalid/read-failure samples under the configured raw-value rule;
- timestamp/sample gaps;
- observed negative raw jumps;
- large negative jump candidates;
- statistically unusual positive delta candidates;
- long unchanged raw periods;
- raw-vs-filtered divergence summary.

Do not automatically label a large negative jump as reset, a negative jump as encoder damage, or a flat period as stall.

## Threshold discipline

Historical scripts contain different assumptions and thresholds. Treat the current CLI defaults as an explicit analysis profile, not universal hardware truth.

In particular:

- `invalid_min` must ultimately come from the real protocol/firmware semantics;
- `large_negative_pulses` is only a candidate separator;
- data-driven MAD outliers identify unusual deltas, not physical impossibility;
- no speed or distance conversion is performed in V1, so `pulses_per_mm` and physical speed limits are not silently assumed.

When a site/firmware-specific encoder profile is confirmed, pass those values explicitly and document them.

## Log context

For each important candidate, use `log-context` over a small window around the event only when explanation is needed. Relevant context may include task stop, encoder reset, Modbus/read errors, restart, actual reverse motion or other lifecycle events.

Example reasoning boundary:

- raw decreases at 07:21:13 = observed fact;
- `ResetEncoderValOnSerialPort` at the same window = observed context;
- “this backstep was caused by reset” is a causal conclusion and should only be stated when the event ordering/semantics support it.

## Scope and stop

- If the user asks only whether data has gaps/backsteps, run the encoder tool and stop once answered.
- Do not automatically analyze cow perception, images or system load.
- Expand to logs or other capabilities only for requested/necessary explanation.
