---
name: system-health
description: Inspect the current DGX Spark host CPU, memory, disk, GPU, Docker and process state through an explicit host-snapshot capability.
user-invocable: true
---

# System health

Use this skill when the user asks about the device's **current** CPU, memory, disk, GPU, Docker/container load, or important processes.

## Data boundary

- ScopeX Agent normally runs in a sandbox. Do **not** use sandbox-local `/proc`, `free`, `top`, `df`, `nvidia-smi` or Docker data as if they described the DGX Spark host.
- V1 does **not** continuously collect CPU/memory/disk/GPU history and does not provide historical resource analysis.
- Host facts must come from a dedicated current-host snapshot/capability owned by ScopeX. If that capability is unavailable, report `宿主机资源状态当前不可用` and stop; never fall back to sandbox metrics.
- A collection error means the metric is unavailable, not that the host is healthy or unhealthy.

## Investigation discipline

- If the user asks only for one resource (for example current disk free), inspect only that resource and stop.
- If the user asks about a historical time such as “3 点 CPU 是否过高”, state that resource history is not retained by ScopeX V1. Do not use current load to explain the past.
- High utilization alone does not prove a business fault. Separate observed current load from causal interpretation.
- Do not restart processes, containers or services unless a separate allowed recovery capability explicitly permits it.

## Output

Prefer a compact human-readable result, for example:

- current timestamp;
- CPU/load if requested;
- memory available if requested;
- filesystem free/used if requested;
- GPU utilization/memory/temperature if requested;
- important process/container facts if requested;
- unavailable fields and collection errors.

Do not expose raw JSON as the headline result when the same facts can be stated clearly in natural language.
