---
name: log-context
description: Extract small, auditable raw log windows around a time or keyword for other business skills; it does not independently diagnose root cause.
user-invocable: false
---

# Log context

This is a shared supporting skill for nipple-recognition-analysis, encoder-health and other future business skills.

Primary CowDisinfect log source:

- `/agent-data/logs`
- host `/opt/ScalingRobotics/CowDisinfect/Log`
- hourly files `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`

When the exact rotated file is not already known, use `data-locator` for the small target time window first. Do not recursively enumerate the log root.

Use `{baseDir}/scripts/log_context.py` to retrieve bounded raw log evidence around:

- an exact event time;
- a start/end window;
- one or more keywords;
- a small number of surrounding lines.

## Boundaries

- This skill retrieves context; it does **not** decide business root cause.
- Prefer explicit log files returned by the primary business tool or data-locator.
- Start from the anomaly/time found by the primary business capability, then inspect a small nearby window.
- Preserve line number, timestamp and raw text so the conclusion can be audited.
- If there is no anchor, report that instead of widening the window indefinitely.
- Do not read an entire large log by default.
- Do not use `grep -R`, recursive `find`, or directory-wide scans over `/agent-data`.

## Typical use

Encoder candidate at `07:21:13.420`:

1. encoder-health reports an observed significant candidate;
2. data-locator resolves only the relevant CowDisinfect rotated log(s) when needed;
3. log-context extracts roughly ±5 seconds around that timestamp;
4. the Agent checks for reset/stop/read-error/restart evidence;
5. the Agent separates observed context from causal interpretation.
