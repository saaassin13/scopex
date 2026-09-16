---
name: nipple-recognition-analysis
description: Compute cow-level 2D nipple recognition KPIs from CowDisinfect logs, with saved JSON/JPG only as supporting result evidence.
user-invocable: true
---

# Nipple recognition analysis

Use this skill for questions such as “7 点这个小时有多少头牛、最终识别到多少个乳头、乳头识别率是多少”。

## Product definition

- One cow is limited to **4 physical nipples**.
- Recognition count means **2D nipple detection box count** from log `NippleNum[...]`.
- A count above 4 is reported as over-detection but is capped at 4 for KPI calculation.
- **Do not use 3D nipple coordinates, `IsValid`, 3D transform success, or 3D valid-count fields to calculate recognition rate.**

## Data sources

Primary KPI source:

- `cowdisinfect_logs` -> `/agent-data/logs`
- host: `/opt/ScalingRobotics/CowDisinfect/Log`
- files: `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`

A log group may start at a non-round clock time; do not guess relevant files from natural-hour strings.

Optional supporting artifacts:

- `left_camera_multimodal` -> `/agent-data/left-camera`
- host: `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera`
- layout: `YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd`

Saved image/JSON files do not exist for every failed detection/inference path, so they are never the denominator.

## Preferred bounded path

1. Use data-locator for the requested log window:

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source cowdisinfect_logs \
  --start "2026-09-14 07:00:00" \
  --end   "2026-09-14 08:00:00"
```

2. Pass exactly the returned log files into the KPI tool:

```bash
python3 {baseDir}/scripts/nipple_stats.py \
  /agent-data/logs/<file1> \
  /agent-data/logs/<file2> \
  --start "2026-09-14 07:00:00:000" \
  --end   "2026-09-14 08:00:00:000" \
  --details-out /task-scratch/nipple-details.json
```

Only add `--artifact-dir /agent-data/left-camera` when saved JPG/JSON cross-checks are actually useful. The script will then inspect only target `YYYYMMDD/HH` directories, never the whole history tree.

Do not inspect script source first and do not use inline Python/heredoc. `--log-dir` remains a helper/test convenience; product time-window selection should prefer data-locator.

The stdout is compact `scopex_role=business_facts`; full per-cow rows stay in `/task-scratch/nipple-details.json` and should only be read for targeted follow-up.

## Final 2D result

The final 2D nipple count for one cow is not `max(NippleNum)` and not a sum across frames:

```text
Start left camera AI detect
  ImgTimeStamp = T
  CowOccuredCount = C
  DetectingNumCurRound = R
        ↓
Left camera cow [C] detecting [R] finished ... NippleNum[N]
        ↓
New cow detecte finished ... LastImgTimeStamp[T]
```

The `NippleNum[N]` belonging to `LastImgTimeStamp[T]` is the cow's final 2D result.

## Cow denominator

V1 counts unique named cow detection cycles whose first `DetectingNumCurRound` frame starts inside the requested time window.
A physical cow completely invisible to the perception system and never creating a named cow cycle cannot be recovered from this source alone; that requires independent ground truth later.

## KPIs

- `total_cows`;
- final count distribution: 4 / 3 / 2 / 1 / 0 / missing;
- `complete_four_nipple_cows`;
- `complete_four_nipple_rate`;
- `capped_2d_detections = Σ min(final_2d_count, 4)`;
- `expected_nipples = total_cows × 4`;
- `nipple_recognition_rate = capped_2d_detections / expected_nipples`;
- unfinished/missing-result/over-detection quality counters.

## Interpretation and report boundary

The stable script computes KPI facts. It should **not** manufacture a natural-language diagnosis or speculate about why recognition is low.

The investigating Agent delivers the final answer directly; no downstream Claims or report-model call is required. It should normally answer in this order:

1. this window's total cow count;
2. 4/3/2/1/0/missing final 2D distribution;
3. complete-four rate and overall nipple recognition rate;
4. important data-quality issues such as unfinished/missing/over-detection;
5. only if the user asks why, bounded follow-up around the concrete abnormal interval/cow.

For example, prefer a human result such as:

> 7:00–8:00 共统计 438 头牛，其中 400 头最终识别到 4 个乳头、38 头识别到 3 个乳头。完整四乳头率为 91.32%，总体乳头识别率为 97.83%。

Do not expose field names/JSON to the user as the main report.

## Log context and stop

Use shared `log-context` only when the user asks why a specific cow/time failed or when an abnormal interval needs explanation.
Do not scan unrelated images/logs merely because they exist. Stop after the requested KPI is supported unless a concrete anomaly requires bounded follow-up.
