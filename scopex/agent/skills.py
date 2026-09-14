from __future__ import annotations

from pathlib import Path
import re
import shutil


_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_BUILTIN_SKILLS = (
    "system-health",
    "image-quality-diagnosis",
    "nipple-recognition-analysis",
    "encoder-health",
    "log-context",
)


def prepare_workspace_skills(
    *,
    workspace: Path,
    skill_names: tuple[str, ...],
    builtin_root: Path,
) -> tuple[str, ...]:
    """Make selected skills visible in the OpenClaw workspace.

    ScopeX owns capability packaging, not skill execution. Repository built-in
    skills are copied into ``<workspace>/skills`` before OpenClaw starts; custom
    skills may already exist there and are only allowlisted, never rewritten.
    """

    workspace = Path(workspace)
    builtin_root = Path(builtin_root)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("workspace must be an existing non-symlink directory")

    ordered: list[str] = []
    seen: set[str] = set()
    skill_root = workspace / "skills"
    skill_root.mkdir(parents=True, exist_ok=True)
    if skill_root.is_symlink():
        raise ValueError("workspace skills directory may not be a symlink")

    for name in skill_names:
        if not isinstance(name, str) or _SKILL_NAME.fullmatch(name) is None:
            raise ValueError(f"invalid skill name: {name!r}")
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)

        source = builtin_root / name
        target = skill_root / name
        if source.is_dir():
            if source.is_symlink():
                raise ValueError(f"built-in skill may not be a symlink: {name}")
            if target.exists():
                if target.is_symlink() or not target.is_dir():
                    raise ValueError(f"workspace skill target is unsafe: {name}")
                shutil.rmtree(target)
            shutil.copytree(source, target)
            continue

        if not target.is_dir() or target.is_symlink():
            raise ValueError(
                f"skill {name!r} is neither a built-in skill nor present in workspace/skills"
            )

    return tuple(ordered)
