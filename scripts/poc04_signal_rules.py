#!/usr/bin/env python3
"""Reusable text signals for POC04 correction grading."""
from __future__ import annotations
import re

def answer_signals(answer: str | None) -> dict:
    text = answer or ""
    lower = text.lower()
    robot_ok = (
        "joint_fault_code=0" in lower
        or "heartbeat=ok" in lower
        or bool(re.search(r"机器人.{0,24}(?:正常|无故障|未见异常|ready)", text, re.I))
        or bool(re.search(r"robot.{0,30}(?:healthy|normal|no fault|ready)", lower))
    )
    system_crash = (
        "status=137" in lower
        or "status 137" in lower
        or ("inference-worker" in lower and any(x in lower for x in ("exited", "退出", "崩溃", "重启")))
    )
    correction_ack = any(
        marker in lower for marker in (
            "暂定假设", "原假设", "上一轮", "修正", "降级", "撤回", "证据不足",
            "previous hypothesis", "earlier hypothesis", "revise", "revised", "downgrade", "retract",
        )
    )
    positive = re.compile(
        r"(?:视觉|vision).{0,32}(?:是|为|就是|is|was).{0,14}(?:根因|root cause)", re.I,
    )
    negated = re.compile(
        r"(?:不能证明|无法证明|不足以证明|未能证明|没有证据(?:能够)?证明|证据不足|"
        r"不是|并非|不应(?:当作|视为)?|不要(?:当作|视为)?|不再(?:认为|当作|视为)?|"
        r"撤回|降级|修正|重新定性|只是症状|"
        r"cannot(?:\s+prove)?|can't(?:\s+prove)?|not\s+(?:the\s+)?|"
        r"no\s+evidence|insufficient\s+evidence|unproven|retract|downgrade|revise)", re.I,
    )
    unsupported_visual_root = False
    for match in positive.finditer(text):
        window = text[max(0, match.start() - 48): min(len(text), match.end() + 20)]
        if negated.search(window):
            continue
        unsupported_visual_root = True
        break
    return {
        "robot_ok": robot_ok,
        "system_crash": system_crash,
        "correction_acknowledged": correction_ack,
        "unsupported_visual_root_assertion": unsupported_visual_root,
    }
