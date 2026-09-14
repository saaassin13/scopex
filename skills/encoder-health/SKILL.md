---
name: encoder-health
description: Detect real encoder sampling gaps, local pulse spikes, reverse/glitch events and unstable count behavior from CowDisinfect logs, then use bounded log context only when explanation is needed.
user-invocable: true
---

# Encoder health

Use this skill when the user asks whether encoder values contain **毛刺、回退、异常跳变、丢采样、卡住或不稳定**.

The goal is not to count every negative delta. The goal is to identify locally abnormal count behavior and show the concrete time/count context of the real candidates.

## Primary source

Use `cowdisinfect_logs` at `/agent-data/logs`.
Host source: `/opt/ScalingRobotics/CowDisinfect/Log`.
Files use `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`; use `data-locator` for the requested time window instead of guessing files by hour string.

Do **not** inspect `/agent-data/left-camera` for an encoder-only request. Do not recursively enumerate `/agent-data`.

## Preferred bounded path

1. Resolve the requested window:

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs \
  --start "2026-09-14 03:00:00" \
  --end   "2026-09-14 04:00:00"
```

2. Analyze all returned rotated logs **once**:

```bash
python3 {baseDir}/scripts/encoder_health.py \
  /agent-data/logs/<file1> \
  /agent-data/logs/<file2> \
  --start "2026-09-14 03:00:00:000" \
  --end   "2026-09-14 04:00:00:000" \
  --events-out /task-scratch/encoder-events.json
```

Do not use `cd && python`, heredoc, inline Python, or inspect the analyzer source before running it.

## What counts as a useful anomaly candidate

Use signed count increments and nearby normal increments. Do not label every decrease as an anomaly.

- **reverse_glitch_candidate**: an isolated significant count decrease followed by near-term catch-up/recovery. This is a strong data-glitch/reverse candidate, not proof of physical reversal.
- **reverse_interval_candidate**: two or more consecutive significant negative increments. This proves a reverse-count interval in the data, not its physical cause.
- **reverse_step_candidate**: one significant decrease without confirmed short recovery.
- **positive_spike_candidate**: a positive increment unusually large relative to nearby positive increments.
- **sampling_gap**: a timestamp/sample gap above the data-driven threshold.
- **flat_count_candidate**: count unchanged for a long period. This may be a normal stop and is not automatically counted as an encoder anomaly.

Small negative groups below the local data-driven threshold are telemetry only (`small_negative_groups_ignored`). A number such as `negative_steps_observed=1000` by itself is **not** the answer to “编码器是否异常”.

## Application count vs raw/filtered

CowDisinfect may contain both:

- `EncoderVal [N], TurnTableSpeed [V mm/s]` — application cumulative count and reported speed;
- `Get EncoderVal, raw[R], filtered[F]` — lower-layer raw and filtered values.

Prefer the application cumulative count as the primary business stream when available. Use raw/filtered as supporting evidence to see whether a low-level anomaly is filtered or propagated. If application count is absent, analyze raw count as the primary stream.

Do not convert counts to physical distance unless a verified site calibration is supplied.

## Interpretation discipline

- A negative delta is an observed count decrease, not automatically a hardware fault.
- An isolated decrease plus recovery is more informative than a raw negative-step total.
- Several consecutive decreases indicate a reverse-count interval; check task stop/reset/actual reverse context before assigning cause.
- A large positive increment immediately after a reverse glitch may be the recovery leg and should not be double-counted as a separate spike.
- Compare anomalies against nearby normal increments and actual sampling intervals, not only a global absolute threshold.
- Keep observed data facts separate from cause hypotheses.

## Log context

Only after the compact summary identifies a meaningful candidate, use `log-context` over a small window around the candidate when the user asks why it happened or whether it affected the business.

Relevant context may include encoder reset, Modbus/read errors, task stop, restart or actual reverse motion. Finding an anomaly alone neither requires broad production tracing nor authorizes repair.

## User-facing result

Lead with a direct answer such as:

- 本时间窗采样连续，未发现明显毛刺/回退/异常跳变；或
- 发现 3 个显著异常事件：2 个回退-恢复毛刺、1 个连续回退区间。

Then show only the important event details:

- 时间；
- 前值 / 当前值 / 后续恢复；
- delta / 持续时间；
- 与附近正常增量的对比；
- 是否 raw 与 filtered 同时出现。

Do not lead with thousands of small negative-step counts or dump the full event JSON.
