---
name: image-quality-diagnosis
description: Inspect local images for blur, haze or fogging, lens contamination, motion blur, exposure and other image-quality problems with bounded visual evidence.
user-invocable: true
---

# Image quality diagnosis

Use this skill when the user asks whether one or more images are blurry, fogged, dirty, contaminated, poorly exposed, motion-blurred, defocused, or otherwise visually degraded.

## Data source

The normal LeftCamera source is:

- `left_camera_multimodal` -> `/agent-data/left-camera`
- host: `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera`
- directories: `YYYYMMDD/HH`
- files: `YYYYMMDD-HHMMSSmmm.jpg|json|pcd`

For a time-window request, first use `data-locator` to resolve only the requested hour/window. Do not recursively enumerate the full historical LeftCamera root.

## Scope first

- Treat the user's explicit target and scope as binding.
- If the user names one image, inspect that original directly. Do not read adjacent logs/JSON/PCD or unrelated files by default.
- For a time window, use a bounded temporal sample across the window. Do not inspect every image by default.
- Do not perform package/environment discovery for a direct visual task.

## Visual diagnosis discipline

Visual inspection is authoritative for visible haze/fog/contamination. Numerical sharpness metrics are supporting screening only.

- **Fogging / condensation features:** diffuse veil, milky/translucent layer, broad low-frequency contrast loss, washed blacks, halos/glare or a persistent hazy layer over otherwise different scenes.
- **Lens contamination:** localized smears, spots, streaks or blobs that remain fixed in image coordinates across different scene content.
- **Defocus:** broadly isotropic edge softness without a translucent veil.
- **Motion blur:** directional streaking or directional duplicated edges.
- Exposure/noise/compression can imitate poor image quality and should remain alternatives when appropriate.

Important negative-claim rule:

- A normal/high Laplacian variance does **not** prove “no fog”. Fog can preserve many edges while adding a veil or contrast loss.
- Do not conclude “no fogging or contamination” from metrics alone.
- For a time-window negative conclusion, directly inspect at least two original images with different scene content and no consistent haze/contamination pattern. If visual evidence is mixed, say uncertain rather than forcing a negative conclusion.
- If any directly inspected original clearly shows a diffuse veil/milky haze, report **visible fogging/haze features are present**. The exact physical cause (condensation, dirty protective glass, lighting) may remain uncertain.

## Claim-grade image discipline

Call `view_image` with **at most 2 original images per call**. The current OpenClaw visual bridge can omit images from larger batches; an omitted image was not actually inspected and must not support a claim.

For a time-window task, inspect several samples in repeated <=2-image calls when needed. Final claim-grade evidence should be the smallest directly inspected original set that supports the conclusion.

## Quantitative helper

For batch screening/comparison, use:

`{baseDir}/scripts/image_quality_metrics.py`

It reports Laplacian variance, gradient energy, brightness, contrast and clipping ratios. These metrics help find relative changes or candidate frames; they do not diagnose fogging or dirt and must not override a clear visual haze observation.

Pass explicit image paths from `data-locator`; do not feed an entire directory by default.

## Stop condition

Stop once the requested images/window has enough directly inspected visual evidence for a calibrated answer. Do not expand into unrelated logs/JSON/PCD merely because they are available.
