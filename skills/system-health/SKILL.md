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
- If the current snapshot is missing, malformed, stale for the user's purpose, or contains a collector error for the requested field, report that host metric as unavailable. Do not fall back to sandbox-local measurements.

## Preferred bounded path

Do **not** read the raw snapshot line-by-line for a normal resource question. Use the stable helper once:

```bash
python3 {baseDir}/scripts/system_health.py
```

The helper reads `/scopex-host/current.json` and emits one compact `scopex_role=business_facts` object containing current CPU, memory, root-disk and GPU summary fields. This keeps User Facts small and avoids turning the JSON source into hundreds of Evidence lines.

Only inspect the raw snapshot after the compact helper when a specific field or collector error needs targeted follow-up.

## Investigation discipline

- If the user asks only for one resource, such as current disk usage, use the compact snapshot and answer only that resource.
- High utilization alone does not prove a business fault. Separate current observed load from causal interpretation.
- Do not automatically inspect business logs, images or encoder data just because they exist.
- Do not restart processes, containers or services unless a separate allowed recovery capability explicitly permits it.

## Output

Prefer a compact human-readable result, for example:

- 当前 CPU：利用率 11.2%；
- 当前内存：已用 57.7 GB，可用 64.0 GB；
- 当前磁盘：已用 49.7%，剩余 1.98 TB；
- 当前 GPU：利用率 74%，显存 ...；
- 数据采样时间：...；
- 不可用字段及 collector error（如有）。

Do not dump the full host JSON into the product answer or User Facts panel.
