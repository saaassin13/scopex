#!/usr/bin/env python3
"""Revalidate one stored Runtime API task without model/tool replay.

Reads evidence.json + claims.json from the task audit, reconstructs the exact
EvidenceCatalog, then applies the current FinalizationService validation and
renderer. The historical task files are never modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.evidence.catalog import EvidenceCatalog
from scopex.finalizer.service import FinalizationService


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT / ".local" / "runtime-api",
    )
    args = parser.parse_args(argv)

    task_dir = args.data_root.expanduser().resolve() / "tasks" / args.task_id
    evidence_path = task_dir / "evidence.json"
    claims_path = task_dir / "claims.json"
    if not evidence_path.is_file():
        raise ValueError(f"missing evidence audit: {evidence_path}")
    if not claims_path.is_file():
        raise ValueError(f"missing claims audit: {claims_path}")

    snapshot = load_json(evidence_path)
    payload = load_json(claims_path)
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise ValueError("evidence.json items must be a list")

    session_key = snapshot.get("session_key")
    if not isinstance(session_key, str):
        session_key = "audit-revalidate"
    catalog = EvidenceCatalog(args.task_id, session_key)
    for index, row in enumerate(items):
        if not isinstance(row, dict):
            raise ValueError(f"evidence item {index} is not an object")
        item = catalog.add(
            source=row.get("source"),
            raw=row.get("raw"),
            tool_call_id=row.get("tool_call_id"),
            metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else None,
        )
        expected = row.get("ref")
        if isinstance(expected, str) and item.ref != expected:
            raise ValueError(
                f"evidence ref order mismatch at {index}: expected {expected}, got {item.ref}"
            )

    result = FinalizationService().finalize(payload, catalog)
    output = {
        "status": "PASS_RUNTIME_TASK_REVALIDATE" if result.valid else "RUNTIME_TASK_REVALIDATE_FAILED",
        "task_id": args.task_id,
        "valid": result.valid,
        "errors": list(result.errors),
        "normalizations": list(result.normalizations),
        "evidence_count": len(catalog.items),
        "rendered": result.rendered,
        "note": "Stored evidence/claims only; no OpenClaw, tool, or model replay.",
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.valid else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print("RUNTIME_TASK_REVALIDATE_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
