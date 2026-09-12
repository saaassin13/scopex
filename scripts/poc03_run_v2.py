#!/usr/bin/env python3
"""POC03 runner compatibility shim for OpenClaw's lazy sandbox lifecycle.

OpenClaw may send the first model request before creating a session sandbox.
That first request cannot contain a tool result from this task yet, so the
validated wire can be forwarded. Once the model requests a tool, OpenClaw
creates the native sandbox, executes the tool there, and the next model request
contains that tool result. Before forwarding that second/subsequent request we
run the original POC03 sandbox boundary/hash gate exactly once.

This shim changes only *when* the existing gate is evaluated. It does not relax
POC03 grading, Skill evidence requirements, sandbox checks, model request
validation, or automatic-retry policy.
"""
from __future__ import annotations

import sys

import poc02_run as p2
import poc03_run as p3


OriginalRecorder = p2.Recorder


def defer_first_gate(gate):
    """Return a gate that defers exactly the first call, then checks once.

    Successful checks are cached. A failed check is also cached and re-raised so
    repeated upstream attempts cannot rerun file-creating audit commands or
    produce misleading FileExistsError noise.
    """
    state = {"calls": 0, "checked": False, "error": None}

    def wrapped():
        state["calls"] += 1
        if state["calls"] == 1:
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


class LazySandboxRecorder(OriginalRecorder):
    def __init__(self, out, ref, key, token, native, gate, max_requests):
        super().__init__(
            out, ref, key, token, native,
            defer_first_gate(gate), max_requests,
        )


def main(argv=None):
    # poc03_run.main imports poc02_run from sys.modules, so replacing the class
    # on that module is enough to preserve all existing POC03 behavior while
    # changing only the gate timing.
    p2.Recorder = LazySandboxRecorder
    return p3.main(argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC03_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
