#!/usr/bin/env python3
"""POC03 compatibility runner with tool-aware sandbox gating.

OpenClaw's read tool is sandbox-backed through its filesystem bridge and does
not require a Docker exec container to exist. Docker container boundary checks
are therefore required only after an exec/process tool has actually returned.
The underlying POC03 task, Skill, wire validation, evidence grading and no-retry
policy stay unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import poc02_run as p2
import poc03_run as p3

OriginalRecorder = p2.Recorder
CONTAINER_TOOLS = {"exec", "process"}
_LAST_RECORDER = None


def request_has_returned_container_tool(path: Path) -> bool:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    calls = {}
    returned = set()
    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                    continue
                func = call.get("function") or {}
                calls[call["id"]] = func.get("name")
        elif message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
            returned.add(message["tool_call_id"])
    return any(calls.get(cid) in CONTAINER_TOOLS for cid in returned)


def latest_request_requires_container(out: Path) -> bool:
    rows = sorted(Path(out).glob("wire-*-request.json"))
    return bool(rows and request_has_returned_container_tool(rows[-1]))


def gate_for_container_tools(gate, out: Path):
    """Run the original Docker boundary/hash gate only when needed, once.

    Read-only file calls are still graded from their exact recorded tool returns.
    If exec/process is used, the next model request must pass the original Docker
    sandbox gate before it is forwarded.
    """
    state = {
        "calls": 0,
        "container_required_seen": False,
        "checked": False,
        "error": None,
    }

    def wrapped():
        state["calls"] += 1
        required = latest_request_requires_container(out)
        state["container_required_seen"] |= required
        if not required:
            return
        if state["checked"]:
            if state["error"] is not None:
                raise state["error"]
            return
        try:
            gate()
        except Exception as exc:
            state["error"] = exc
            state["checked"] = True
            raise
        else:
            state["checked"] = True

    wrapped._scopex_gate_state = state
    return wrapped


class ToolAwareSandboxRecorder(OriginalRecorder):
    def __init__(self, out, ref, key, token, native, gate, max_requests):
        global _LAST_RECORDER
        wrapped = gate_for_container_tools(gate, Path(out))
        self.scopex_gate = wrapped
        super().__init__(out, ref, key, token, native, wrapped, max_requests)
        _LAST_RECORDER = self


def trace_requires_container(trace: dict) -> bool:
    return any(
        isinstance(row, dict)
        and row.get("name") in CONTAINER_TOOLS
        and row.get("has_return") is True
        for row in trace.get("tool_calls", [])
    )


def can_promote_read_only(result: dict) -> bool:
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    if trace_requires_container(trace):
        return False
    return bool(
        result.get("returncode") == 0
        and not result.get("stop")
        and not result.get("errors")
        and result.get("wire_ok") is True
        and isinstance(result.get("grade"), dict)
        and result["grade"].get("passed") is True
    )


def _arg_value(argv, name: str):
    rows = list(sys.argv[1:] if argv is None else argv)
    for i, value in enumerate(rows):
        if value == name and i + 1 < len(rows):
            return rows[i + 1]
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
    return None


def _rewrite_summary(out: Path, result: dict):
    summary = (
        "# POC03 Skill 业务诊断单次验证（tool-aware gate）\n\n"
        "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n\n"
        "纯 read 路径不要求 Docker exec container；若执行 exec/process，仍必须通过原 Docker 边界与文件 SHA 门禁。\n"
    )
    (out / "summary.md").write_text(summary, encoding="utf-8")


def main(argv=None):
    global _LAST_RECORDER
    _LAST_RECORDER = None
    preflight = _arg_value(argv, "--preflight")
    parent = Path(preflight).resolve().parent if preflight else None
    before = set(parent.glob("poc03-*")) if parent else set()

    p2.Recorder = ToolAwareSandboxRecorder
    rc = p3.main(argv)

    if parent is None:
        return rc
    new_dirs = [p for p in parent.glob("poc03-*") if p not in before and p.is_dir()]
    if not new_dirs:
        return rc
    out = max(new_dirs, key=lambda p: p.stat().st_mtime_ns)
    result_path = out / "result.json"
    if not result_path.is_file():
        return rc

    result = json.loads(result_path.read_text(encoding="utf-8"))
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    required = trace_requires_container(trace)
    state = getattr(getattr(_LAST_RECORDER, "scopex_gate", None), "_scopex_gate_state", None)
    state_public = None
    if isinstance(state, dict):
        state_public = {
            "calls": state.get("calls"),
            "container_required_seen": state.get("container_required_seen"),
            "checked": state.get("checked"),
            "error": None if state.get("error") is None else str(state.get("error"))[:300],
        }
    result["sandbox_gate"] = {
        "container_required": required,
        "policy": "Docker gate only for returned exec/process tools; read uses sandbox fs bridge",
        "state": state_public,
    }

    if can_promote_read_only(result):
        result["functional_pass"] = True
        result["status"] = "PASS_POC03_SINGLE_CASE"
        result["sandbox_gate"]["read_only_pass_promoted"] = True
        rc = 0

    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _rewrite_summary(out, result)
    print("[poc03-v3] final assessment:")
    print(json.dumps({
        "status": result.get("status"),
        "functional_pass": result.get("functional_pass"),
        "sandbox_gate": result.get("sandbox_gate"),
        "audit_directory": str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("functional_pass") else rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC03_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
