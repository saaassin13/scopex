#!/usr/bin/env python3
"""Re-finalize a stored Runtime MVP investigation without rerunning OpenClaw.

This utility is for output-quality iteration after an investigation has already
been proven. It reads the recorded wire trace from ``agent-turns/turn-001``,
rebuilds a fresh line-level EvidenceCatalog, performs exactly one fresh no-tool
Structured Finalizer call, and writes results into a new ``refinalize-*``
directory under the existing run. It never mutates the original task audit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.trace import load_audit_trace
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.extractor import EvidenceExtractionPipeline, ReadLineExtractor
from scopex.events.progress import InMemoryEventSink
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer


REQUIRED_FILES = {"app.log", "system.log", "robot.log"}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_task(run: Path) -> tuple[Path, dict]:
    rows = sorted((run / "tasks").glob("*/task.json"))
    if len(rows) != 1:
        raise ValueError("stored run must contain exactly one tasks/<id>/task.json")
    return rows[0], load_json(rows[0])


def coverage(catalog: EvidenceCatalog) -> dict[str, bool]:
    names = {Path(item.source).name for item in catalog.items}
    return {name: name in names for name in sorted(REQUIRED_FILES)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True, help="existing runtime-mvp-smoke run root")
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--turn", default="turn-001")
    ap.add_argument("--finalizer-max-tokens", type=int, default=768)
    ap.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = ap.parse_args(argv)

    run = args.run.expanduser().resolve()
    if not run.is_dir():
        raise ValueError("--run must be an existing Runtime MVP smoke directory")
    turn = run / "agent-turns" / args.turn
    if not turn.is_dir():
        raise ValueError("stored turn audit directory does not exist")
    if not 256 <= args.finalizer_max_tokens <= 1024:
        raise ValueError("finalizer-max-tokens must be between 256 and 1024")

    _task_path, task = find_task(run)
    task_id = task.get("id")
    session_key = task.get("session_key")
    user_request = task.get("user_request")
    if not all(isinstance(value, str) and value for value in (task_id, session_key, user_request)):
        raise ValueError("stored task.json is missing identity/request fields")

    events = InMemoryEventSink()
    catalog = EvidenceCatalog(task_id, session_key)
    pipeline = EvidenceExtractionPipeline(
        EvidenceCollector(catalog, events),
        (ReadLineExtractor(max_lines=256, max_line_chars=4096),),
    )
    trace = load_audit_trace(turn)
    pipeline.process_trace(trace)
    found = coverage(catalog)
    if not all(found.values()):
        raise ValueError("stored investigation does not contain completed reads for all fixtures")

    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")
    finalizer = StructuredFinalizer(
        StreamingFinalizerClient(args.base_url, api_key=api_key, timeout_s=120),
        model=args.model,
        max_tokens=args.finalizer_max_tokens,
    )
    final = finalizer.run(user_request=user_request, catalog=catalog)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = run / ("refinalize-" + stamp)
    out.mkdir(mode=0o700)
    (out / "evidence.json").write_text(
        json.dumps(catalog.snapshot(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    if final.payload is not None:
        (out / "claims.json").write_text(
            json.dumps(final.payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    rendered = final.finalization.rendered if final.finalization is not None else None
    if rendered is not None:
        (out / "final.txt").write_text(rendered + "\n", encoding="utf-8")

    result = {
        "status": "PASS_RUNTIME_MVP_REFINALIZE" if final.valid else "RUNTIME_MVP_REFINALIZE_FAILED",
        "source_run": str(run),
        "source_turn": str(turn),
        "task_id": task_id,
        "model": args.model,
        "evidence_count": len(catalog.items),
        "coverage": found,
        "parse_error": final.parse_error,
        "finish_reasons": list(final.transport.finish_reasons),
        "done_seen": final.transport.done_seen,
        "elapsed_s": final.transport.elapsed_s,
        "usage": final.transport.usage,
        "validation_errors": list(final.finalization.errors) if final.finalization else [],
        "rendered": rendered,
        "note": "No OpenClaw/tool replay; stored wire trace was re-extracted at line granularity.",
    }
    (out / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print("runtime mvp refinalize audit:", out)
    return 0 if final.valid else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("RUNTIME_MVP_REFINALIZE_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
