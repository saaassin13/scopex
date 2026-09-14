---
name: image-quality-diagnosis
description: Inspect local images for blur, haze/fogging, lens contamination, water droplets, motion blur and defocus with bounded multi-image visual evidence.
user-invocable: true
---

# Image quality diagnosis

Use this skill when the user asks whether one image or a time window contains blur, fogging/haze, dirty lens/protective glass, water droplets, motion blur, defocus or other visible degradation.

## Business source

- `left_camera_multimodal` -> `/agent-data/left-camera`
- host: `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera`
- layout: `YYYYMMDD/HH/YYYYMMDD-HHMMSSmmm.jpg|json|pcd`

Use `data-locator` for time-window requests. Do not recursively enumerate historical roots.

## Core rule: visual judgement owns the result

**The final dirty/blur/fog/water-droplet judgement must come from direct visual inspection of original JPGs.**

Laplacian, gradient energy, brightness, contrast and clip ratios are optional screening/reference features only. They may help divide a large image set into different-looking groups, but none of them may independently decide:

- “no fog”;
- “no contamination”;
- “image is normal”; or
- the physical cause of degradation.

A high Laplacian can coexist with a translucent veil; a low-contrast frame can be caused by scene content rather than fog. Never turn those metrics into a product conclusion by themselves.

## Single-image flow

If the user names one or a few images:

1. inspect the original JPG directly with `view_image`;
2. judge the visible dimensions below;
3. use metrics only if they materially help compare sharpness/exposure;
4. stop when the visual question is answered.

Do not inspect adjacent JSON/log/PCD unless the user requests cross-source explanation.

## Time-window / many-image flow

For a large set such as one hour:

1. **Locate only the requested window.**
2. **Build a bounded representative screening set.** Prefer temporal coverage across beginning/middle/end. If metrics are useful, compute them only on a bounded sample.
3. **Use metrics only to partition/reference the sample**, e.g. lower/median/higher sharpness, lower/normal contrast, or different time sections. Do not choose only the “worst Laplacian” images.
4. **Select original images from multiple partitions/time sections** so that visual inspection covers different scenes and both normal-looking and suspicious candidates.
5. **Directly inspect originals in repeated small `view_image` calls (<=2 images per call).**
6. Compare across images and decide whether the visible problem is isolated or persistent.
7. Stop after enough representative originals support a calibrated answer.

The goal of screening is coverage and de-duplication, not automated diagnosis.

## What to judge visually

For each directly viewed representative image, distinguish:

- **blur / low sharpness:** edges and fine structures are visibly soft;
- **fogging / haze:** diffuse translucent or milky veil, washed blacks, broad contrast loss, halos/glare, persistent hazy layer;
- **water droplets:** localized droplet-like translucent/reflective shapes, often with optical distortion;
- **lens/protective-glass contamination:** localized smear, spot, streak or blob; stronger evidence when the pattern stays at the same image coordinate across different cows/scenes;
- **motion blur:** directional streaking/duplicated edges;
- **defocus:** more isotropic softness without a veil;
- **exposure/lighting/noise/compression:** alternative explanations when visually supported.

Separate **visible observation** from **physical cause**. For example, “明显雾化/veil” can be observed; “一定是冷凝水” normally requires stronger evidence.

## Negative conclusion is harder than positive detection

For a time window, do not conclude “no fog/no dirt” after seeing one normal frame or from metrics alone.

A negative conclusion requires direct visual coverage across different times/scenes. If representative images disagree, report mixed/uncertain quality rather than forcing a global normal result.

If one or more directly inspected originals clearly show a persistent diffuse veil or droplets, report that visible feature even if sharpness metrics look normal.

## Claim-grade visual evidence

- Use **at most 2 original images per `view_image` call**.
- If the visual tool reports any image was omitted/truncated/not placed in context, that call is incomplete and must not support a claim; re-open the needed originals in a smaller call.
- Scratch contact sheets/previews may help screening but cannot replace final original-image inspection.

## Optional metric helper

`{baseDir}/scripts/image_quality_metrics.py`

It is a screening helper only. Pass explicit bounded paths; never feed an entire large directory by default.

## Expected result structure

The business result should answer:

1. whether visible blur/fog/contamination/water droplets are present in the inspected window;
2. whether the issue appears persistent or isolated across representative originals;
3. the key visual facts supporting that judgement;
4. uncertain physical causes as possibilities, not facts;
5. what additional inspection is needed only when current visual evidence is insufficient.
