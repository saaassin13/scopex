---
name: data-locator
description: Resolve CowDisinfect log and LeftCamera multimodal files for an explicit time window without recursively scanning large mounted data roots.
user-invocable: false
---

# Data locator

This is a support capability for other business Skills. It tells the Agent where CowDisinfect data lives and resolves only the files/directories relevant to a requested time window.

## Global data sources

ScopeX keeps one source catalog in the repository and provisions the machine-readable sandbox copy at:

`/workspace/skills/data-locator/references/data-catalog.json`

A separate host-workspace copy may exist for operator/audit visibility, but the locator does not depend on arbitrary workspace-root files being mounted into the sandbox.

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

The locator is not business evidence and does not diagnose anything. It only returns a bounded set/count of relevant paths.

For a fixed-time check, `status=no_data` / `matching_count=0` ends the current run:
state that no matching data was found for the source and exact window, then finish.
Do not widen/shift the window, search other roots, poll or retry unless the user
explicitly requested that fallback. `source_unavailable` means the source could
not be accessed, not an empty healthy dataset; report the access problem and finish.
Future scheduled triggers are unaffected. Only when paths are found, call the
business tool; if it finds zero records in the requested window, likewise finish
with no data, never a normal diagnosis. Parse failures are errors, not no-data proof.
