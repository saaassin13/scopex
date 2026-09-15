from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any


WORKSPACE_CATALOG_NAME = "scopex-data-catalog.json"
LOCATOR_CATALOG_RELATIVE = Path("skills/data-locator/references/data-catalog.json")


def load_data_catalog(path: Path) -> dict[str, Any]:
    path = Path(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("data catalog must be a schema=1 object")
    sources = value.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("data catalog sources are required")
    for name, row in sources.items():
        if not isinstance(name, str) or not name:
            raise ValueError("data source name must be a non-empty string")
        if not isinstance(row, dict):
            raise ValueError(f"data source {name!r} must be an object")
        host_path = row.get("host_path")
        agent_path = row.get("agent_path")
        if not isinstance(host_path, str) or not host_path.startswith("/"):
            raise ValueError(f"data source {name!r} host_path must be absolute")
        if not isinstance(agent_path, str) or not agent_path.startswith("/") or agent_path == "/":
            raise ValueError(f"data source {name!r} agent_path must be an absolute non-root path")
    return value


def provision_workspace_catalog(*, workspace: Path, catalog_path: Path) -> Path:
    """Keep a host-side workspace copy for audit/operator visibility."""
    workspace = Path(workspace)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("workspace must be an existing non-symlink directory")
    target = workspace / WORKSPACE_CATALOG_NAME
    if target.exists() and target.is_symlink():
        raise ValueError("workspace data catalog target may not be a symlink")
    shutil.copyfile(catalog_path, target)
    return target


def provision_locator_catalog(*, workspace: Path, catalog_path: Path) -> Path:
    """Copy the machine-readable catalog into the data-locator Skill.

    OpenClaw guarantees the selected Skill directory is available inside the
    sandbox as ``/workspace/skills/...``. It does not guarantee arbitrary files
    placed at the workspace root are visible there, so the locator must not
    depend on ``/workspace/scopex-data-catalog.json``.
    """
    workspace = Path(workspace)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("workspace must be an existing non-symlink directory")
    skill_root = workspace / "skills" / "data-locator"
    if not skill_root.is_dir() or skill_root.is_symlink():
        raise ValueError("data-locator skill must be provisioned before its catalog")
    references = skill_root / "references"
    references.mkdir(parents=True, exist_ok=True)
    if references.is_symlink():
        raise ValueError("data-locator references directory may not be a symlink")
    target = workspace / LOCATOR_CATALOG_RELATIVE
    if target.exists() and target.is_symlink():
        raise ValueError("data-locator catalog target may not be a symlink")
    shutil.copyfile(catalog_path, target)
    return target


def catalog_binds(catalog: dict[str, Any], *, existing_only: bool = True) -> tuple[str, ...]:
    binds: list[str] = []
    seen_targets: set[str] = set()
    for row in catalog["sources"].values():
        host = Path(str(row["host_path"])).expanduser().resolve()
        target = str(row["agent_path"])
        if existing_only and not host.is_dir():
            continue
        if target in seen_targets:
            raise ValueError(f"duplicate data catalog agent_path: {target}")
        seen_targets.add(target)
        binds.append(f"{host}:{target}:ro")
    return tuple(binds)


def render_runtime_catalog_summary(catalog: dict[str, Any]) -> str:
    lines = [
        "Available ScopeX data sources. These are semantic locations, not an invitation to recursively scan roots.",
        "Use the named source and requested time window; prefer /workspace/skills/data-locator/scripts/data_locator.py for bounded file resolution.",
    ]
    for name, row in catalog["sources"].items():
        description = str(row.get("description") or "").strip()
        lines.append(f"- {name}: {row['agent_path']} ({row.get('type', 'data')})")
        if description:
            lines.append(f"  {description}")
        layout = row.get("layout")
        if isinstance(layout, dict):
            if layout.get("directory"):
                lines.append(f"  directory layout: {layout['directory']}")
            if layout.get("filename"):
                lines.append(f"  filename: {layout['filename']}")
            if layout.get("timezone"):
                lines.append(f"  timestamp timezone: {layout['timezone']}")
        access = row.get("access")
        if isinstance(access, dict):
            limits = []
            for key in (
                "max_hour_buckets",
                "max_files_per_operation",
                "max_claim_images",
                "max_pointcloud_files_per_operation",
            ):
                if access.get(key) is not None:
                    limits.append(f"{key}={access[key]}")
            if limits:
                lines.append("  bounded access: " + ", ".join(limits))
    lines.append("Do not use recursive find/grep/du over these mounted roots for ordinary time-window tasks.")
    return "\n".join(lines)
