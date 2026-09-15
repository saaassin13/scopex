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
