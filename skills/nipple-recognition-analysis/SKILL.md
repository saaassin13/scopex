---
name: nipple-recognition-analysis
description: Compute cow-level nipple recognition coverage for a requested time window from inference JSON, then use logs only when needed for missing/abnormal context.
user-invocable: true
---

# Nipple recognition analysis

Use this skill for questions such as “7 点这个小时有多少头牛、每头识别到几个乳头、完整识别率和乳头识别率是多少”。

## Primary source

Inference JSON is the primary source. Logs are supporting context, not the KPI source by default.

Use `{baseDir}/scripts/nipple_stats.py` with explicit schema mappings:

- timestamp field;
- cow identity field(s);
- nipple count/list field;
- optional final/selected marker;
- explicit per-cow selection policy.

Do not guess a JSON field name from business terminology. Inspect a small representative JSON first when the mapping is not already documented.

## Cow-level aggregation

A cow may have multiple inference records. KPI calculation must first reduce records to one business result per cow.

Selection policy must match confirmed data semantics:

- `selected`: use when JSON has a reliable final/selected marker;
- `latest`: use when the latest record is the actual consumed business result;
- `max`: only use when the business meaning is explicitly “best observed recognition during the cow cycle”. If this is only a provisional diagnostic convention, say so in the result.

Never sum all frame-level nipple detections as though they were different physical nipples.

## Coverage before KPI

Keep two denominators separate:

- `total_cows`: unique cow keys represented by a valid timestamp + cow identity in the JSON window;
- `cows_with_selected_result`: cows for which the configured policy produced a usable nipple result.

Always surface `selected_result_coverage_rate` and `cows_without_selected_result`. A KPI must not silently look better just because cows with missing/bad final results disappeared from the denominator.

Completely missing cows that generated **no JSON record at all** still cannot be discovered from JSON alone. If an independent log/RFID/business source later provides the actual passed-cow count, report that as a separate cross-source coverage metric instead of pretending JSON observed it.

## First-version KPIs

For the selected record of each cow:

- exact four-nipple cows = selected nipple count exactly 4;
- `complete_four_nipple_rate` uses all JSON-observed cows as the conservative denominator; a cow without usable selected result is therefore not counted as complete;
- `nipple_recognition_rate = sum(min(selected count, 4)) / (total JSON-observed cows × 4)`;
- selected-only variants are also exposed for diagnosis, but must be shown together with result coverage;
- count > 4 is reported separately as over-detection and must not inflate recognition rate above 100%;
- distribution of selected nipple count is always shown.

These definitions are product metrics, not proof of why recognition failed.

## Log context

Use `log-context` only when needed, for example:

- JSON has a gap or malformed/missing records;
- a cow is represented in JSON but has no usable final result;
- a time slice shows a meaningful recognition drop;
- the user asks what happened around a particular cow/time;
- you need to distinguish inference absence from task stop/restart/camera/runtime events.

Do not scan a whole log just because it exists.

## Output discipline

Separate:

1. data coverage / mapping / selection policy;
2. KPI facts;
3. abnormal cows/time ranges;
4. log-backed contextual explanation, if requested/needed;
5. unknowns.

If the real JSON schema or selection semantics are not confirmed, report that limitation rather than silently manufacturing a production KPI.
