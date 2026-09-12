# CowDisinfect log terms

SCOPEX_COW_DISINFECT_KNOWLEDGE_V1

This file is project background knowledge, not incident evidence.

- `CalLeftCamStartFollowPt` is the left-camera path that computes the start-follow point used by downstream robot-follow planning.
- The start-follow calculation depends on usable leg/knee geometric inputs. Missing or invalid required inputs can prevent the point from being computed.
- `LefKneeBound` / `LefLegBound` and corresponding right-side bounds describe detected image regions. An invalid, missing, negative, or out-of-range bound is evidence about that detection result; by itself it does not establish why detection failed.
- Repeated `detecting[n]` / detection-round records for the same cow represent successive observations. Later successful calculations are relevant when deciding whether an earlier failure was transient in the observed window.
- `CowDetectedCurRound`, `NippleNum`, encoder values, camera frame records, and detector timings can provide context, but only use them when they help answer the concrete diagnosis question.
- An `ERROR` level marks an event reported as an error. It does not by itself prove a component is permanently unhealthy.
- Distinguish: observed fact -> immediate trigger -> inference -> unresolved root cause. Keep those levels separate in the final diagnosis.
