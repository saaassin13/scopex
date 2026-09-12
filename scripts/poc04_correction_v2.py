#!/usr/bin/env python3
"""POC04-B runner with corrected reusable answer-signal rules.

This wrapper preserves the validated POC04-B runtime implementation while
replacing only the text signal classifier that previously misread retracted
hypotheses as affirmative claims.
"""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc04_correction as base
from poc04_signal_rules import answer_signals

base.answer_signals = answer_signals

if __name__ == "__main__":
    try:
        sys.exit(base.main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC04B_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
