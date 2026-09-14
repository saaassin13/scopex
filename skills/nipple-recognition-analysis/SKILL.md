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
- hourly files: `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`

Optional supporting artifacts:

- `left_camera_multimodal` -> `/agent-data/left-camera`
- host: `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera`
- layout: `YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd`

Saved image/JSON files do not exist for every failed detection/inference path, so they are never the denominator.

Do not recursively enumerate either mounted root. The stable script selects only the requested log hours and, when artifact checking is enabled, only the matching `YYYYMMDD/HH` directories.

## Preferred invocation

Run the existing script directly; do not inspect its source first and do not use inline Python/heredoc.

```bash
python3 {baseDir}/scripts/nipple_stats.py \
  --log-dir /agent-data/logs \
  --start "2026-09-14 07:00:00:000" \
  --end   "2026-09-14 08:00:00:000" \
  --details-out /task-scratch/nipple-details.json
```

Only add:

```bash
--artifact-dir /agent-data/left-camera
```

when saved JPG/JSON cross-checks are actually useful. The stdout is compact `scopex_role=business_facts`; full per-cow rows stay in `/task-scratch/nipple-details.json` and should only be read for targeted follow-up.

## Final 2D result

The final 2D nipple count for one cow is not `max(NippleNum)` and not a sum across frames. Resolve it through the actual consumed frame:

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

For all counted cow cycles:

- `total_cows`;
- `complete_four_nipple_cows`;
- `complete_four_nipple_rate = complete_four_nipple_cows / total_cows`;
- `capped_2d_detections = Σ min(final_2d_count, 4)`; unfinished/missing final results contribute 0 to the conservative numerator;
- `expected_nipples = total_cows × 4`;
- `nipple_recognition_rate = capped_2d_detections / expected_nipples`;
- final 2D count distribution;
- unfinished/missing-result/over-detection quality counters.

## Log context and stop

Use shared `log-context` only when the user asks why a specific cow/time failed or when an abnormal interval needs explanation.

Do not scan unrelated images/logs merely because they exist. Stop after the requested KPI is supported unless a concrete anomaly requires bounded follow-up.
