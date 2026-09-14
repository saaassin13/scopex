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

## Primary source

CowDisinfect logs are the KPI source because saved image/JSON files do not exist for every failed detection/inference path.

Use `{baseDir}/scripts/nipple_stats.py` with the relevant rotated log files and an explicit time window.

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

This prevents an earlier frame with 4 boxes from hiding a later final frame with only 2 or 3 boxes.

## Cow denominator

V1 counts unique named cow detection cycles whose first `DetectingNumCurRound` frame starts inside the requested time window.

This is a log-backed business denominator. A physical cow that is completely invisible to the perception system and never creates a named cow cycle cannot be recovered from this source alone; that requires an independent ground-truth source later (for example RFID/video/other site truth).

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

## Saved JPG / JSON

JPG and JSON are optional supporting artifacts, not the KPI denominator.

If `--artifact-dir` is available, the tool may cross-check:

- whether the selected `LastImgTimeStamp` has a saved image/JSON;
- whether saved JSON marker labels `1..4` agree with log final 2D count.

Do not infer failure only from a missing artifact unless the supplied artifact directory is known to be complete for that time window.

## Log context

The KPI script already consumes the stable business anchors needed for counting. Use the shared `log-context` Skill only when the user asks why a specific cow/time failed or when an abnormal interval needs explanation.

Do not scan unrelated logs/images merely because they exist.

## Output discipline

Separate:

1. time/log coverage and denominator definition;
2. 2D KPI facts;
3. incomplete/missing/over-detection data-quality facts;
4. artifact coverage/cross-checks when available;
5. contextual explanation only when requested or needed;
6. unknowns.

Do not turn 3D calculation failures into 2D recognition failures unless the user explicitly asks about the 3D downstream pipeline.
