---
name: cow-disinfect-diagnosis
description: Diagnose CowDisinfect camera/robot log failures with adjacent-frame evidence, recovery checks, and explicit uncertainty.
user-invocable: true
---

# CowDisinfect evidence diagnosis

SCOPEX_DIAGNOSIS_SKILL_V1

Use this skill for CowDisinfect camera, detection, StartFollowPt, nipple/leg geometry, and robot-follow log diagnosis.

Before deciding what an event means, read `{baseDir}/references/cow-disinfect-log.md` for the project terms and evidence rules.

## Investigation discipline

- Treat the original log as evidence. Skill/reference text is background knowledge, never proof that an event happened.
- Start from the reported symptom, then trace only enough nearby upstream/downstream records to establish the immediate trigger and whether the system later recovered.
- For non-trivial logs, search before bulk reading. Locate symptom/recovery anchors and line numbers first, then inspect small nearby windows. Do not read the whole file by default when targeted search can answer the question.
- Keep tool results compact. Prefer the smallest evidence window that preserves causal order; avoid overlapping or repeated reads of content already inspected.
- After the immediate trigger, failure record, later recovery/persistence state, and important unknowns are supported, stop investigating and produce the requested answer. Additional unrelated context is not evidence quality.
- Compare adjacent detection rounds or frames before calling a fault persistent. A single ERROR record or one failed frame is not sufficient evidence of a persistent hardware/software fault.
- Separate the immediate trigger visible in the log from the deeper root cause. If the log only proves an invalid/missing detection input, say that; do not invent why the detection became invalid.
- Do not conclude camera damage, network failure, hardware failure, calibration failure, or AI-model failure unless the inspected evidence directly supports it.
- Prefer exact raw log lines with timestamps and source locations so the operator can verify every conclusion.
- Use available read/exec tools autonomously. Choose commands and investigation depth from the evidence; there is no fixed command sequence.
- Stop when the direct trigger, persistence/recovery state, important unknowns, and confidence are supported. Do not keep exploring unrelated subsystems.
