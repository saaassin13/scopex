#!/usr/bin/env python3
"""Regrade POC05 strictly on its control-plane objective.

POC05 is about observable progress, deterministic STOP, same-session RESUME,
context preservation, and resume-time re-steering. Diagnostic prose calibration
is intentionally reported as a separate quality dimension instead of being used
to rewrite the control result.

No model or tool call is made.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc05_finalize_resume as base


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_finalizer_runs(run: Path) -> list[tuple[Path, dict]]:
    rows = []
    for path in sorted(run.glob("finalize-*/result.json")):
        try:
            obj = load(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            rows.append((path, obj))
    return rows


def finalizer_mechanics(obj: dict, result_path: Path) -> dict:
    meta = obj.get("finalizer") or {}
    reasons = meta.get("finish_reasons") or []
    answer = obj.get("final_answer")
    request_path = result_path.parent / "request.json"
    no_tools = False
    simple_roles = False
    if request_path.is_file():
        req = load(request_path)
        no_tools = "tools" not in req
        messages = req.get("messages") or []
        simple_roles = bool(messages) and all(
            isinstance(m, dict) and m.get("role") in {"system", "user"}
            for m in messages
        )
    return {
        "done_seen": meta.get("done_seen") is True,
        "finish_reason_stop": bool(reasons) and reasons[-1] == "stop",
        "visible_answer": isinstance(answer, str) and bool(answer.strip()),
        "fresh_context_has_no_tools": no_tools,
        "fresh_context_only_system_user": simple_roles,
    }


def choose_completed_finalizer(run: Path):
    candidates = []
    for path, obj in find_finalizer_runs(run):
        checks = finalizer_mechanics(obj, path)
        if all(checks.values()):
            candidates.append((path, obj, checks))
    return candidates[-1] if candidates else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    stored_path = run / "result.json"
    if not stored_path.is_file():
        raise ValueError("--run must contain result.json")
    stored = load(stored_path)

    mechanics_pass, mechanics_errors = base.mechanics_ok(stored)
    chosen = choose_completed_finalizer(run)
    finalizer_checks = {}
    finalizer_path = None
    answer_quality = "NOT_EVALUATED"
    if chosen:
        result_path, final_obj, finalizer_checks = chosen
        finalizer_path = str(result_path)
        answer_quality = "PASS" if final_obj.get("passed") is True else "FAIL_EVIDENCE_CALIBRATION"

    control_checks = {
        "stored_progress_stop_resume_mechanics": mechanics_pass,
        "fresh_finalizer_completed": chosen is not None,
    }
    control_pass = all(control_checks.values())

    result = {
        "source_run": str(run),
        "original_status": stored.get("status"),
        "model": stored.get("model"),
        "session_key": stored.get("session_key"),
        "control_checks": control_checks,
        "stored_mechanics_errors": mechanics_errors,
        "finalizer_result": finalizer_path,
        "finalizer_mechanics": finalizer_checks,
        "answer_quality": answer_quality,
        "control_passed": control_pass,
        "status": "PASS_POC05_CONTROL" if control_pass else "POC05_CONTROL_FAILED",
        "note": (
            "POC05 control-plane result is separated from diagnostic prose calibration. "
            "No model/tool replay was performed."
        ),
    }
    out = run / "control-regrade-result.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("poc05 control regrade:", out)
    return 0 if control_pass else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC05_CONTROL_REGRADE_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
