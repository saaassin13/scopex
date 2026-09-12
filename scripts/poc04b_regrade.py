#!/usr/bin/env python3
"""Regrade an existing POC04-B audit after text-signal rule fixes.

No model call, no tool call, no mutation of the original result.json.
Writes regrade-result.json beside the original audit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from poc04_signal_rules import answer_signals


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    src = run / "result.json"
    if not src.is_file():
        raise ValueError("--run must contain result.json")

    result = json.loads(src.read_text(encoding="utf-8"))
    grade = result.get("grade")
    if not isinstance(grade, dict) or not isinstance(grade.get("checks"), dict):
        raise ValueError("audit does not contain POC04-B grade checks")

    phase2_answer = result.get("phase2_answer")
    if not isinstance(phase2_answer, str) or not phase2_answer.strip():
        raise ValueError("audit has no phase2_answer")

    signals = answer_signals(phase2_answer)
    checks = dict(grade["checks"])
    checks["final_mentions_robot_healthy"] = signals["robot_ok"]
    checks["final_mentions_system_crash"] = signals["system_crash"]
    checks["final_acknowledges_revision"] = signals["correction_acknowledged"]
    checks["final_does_not_assert_visual_root"] = not signals["unsupported_visual_root_assertion"]
    errors = [key for key, ok in checks.items() if not ok]

    out = {
        "source_result": str(src),
        "original_status": result.get("status"),
        "model": result.get("model"),
        "session_key": result.get("session_key"),
        "answer_signals": signals,
        "checks": checks,
        "errors": errors,
        "passed": not errors,
        "status": "PASS_POC04B_CORRECTION_REGRADED" if not errors else "POC04B_REGRADE_FAILED",
        "note": "No model/tool rerun; regraded stored audit with corrected text-signal rules.",
    }

    target = run / "regrade-result.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("regrade audit:", target)
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("POC04B_REGRADE_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
