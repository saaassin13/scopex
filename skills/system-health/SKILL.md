---
name: system-health
description: Analyze DGX Spark CPU, memory, disk, GPU, Docker and process load from current or historical host resource snapshots.
user-invocable: true
---

# System health

Use this skill when the user asks about device load, CPU, memory, disk, GPU, Docker/container load, or whether host resource pressure coincided with a business problem.

## Data boundary

- ScopeX Agent normally runs in a sandbox. Do **not** use sandbox-local `/proc`, `free`, `top`, `df`, `nvidia-smi` or Docker data as if they described the Spark host.
- Host resource facts come from the read-only history mounted at `/scopex-system-metrics/system_metrics.jsonl`.
- For a time window, use `{baseDir}/scripts/system_health_summary.py` over that history.
- The host collector records facts only. A collector error means the metric is unavailable, not that the device is healthy or unhealthy.

## Investigation discipline

- If the user asks only for one resource (for example disk free), answer from that resource and stop.
- If the user names a historical time, inspect the matching resource window first. Do not use current load to explain a past event.
- Only correlate resource pressure with image/perception/encoder/log anomalies when their time windows actually overlap.
- High utilization alone does not prove a business fault. Separate observed load from causal interpretation.
- Use `log-context` only when resource data shows a relevant window or the user explicitly asks what the system was doing then.
- Do not restart processes, containers or services unless a separate allowed recovery capability explicitly permits it.

## Output

Prefer a compact result:

- time window and sample coverage;
- CPU/load, memory available, disk free, GPU utilization/memory/temperature as available;
- important top processes/containers from the latest relevant snapshot;
- collector gaps/errors;
- whether a business-impact claim is observed, only temporally correlated, or still unknown.
