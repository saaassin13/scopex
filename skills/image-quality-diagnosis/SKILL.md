---
name: image-quality-diagnosis
description: Inspect local images for blur, haze or fogging, lens contamination, motion blur, exposure and other image-quality problems with bounded visual evidence.
user-invocable: true
---

# Image quality diagnosis

Use this skill when the user asks whether one or more images are blurry, fogged, dirty, contaminated, poorly exposed, motion-blurred, defocused, or otherwise visually degraded.

## Scope first

- Treat the user's explicit target and scope as binding. If the user names one image, a short list of images, or says to use visual inspection only, inspect those original images directly and do not enumerate or read sibling logs, JSON files, directories, or unrelated data by default.
- Expand to neighboring images or other data only when the user asked for cross-source correlation, or when the requested cause cannot be distinguished from the named images alone. Keep any expansion minimal and directly relevant.
- Do not perform environment/package discovery for a direct visual task. The ScopeX analysis sandbox already provides the common offline analysis toolbox.

## Visual diagnosis discipline

- For one or a few named images, prefer `view_image` on the read-only originals before derived metrics or scripts.
- Separate **what is visibly present** from **why it may be present**.
- Blur/low sharpness can be observed visually; the exact cause may still be uncertain.
- Fogging/condensation is better supported by diffuse veiling haze, broad contrast loss, halos/glare, or a milky layer. A single image normally cannot prove physical condensation by itself; state it as consistent with or unlikely unless stronger evidence exists.
- Lens dirt/contamination is better supported by localized smears, spots, streaks or blobs fixed in image coordinates across different scene content. One image may not distinguish lens dirt from an object in the scene.
- Motion blur is better supported by directional streaking or subject/camera motion patterns. Defocus is usually more isotropic and edge softness is more uniform.
- Exposure problems, sensor noise, compression artifacts, occlusion and scene illumination can imitate poor sharpness or haze; keep these as alternatives when supported.
- Do not turn common causes into observed facts without direct evidence.

## Quantitative helper

For batch screening, comparison, or when the user explicitly wants numerical quality indicators, use:

`{baseDir}/scripts/image_quality_metrics.py`

The helper only reports bounded objective metrics such as Laplacian variance, gradient energy, brightness, contrast and clipping ratios. It does not diagnose a root cause. Pass explicit image paths; do not feed an entire directory by default.

For a direct one-image visual question, the helper is optional and should not replace visual inspection.

## Stop condition

Stop using tools once the requested image(s) have been directly inspected and the requested judgement can be stated with calibrated uncertainty. More unrelated evidence is not automatically better evidence.

If the final conclusion depends on image content, keep the claim-grade visual working set small and use the original read-only images, not only scratch-derived contact sheets or previews.
