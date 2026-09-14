---
name: system-health
description: Analyze the current DGX Spark CPU, memory, disk, GPU, Docker and process state from a ScopeX host snapshot.
user-invocable: true
---

# System health

Use this skill when the user asks about the device's **current** CPU, memory, disk, GPU, Docker/container load, or important processes.

## Data boundary

- ScopeX Agent normally runs in a sandbox. Never use sandbox-local `/proc`, `free`, `top`, `df`, `nvidia-smi` or Docker data as if they described the Spark host.
- The only authoritative current-host source is `/scopex-host/current.json`, produced on the host immediately before this task starts and mounted read-only.
- ScopeX V1 does **not** continuously collect CPU/memory/disk/GPU history. If the user asks about a past host-resource state, explain that historical resource data is unavailable.
- If `/scopex-host/current.json` is missing, malformed, stale for the user's purpose, or contains a collector error for the requested field, report that host metric as unavailable. Do not fall back to sandbox-local measurements.

## Investigation discipline

- If the user asks only for one resource, such as current disk usage, read only the relevant field and stop.
- High utilization alone does not prove a business fault. Separate current observed load from causal interpretation.
- Do not automatically inspect business logs, images or encoder data just because they exist.
- Do not restart processes, containers or services unless a separate allowed recovery capability explicitly permits it.

## Output

Prefer a compact human-readable result, for example:

- 当前磁盘：已用 62.4%，剩余 1.42 TB；
- 当前内存：可用 36.8 GB / 128 GB；
- 当前 GPU：利用率 74%，显存 21.4 / 96 GB；
- 数据采样时间：...；
- 不可用字段及 collector error（如有）。

The raw JSON remains supporting Evidence; the user-facing answer should explain the values instead of dumping field names.
