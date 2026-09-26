---
name: encoder-health
description: Scan a user-selected raw CowDisinfect encoder window, generate review plots, and inspect clear candidates without stopping the full-window review.
user-invocable: true
---

# Raw encoder analysis

Analyze only the number in `Get EncoderVal, raw[...]`. Forward/reverse motion,
starts and stops are allowed. Other logs can explain raw changes; `filtered`,
`TurnTableSpeed` and application values are not measurements for this task.
The user supplies the trigger interval and each non-overlapping analysis window.

## Scan once

Read this Skill once at the start of the task and reuse its procedure; do not
re-open it after every tool call. Use data-locator to resolve the explicit files
and requested window. Run:

```bash
python3 {baseDir}/scripts/encoder_health.py scan /agent-data/logs/<file1> /agent-data/logs/<file2> \
  --start "YYYY-MM-DD HH:MM:SS:000" --end "YYYY-MM-DD HH:MM:SS:000" \
  --out /task-scratch/encoder-raw.json
```

Check raw coverage, gaps and issues first. `no_raw_data` means there are no raw
samples, not normal operation; do not fall back to application readings.
`error` means no completed analysis. A candidate is a place to inspect, not a fault.
The scan covers the whole requested window and does not stop at the first candidate.

## Generate plots from the saved snapshot

Generate the overview and candidate local plots from the same saved JSON snapshot:

```bash
python3 {baseDir}/scripts/encoder_health.py plot /task-scratch/encoder-raw.json \
  --out-dir /task-scratch/encoder-plots
```

The overview has four raw-only panels: cumulative raw count, actual sample `dt`,
signed `delta_raw`, and a signed raw-derived speed using a 200 ms net-delta
window. Every candidate position is marked on the overview with a red line, and
the plotted candidates carry their ID and wall-clock time next to the line. Axes
show absolute log time and each candidate plot's title carries its time window,
so anomaly times can be read off the image directly. By default only the five
highest-severity candidates get local plots; pass `--candidate R6 --candidate R7`
to plot exactly that selection instead of the default five. Candidate plots
include bounded context and highlight the
candidate core. The speed is `(raw_end - raw_start) / actual_time * 501.144 / 4096`;
it never uses `filtered` or `TurnTableSpeed`. Missing data and blocked pairs
remain breaks. If `view_image` is available, inspect `overview.png` and the
candidate plots as working-derived visual aids. The saved raw numbers and source
lines remain the authoritative evidence; a plot cannot upgrade an unresolved
candidate. The plot command returns only compact artifact metadata (path, hash,
size and panels); do not re-read or restate the full saved plot metadata in the
conversation.

## Inspect every candidate

Inspect every returned candidate so the requested window is covered. With four
or more candidates a single batched call is required: it computes the same
reviews in one snapshot load and keeps the conversation small enough to avoid
mid-task context compaction:

```bash
python3 {baseDir}/scripts/encoder_health.py inspect /task-scratch/encoder-raw.json \
  --candidate R1 --candidate R2 --candidate R3 --candidate R4
```

A batch returns one summary line per candidate (region, times, status, check
outcomes); each review is computed and persisted exactly like a single call.
Single-candidate calls are for follow-ups on one region (paging with
`--offset`/`--limit` or widening `--context-ms`).

The result contains measured changes, exact source lines, local raw samples,
counter-log context and check outcomes. It persists the check for later reports.
Pages are dense by default (a few trace rows and context lines per call); the
saved snapshot keeps every sample, so page with `--offset`/`--limit` or widen
`--context-ms` only for a candidate that needs a closer look.
For a different view, use `--context-ms 5000` or an arbitrary captured
`--start ... --end ...` range instead of `--candidate`.
Trace is paginated raw data, not a continuous reconstructed curve; `--offset`
and `--limit` retrieve more. Inspect returns core extrema/source evidence even
when the first trace page contains only earlier context.

If needed, use log-context with the reported files and times for other relevant
log messages. Keep newly retrieved evidence distinct from the saved scan.
A reset request is not proof that reset completed. Repeated shapes do not prove
a fault; an isolated occurrence is not automatically normal.

## Decide how to describe each candidate

- `confirmed_raw_anomaly`: a supplied profile-backed range/rate check established
  an anomaly. Continue inspecting other candidates; this is not a stop signal.
- `unresolved`: inspect the fixed decision context or report the missing distinction.
  Do not promote a suspicious shape to confirmed solely by model judgment.
- `compatible_motion` / `no_anomaly_evidence`: no anomaly was established in this
  region under the implemented checks; continue through the remaining candidates.
- When no new evidence is available or the task budget ends, report the unresolved
  question. Opening a region does not mean it was resolved.

List remaining candidates or resume after context compaction. Reuse the saved
snapshot and request only the next page; do not repeat pages or embed prior plot
metadata in later prompts. Whenever the report or an inspect result shows
unreviewed candidates, its `next_action`/`hint` field already contains the
complete batched command — copy and run it as one call instead of inspecting
candidates one by one:

```bash
python3 {baseDir}/scripts/encoder_health.py report /task-scratch/encoder-raw.json
# Follow next_offset exactly; use the returned page size rather than assuming 5:
python3 {baseDir}/scripts/encoder_health.py report /task-scratch/encoder-raw.json --offset <next_offset>
```
Stop tool calls once every candidate has been inspected and the requested
coverage/quality boundaries are reported. Candidates are ordered by severity:
profile-backed checks first, then deviation size. Each candidate row carries the
absolute time window, duration, sample count, peak rate and metric multiples
(`excess` over a supplied rate limit, `deviation` from the local rate reference,
`shift` of a persistent change) next to its review status — read these before
opening plots.

A profile is optional. Without it the first version screens shape changes but
does not automatically confirm mechanical inconsistency. Read
[references/raw-contract.md](references/raw-contract.md) only when an existing,
verified device profile is available or representation/confirmation needs clarification.
Never create physical limits from the observed maximum or model guesses.

## Answer

Use the tool's times, raw values, plots and evidence. Distinguish confirmed
profile-backed checks, unresolved candidates, compatible motion and data-quality
boundaries. State the number of candidates inspected and any remaining unseen
candidates. Do not infer total frequency, periodicity, unique cause or hardware
failure from a shape candidate. If unresolved, name the missing distinction or
evidence. “No anomaly evidence in completed checks” is limited by coverage and
detector scope; it is not a health certificate.
