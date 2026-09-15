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
3. log-context extracts a narrow window with relevant keywords;
4. the Agent checks for reset/stop/read-error/restart evidence;
5. the Agent separates observed context from causal interpretation.

Log paths are positional arguments (there is no `--files` option):

```bash
python3 {baseDir}/scripts/log_context.py /agent-data/logs/<file> \
  --center "2026-09-14 07:21:13:420" --window-s 1 \
  --keyword EncoderVal --before 0 --after 0 --max-lines 20 --max-chars 6000
```

Choose keywords for the question; repeated keywords match ANY of them inside
the time window. For a separate reset/read-error check, query those terms over
the relevant episode. Do not mix every subsystem's high-frequency trace output.
Default output is at most 40 lines and 6000 characters, including JSON metadata.
`truncated` means incomplete coverage; `output_limited` means the character
budget removed whole rows. Narrow the time window or keywords rather than
increasing output repeatedly. Returned anchor counts are not whole-window totals.
An empty limited result is not evidence of absence. When multiple windows are
needed, inspect one result before deciding whether another is necessary.
