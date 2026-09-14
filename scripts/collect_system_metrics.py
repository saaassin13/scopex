#!/usr/bin/env python3
"""Print one current DGX Spark host resource snapshot.

This helper intentionally does not persist or append historical metrics. Product
runtime snapshots are created per task by `scopex.host_snapshot` and mounted
read-only into the Agent sandbox.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.host_snapshot import collect_current_host_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pretty', action='store_true')
    args = parser.parse_args()
    snapshot = collect_current_host_snapshot()
    print(json.dumps(snapshot, ensure_ascii=False, indent=2 if args.pretty else None, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
