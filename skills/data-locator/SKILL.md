---
name: data-locator
description: Resolve CowDisinfect log and LeftCamera multimodal files for an explicit time window without recursively scanning large mounted data roots.
user-invocable: false
---

# Data locator

This is a support capability for other business Skills. It tells the Agent where CowDisinfect data lives and resolves only the files/directories relevant to a requested time window.

## Global data sources

Catalog: `/workspace/scopex-data-catalog.json`.

Stable sandbox paths:

- `cowdisinfect_logs` -> `/agent-data/logs`
  - host: `/opt/ScalingRobotics/CowDisinfect/Log`
  - files: `CowDisinfect-YYYYMMDD-HHMMSS.log[.N]`
  - files are produced per hour; one hour may have `.1/.2/...` rotation files.

- `left_camera_multimodal` -> `/agent-data/left-camera`
  - host: `/opt/ScalingRobotics/CowDisinfect/GrabbedImages/LeftCamera`
  - directories: `YYYYMMDD/HH`
  - files: `YYYYMMDD-HHMMSSmmm.jpg|json|pcd`
  - JPG/JSON/PCD with the same timestamp stem belong to the same captured result.

## Mandatory access discipline

Do not recursively enumerate `/agent-data`, `/agent-data/logs`, or `/agent-data/left-camera` for normal time-window tasks.

Avoid commands such as:

- `find /agent-data ...`
- `du -a /agent-data ...`
- `grep -R /agent-data ...`
- `rg --files /agent-data ...`

Instead use:

`{baseDir}/scripts/data_locator.py`

Examples:

```bash
python3 {baseDir}/scripts/data_locator.py --source cowdisinfect_logs --start "2026-09-14 03:00:00" --end "2026-09-14 04:00:00"
```

```bash
python3 {baseDir}/scripts/data_locator.py --source left_camera_multimodal --kind jpg --start "2026-09-14 13:00:00" --end "2026-09-14 13:30:00" --max-files 32
```

The locator is not business evidence and does not diagnose anything. It only returns a bounded set/count of relevant paths. After locating data, call the appropriate business Skill/tool.
