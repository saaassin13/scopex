---
name: encoder-health
description: Check raw CowDisinfect encoder readings for data anomalies, inspect local evidence progressively, and stop once one anomaly is confirmed.
user-invocable: true
---

# Raw encoder analysis

Analyze only the number in `Get EncoderVal, raw[...]`. Forward/reverse motion,
starts and stops are allowed. Other logs can explain raw changes; filtered and
application values are not alternative measurements for this task.

## Scan once

Use data-locator to resolve the explicit files and requested window. Run:

```bash
python3 {baseDir}/scripts/encoder_health.py scan /agent-data/logs/<file1> /agent-data/logs/<file2> \
  --start "YYYY-MM-DD HH:MM:SS:000" --end "YYYY-MM-DD HH:MM:SS:000" \
  --out /task-scratch/encoder-raw.json
```

Check raw coverage, gaps and issues first. `no_raw_data` means there are no raw
samples, not normal operation; do not fall back to application readings.
`error` means no completed analysis. A candidate is a place to inspect, not a fault.

## Inspect one question at a time

Start with a returned candidate ID:

```bash
python3 {baseDir}/scripts/encoder_health.py inspect /task-scratch/encoder-raw.json --candidate R1
```

The result contains measured changes, exact source lines, local raw samples,
counter-log context and check outcomes. It persists the check for later reports.
For a different view, use `--context-ms 5000` or an arbitrary captured
`--start ... --end ...` range instead of `--candidate`.
Trace is paginated raw data, not a continuous reconstructed curve; `--offset`
and `--limit` retrieve more. Inspect returns core extrema/source evidence even
when the first trace page contains only earlier context.

If needed, use log-context with the reported files and times for other relevant
log messages. Keep newly retrieved evidence distinct from the saved scan.
A reset request is not proof that reset completed. Repeated shapes do not prove
a fault; an isolated occurrence is not automatically normal.

## Decide whether to continue

- `confirmed_raw_anomaly`: source-backed range/rate checks established an anomaly.
  For “is there an anomaly?”, stop inspecting later blocks and get the report.
- `unresolved`: inspect more relevant context or compare another candidate.
  Do not promote a suspicious shape to confirmed solely by model judgment.
- `compatible_motion` / `no_anomaly_evidence`: no anomaly was established in this
  region under the implemented checks; continue if other candidates remain.
- When no new evidence is available or the task budget ends, report the unresolved
  question. Opening a region does not mean it was resolved.

List remaining candidates or resume after context compaction:

```bash
python3 {baseDir}/scripts/encoder_health.py report /task-scratch/encoder-raw.json
# Follow next_offset to retrieve another candidate page:
python3 {baseDir}/scripts/encoder_health.py report /task-scratch/encoder-raw.json --offset 5
```

A profile is optional. Without it the first version screens shape changes but
does not automatically confirm mechanical inconsistency. Read
[references/raw-contract.md](references/raw-contract.md) only when an existing,
verified device profile is available or representation/confirmation needs clarification.
Never create physical limits from the observed maximum or model guesses.

## Answer

Use the tool's times, raw values and evidence. State at least one confirmed
anomaly when established, and that later candidates were not individually checked.
Do not infer total frequency, periodicity, unique cause or hardware failure from
that stopping point. If unresolved, name the missing distinction/evidence.
“No anomaly evidence in completed checks” is limited by coverage and detector
scope; it is not a health certificate. Continue beyond the first confirmed block
only for a user request about all events, frequency, impact or cause.
