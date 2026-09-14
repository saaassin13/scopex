---
name: log-context
description: Extract small, auditable raw log windows around a time or keyword for other business skills; it does not independently diagnose root cause.
user-invocable: false
---

# Log context

This is a shared supporting skill for system-health, nipple-recognition-analysis, encoder-health and other future business skills.

Use `{baseDir}/scripts/log_context.py` to retrieve bounded raw log evidence around:

- an exact event time;
- a start/end window;
- one or more keywords;
- a small number of surrounding lines.

## Boundaries

- This skill retrieves context; it does **not** decide business root cause.
- Prefer explicit log files. Do not recursively enumerate every log on the device unless the user asked for broad discovery.
- Start from the anomaly/time found by the primary business capability, then inspect a small nearby window.
- Preserve line number, timestamp and raw text so the conclusion can be audited.
- If there is no anchor, report that instead of widening the window indefinitely.
- Do not read an entire large log by default.

## Typical use

Encoder candidate at `07:21:13.420`:

1. encoder-health reports an observed negative jump;
2. log-context extracts roughly ±5 seconds around that timestamp;
3. the Agent checks for reset/stop/read-error/restart evidence;
4. the Agent separates observed context from causal interpretation.

The same pattern applies to perception-rate drops or host-load anomalies.
