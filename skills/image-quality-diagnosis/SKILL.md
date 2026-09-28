---
name: image-quality-diagnosis
description: Inspect original images for visible quality degradation, including blur, defocus, haze, dirt and droplets; compare time samples and locally verify suspected persistent problems.
user-invocable: true
---

# Image quality diagnosis

First answer whether image quality is degraded, where and how much. Then describe
visible features and possible causes. Do not force a unique physical diagnosis
when blur, haze, lighting and contamination cannot be separated from these images.

Source: `left_camera_multimodal`, `/agent-data/left-camera`, layout
`YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg`. Use originals for visual judgment.

## One or a few named images

View those JPGs directly. No directory exploration, metrics or adjacent JSON/logs
are needed unless the question or an unresolved distinction requires them.

## A time window

1. Locate only the requested window with `data-locator`. The helper samples by
   time; returned paths are not attached images and do not establish visual coverage.
2. Reserve some of the Runtime's cumulative image allowance for follow-up. With
   4 slots, first view 2 time-separated originals, then use up to 2 for broader
   coverage or local verification. With 12 slots, start with 4–6, not all 12.
   With only 1 slot, give a single-frame answer and state the limitation.
3. Inspect originals in `view_image` calls of at most 2. First assess visible
   degradation across the frame and the business subject, then compare patterns.
4. For a suspicious period or a disagreement, locate a narrower neighboring
   window and view fresh originals within the remaining allowance. Compare
   earlier/later observations to check persistence or bound a change. Do not
   reopen already-viewed images just for bookkeeping.
5. Stop when the question is supported, or state exactly which coverage is
   missing. More metric calls cannot substitute for missing visual observations.

Example first pass with a 4-image allowance (use the actual requested window):

```bash
python3 /workspace/skills/data-locator/scripts/data_locator.py \
  --source left_camera_multimodal --kind jpg \
  --start "2026-09-14 08:00:00" --end "2026-09-14 09:00:00" --max-files 2
```

The same command with a narrower start/end locates follow-up samples. Never scan
the full historical root. Keep the filename timestamps and distinguish images
located, images measured, and images actually viewed.

## Visual assessment

- **Blur / softness:** are relevant contours and fine structures distinguishable?
  Recognizing a cow or equipment does not mean edges are sharp enough.
- **Defocus / motion:** broadly soft edges versus directional streaking or doubled
  contours. Allow “blur visible, mechanism uncertain”.
- **Haze / fog-like veil:** diffuse translucent layer, washed dark regions, contrast
  loss or glare. High local sharpness elsewhere does not rule this out.
- **Droplets:** localized translucent/reflective shapes with optical distortion.
- **Contamination:** smears, spots or streaks; stable image coordinates across
  changing scenes strengthen suspicion of a lens/protective-glass issue. Dirt
  on a fixed railing is also spatially stable, so position alone is not proof.
- **Other contributors:** lighting, exposure, noise, compression, scene motion or
  occlusion. Note affected area/severity before asserting a physical cause.

Do not declare the whole image normal because a background metal edge is sharp,
or diagnose condensation solely because a veil is visible. Do not invent a fixed
business ROI; describe which part of the actual subject is affected.

## Persistence and coverage

Several degraded samples support “degradation recurs at these times”. They do not
prove every unobserved frame is degraded. Report observed times and gaps. A change
lies between the last observed state and the next different state; do not invent
an exact onset/end. If the allowance cannot locate the boundary, say so.

Normal samples support only their inspected coverage. Negative conclusions need
multiple times/scenes; one normal frame or normal metrics cannot clear an hour.

## Optional metrics

Use only when comparison would change the sampling decision:

```bash
python3 {baseDir}/scripts/image_quality_metrics.py /agent-data/left-camera/<original1>.jpg /agent-data/left-camera/<original2>.jpg
```

The result is an object with an `images` array. Brightness, contrast, Laplacian
and gradient energy are screening features, never a fog/dirt/normal classifier.
Similar numbers across samples prove neither normality nor uninterrupted damage.
No `--help`, source inspection or repeated calculation is needed for this known
interface; use explicit paths, not a large directory or a truncated JSON pipeline.

## Final answer

Give a concise Chinese assessment: visible degradation and affected areas, a few
supporting timestamps, observed recurrence/change, and material coverage limits.
Separate visible appearance from suspected cause. Give maintenance advice only
when the observations justify it; do not pad with a table of all-normal labels.

The entire conversation's image allowance accumulates across calls, including
reopens. A view reported as omitted/truncated is not a completed inspection.
Scratch previews can help screening but cannot replace original-image judgment.

## Three consecutive dirty / blurry originals (2026-09-28 user rule)

For 30 / 60 minute checks, flag an anomaly when THREE CONSECUTIVE original
images from the same camera show visible dirt or blur. This is the user's
acceptance rule, not a threshold to invent or tune for fewer reports. Metrics
alone never establish dirty/blurred appearance. Assess the relevant subject and
visible affected area; distinguish appearance from a physical cause.

First sample across the requested window using data-locator and reserve image
slots. When an original looks degraded, locate its real neighbors:

```bash
python3 {baseDir}/scripts/image_quality_window.py neighbors <original.jpg> \
  --start "2026-09-28 08:00:00" --end "2026-09-28 08:30:00"
```

Use the actual requested dates. The tool lists at most five consecutive saved
originals around the anchor, including across an hour boundary. View enough
adjacent originals to establish or refute a triple (at most two images per
view_image call, within the cumulative allowance). Nonadjacent samples,
reopening one image three times, low metric scores, missing or omitted images
must not be counted as three consecutive observations. A clear or uncertain
intervening original prevents a triple from being established. If the allowance
is insufficient, say what remains unverified, rather than infer unseen frames.

Record only the originals actually viewed in task scratch. Format example
(the dates, filenames and verdict below are examples, not actual observations):

```json
{
  "camera": "left_camera_multimodal",
  "start": "2026-09-28 08:00:00",
  "end": "2026-09-28 08:30:00",
  "observations": [
    {
      "path": "/agent-data/left-camera/20260928/08/20260928-080100000.jpg",
      "visually_reviewed": true,
      "verdict": "degraded",
      "reason": "主体边缘模糊，细节难以分辨"
    }
  ]
}
```

Verdicts: `degraded` (visible dirt or blur), `clear`, `uncertain`. Aggregate:

```bash
python3 {baseDir}/scripts/image_quality_window.py summarize /task-scratch/image-observations.json
```

The tool checks adjacency against the original camera directory, deduplicates
reopens and merges overlapping triples into one event. A run of five degraded
originals is one event, not three alarms. It scans only the named 30/60-minute
window; `--source-root` is only for an explicitly configured alternate same-camera
source, never a reason to widen/change the user's source. Before a window ends,
three adjacent frames must all lie within that window; do not borrow outside
frames to assert an in-window event.

Lead the answer with the rule outcome and each event's exact time, consecutive
frame count, visible basis and three original filenames. Then show sampling
coverage and unknown periods. `abnormal` means the user-specified visual rule
was met, not a hardware diagnosis. `insufficient_evidence` and `no_images` must
never become normal. If all stored originals were reviewed and no triple exists,
`criterion_not_met` still applies only to saved images. Business detection may
fail to save images: consecutive saved originals do not prove uninterrupted
camera capture. Report the actual saved-frame spacing; do not infer continuous
blur duration or whole-window percentages from three frames.
