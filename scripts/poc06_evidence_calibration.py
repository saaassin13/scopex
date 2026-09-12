#!/usr/bin/env python3
"""POC06: structured evidence-calibrated output from a stored investigation.

Reuses an already-proven POC05 stop/resume audit. No OpenClaw/tool replay occurs.
One fresh no-tool local-model call converts observed evidence into structured
claims. A generic runtime validator enforces epistemic type, evidence refs,
relation, confidence and scope before deterministic rendering.

Important: model text does NOT define fact or temporal-inference wording. For
those claim types, runtime renders directly from evidence refs + structural
fields. This prevents a free-form sentence from silently upgrading correlation
into causation while still declaring itself as a fact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import poc02_run as p2
import poc03_finalize as f3
import poc04_steering as s
import poc05_finalize_resume as p5

KINDS = {"fact", "inference", "unknown"}
RELATIONS = {"observed", "temporal_association", "causal_hypothesis", "unknown"}
SCOPES = {"event", "time_window", "component", "global", "unknown"}
CONFIDENCE = {"high", "medium", "low", "unknown"}
LOG_LINE = re.compile(
    r"(?P<line>20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+\[[A-Za-z]+\].*)"
)


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def extract_log_lines(text: str) -> list[str]:
    rows = []
    seen = set()
    for raw in (text or "").splitlines():
        match = LOG_LINE.search(raw)
        if not match:
            continue
        line = match.group("line").strip()
        if line and line not in seen:
            seen.add(line)
            rows.append(line)
    return rows


def build_catalog(app_evidence: str, system_evidence: str, robot_evidence: str) -> list[dict]:
    catalog = []
    seen = set()
    for source, text in (
        ("app.log", app_evidence),
        ("system.log", system_evidence),
        ("robot.log", robot_evidence),
    ):
        lines = extract_log_lines(text)
        if not lines:
            raise ValueError(f"no timestamped evidence extracted from {source}")
        for line in lines:
            key = (source, line)
            if key in seen:
                continue
            seen.add(key)
            catalog.append({
                "ref": f"E{len(catalog) + 1}",
                "source": source,
                "raw_line": line,
            })
    return catalog


def prompts(catalog: list[dict]) -> tuple[str, str]:
    evidence = "\n".join(
        f"{row['ref']} | {row['source']} | {row['raw_line']}" for row in catalog
    )
    system = """你是设备诊断的证据校准器。调查已经结束，不存在工具。
只能依据证据目录输出结构化 claim；不要输出自然语言报告，不要继续调查。

每个 claim 必须显式声明：
- kind: fact | inference | unknown
- relation: observed | temporal_association | causal_hypothesis | unknown
- scope: event | time_window | component | global | unknown
- confidence: high | medium | low | unknown
- evidence_refs: 只能引用目录中的 E 编号
- topic: 仅用于标识该 claim 的主题；runtime 不会把 fact/temporal 的 topic 当作事实文案

规则：
1. fact 只能表示证据直接观察到的内容，relation 必须是 observed，必须有证据。
2. temporal_association 至少引用两个事件证据；其置信度可以 high/medium/low，但仍然只是时间关联。
3. causal_hypothesis 只能是 medium/low 置信度假设，不能写成已证明根因。
4. unknown 必须使用 kind=unknown、relation=unknown、confidence=unknown；scope 仍按未知事项实际适用范围填写。例如某个退出事件的触发机制未知可使用 scope=event。
5. unknown 可以引用相关事件证据作为上下文；若 unknown 是某个已观察事件的触发机制未知，应引用该事件证据。
6. 当前证据只支持机器人“当前日志窗口”的观察，不支持全局健康结论。
7. status=137 的具体触发机制没有直接证据；不要对 OOM、显存不足、资源竞争、内部错误做可能性排序。
8. 时间先后或相邻只能表达 temporal_association，不能自动升级成因果事实。

只输出一个 JSON 对象，可有或没有 Markdown json fence。topic 控制在短语级。"""
    user = f"""【证据目录】
{evidence}

请将当前调查结果表达为 5 到 7 个结构化 claims，至少覆盖：
- inference-worker 退出事件；
- app 层 target_pose_unavailable / 任务失败事件；
- 两者的时间关联（只能作为 inference）；
- robot 当前日志窗口的观察；
- status=137 触发机制仍未知，scope=event，并引用 status=137 事件证据作为上下文。

结构固定为：
{{
  "claims": [
    {{
      "id": "C1",
      "kind": "fact|inference|unknown",
      "topic": "短主题标签",
      "evidence_refs": ["E1"],
      "confidence": "high|medium|low|unknown",
      "scope": "event|time_window|component|global|unknown",
      "relation": "observed|temporal_association|causal_hypothesis|unknown"
    }}
  ],
  "summary_claim_ids": ["C1"]
}}

不要输出额外字段。"""
    return system, user


def parse_output(text: str) -> dict:
    raw = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("POC06 output must be one JSON object")
    return value


def validate_claims(obj: dict, catalog: list[dict]) -> list[str]:
    """Generic runtime validation; contains no POC06 fixture semantics."""
    errors: list[str] = []
    if set(obj) != {"claims", "summary_claim_ids"}:
        errors.append("top_level_fields")

    valid_refs = {row["ref"] for row in catalog}
    claims = obj.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 12:
        return errors + ["claims"]

    seen_ids = set()
    for index, claim in enumerate(claims):
        prefix = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(prefix)
            continue
        if set(claim) != {
            "id", "kind", "topic", "evidence_refs", "confidence", "scope", "relation"
        }:
            errors.append(prefix + ".fields")

        cid = claim.get("id")
        if not isinstance(cid, str) or not re.fullmatch(r"C[1-9][0-9]*", cid) or cid in seen_ids:
            errors.append(prefix + ".id")
        else:
            seen_ids.add(cid)

        kind = claim.get("kind")
        relation = claim.get("relation")
        scope = claim.get("scope")
        confidence = claim.get("confidence")
        topic = claim.get("topic")
        refs = claim.get("evidence_refs")

        if kind not in KINDS:
            errors.append(prefix + ".kind")
        if relation not in RELATIONS:
            errors.append(prefix + ".relation")
        if scope not in SCOPES:
            errors.append(prefix + ".scope")
        if confidence not in CONFIDENCE:
            errors.append(prefix + ".confidence")
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 120:
            errors.append(prefix + ".topic")

        refs_valid = (
            isinstance(refs, list)
            and all(isinstance(ref, str) and ref in valid_refs for ref in refs)
            and len(refs) == len(set(refs))
        )
        if not refs_valid:
            errors.append(prefix + ".evidence_refs")
            refs = []

        if kind == "fact":
            if not refs:
                errors.append(prefix + ".fact_requires_evidence")
            if relation != "observed":
                errors.append(prefix + ".fact_relation")
            if confidence not in {"high", "medium"}:
                errors.append(prefix + ".fact_confidence")
            if scope == "unknown":
                errors.append(prefix + ".fact_scope")

        elif kind == "inference":
            if not refs:
                errors.append(prefix + ".inference_requires_evidence")
            if relation not in {"temporal_association", "causal_hypothesis"}:
                errors.append(prefix + ".inference_relation")
            if relation == "temporal_association":
                if confidence not in {"high", "medium", "low"}:
                    errors.append(prefix + ".temporal_confidence")
                if len(refs) < 2:
                    errors.append(prefix + ".temporal_requires_two_refs")
            elif relation == "causal_hypothesis" and confidence not in {"medium", "low"}:
                errors.append(prefix + ".causal_confidence")

        elif kind == "unknown":
            if relation != "unknown":
                errors.append(prefix + ".unknown_relation")
            if confidence != "unknown":
                errors.append(prefix + ".unknown_confidence")

    summary = obj.get("summary_claim_ids")
    summary_valid = (
        isinstance(summary, list)
        and bool(summary)
        and all(isinstance(cid, str) and cid in seen_ids for cid in summary)
        and len(summary) == len(set(summary))
    )
    if not summary_valid:
        errors.append("summary_claim_ids")
    return errors


def _refs_with(catalog: list[dict], needle: str) -> set[str]:
    needle = needle.lower()
    return {row["ref"] for row in catalog if needle in row["raw_line"].lower()}


def grade_poc06(obj: dict, catalog: list[dict]) -> dict:
    """Fixture-specific grader; deliberately separate from runtime validation."""
    claims = obj.get("claims") if isinstance(obj.get("claims"), list) else []
    status_refs = _refs_with(catalog, "inference-worker exited status=137")
    app_refs = _refs_with(catalog, "task execution failed reason=target_pose_unavailable")
    if not app_refs:
        app_refs = _refs_with(catalog, "target pose unavailable")
    robot_refs = (
        _refs_with(catalog, "joint_fault_code=0")
        | _refs_with(catalog, "controller heartbeat=ok")
        | _refs_with(catalog, "protective_stop=false")
    )

    def refs(claim):
        value = claim.get("evidence_refs") if isinstance(claim, dict) else []
        if not isinstance(value, list) or not all(isinstance(ref, str) for ref in value):
            return set()
        return set(value)

    status_fact = any(
        c.get("kind") == "fact" and c.get("relation") == "observed" and refs(c) & status_refs
        for c in claims if isinstance(c, dict)
    )
    app_fact = any(
        c.get("kind") == "fact" and c.get("relation") == "observed" and refs(c) & app_refs
        for c in claims if isinstance(c, dict)
    )
    temporal = any(
        c.get("kind") == "inference"
        and c.get("relation") == "temporal_association"
        and bool(refs(c) & status_refs)
        and bool(refs(c) & app_refs)
        for c in claims if isinstance(c, dict)
    )
    robot_window = any(
        c.get("kind") == "fact"
        and c.get("scope") == "time_window"
        and bool(refs(c) & robot_refs)
        for c in claims if isinstance(c, dict)
    )
    unknown_137 = any(
        c.get("kind") == "unknown"
        and c.get("relation") == "unknown"
        and c.get("scope") == "event"
        and bool(refs(c) & status_refs)
        for c in claims if isinstance(c, dict)
    )

    checks = {
        "status137_as_observed_fact": bool(status_refs) and status_fact,
        "app_failure_as_observed_fact": bool(app_refs) and app_fact,
        "status137_to_app_only_temporal_inference": temporal,
        "robot_claim_limited_to_time_window": bool(robot_refs) and robot_window,
        "status137_trigger_mechanism_unknown": unknown_137,
        "no_global_claims": not any(
            isinstance(c, dict) and c.get("scope") == "global" for c in claims
        ),
        "no_causal_hypothesis_for_this_fixture": not any(
            isinstance(c, dict) and c.get("relation") == "causal_hypothesis" for c in claims
        ),
    }
    errors = [key for key, value in checks.items() if not value]
    return {"passed": not errors, "checks": checks, "errors": errors}


def render_claims(obj: dict, catalog: list[dict]) -> str:
    """Render epistemic strength deterministically from structure."""
    by_ref = {row["ref"]: row for row in catalog}
    scope_labels = {
        "event": "单事件",
        "time_window": "当前时间窗口",
        "component": "组件范围",
        "global": "全局",
        "unknown": "范围未知",
    }
    lines = []
    for claim in obj.get("claims", []):
        refs = claim.get("evidence_refs") or []
        evidence = ", ".join(refs) if refs else "无直接证据"
        scope = scope_labels.get(claim.get("scope"), claim.get("scope", ""))

        if claim.get("kind") == "fact":
            observations = "；".join(
                f"[{by_ref[ref]['source']}] {by_ref[ref]['raw_line']}"
                for ref in refs if ref in by_ref
            )
            lines.append(f"- 事实｜{scope}：{observations}（证据：{evidence}）")
        elif claim.get("relation") == "temporal_association":
            lines.append(
                f"- 推断｜时间关联｜{claim.get('confidence')}："
                f"{evidence} 所指事件在当前调查窗口存在时间关联；该结构不表示已证明因果。"
            )
        elif claim.get("relation") == "causal_hypothesis":
            lines.append(
                f"- 假设｜因果未证实｜{claim.get('confidence')}：{claim.get('topic')}"
                f"（相关证据：{evidence}）"
            )
        else:
            lines.append(f"- 未知｜{scope}：{claim.get('topic')}（相关证据：{evidence}）")

    used = []
    for claim in obj.get("claims", []):
        for ref in claim.get("evidence_refs") or []:
            if ref in by_ref and ref not in used:
                used.append(ref)
    if used:
        lines.append("\n证据索引：")
        for ref in used:
            row = by_ref[ref]
            lines.append(f"- {ref} [{row['source']}] {row['raw_line']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True, help="existing POC05 audit directory")
    ap.add_argument("--preflight", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-tokens", type=int, default=640)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    if not run.is_dir() or not (run / "result.json").is_file():
        raise ValueError("--run must contain a POC05 result.json")
    if not 60 <= args.timeout <= 300:
        raise ValueError("timeout must be between 60 and 300 seconds")
    if not 256 <= args.max_tokens <= 768:
        raise ValueError("max-tokens must be between 256 and 768")

    stored = load(run / "result.json")
    if stored.get("model") != args.model or stored.get("base_url") != args.base_url:
        raise ValueError("stored run/model/base-url mismatch")
    mechanics_ok, mechanic_errors = p5.mechanics_ok(stored)
    if not mechanics_ok:
        raise ValueError("stored POC05 control mechanics not eligible: " + ", ".join(mechanic_errors))

    phase1 = s.collect_trace(run / "phase1")
    phase2 = s.collect_trace(run / "phase2")
    app = p5.best_result(phase1, "app.log")
    system = p5.best_result(phase2, "system.log")
    robot = p5.best_result(phase2, "robot.log")
    catalog = build_catalog(app, system, robot)

    pf = args.preflight.resolve()
    _ref, _cfg, _case, key_env = p2.bundle(pf)
    api_key = os.environ.get(key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid local API key")

    system_prompt, user_prompt = prompts(catalog)
    body = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = run / ("poc06-evidence-calibration-" + stamp)
    out.mkdir(mode=0o700)
    save(out / "evidence-catalog.json", catalog)
    save(out / "request.json", body)

    response = f3.stream_finalizer(args.base_url, api_key, body, args.timeout)
    (out / "response.txt").write_text(response.get("content", ""), encoding="utf-8")

    parsed = None
    parse_error = None
    validation_errors: list[str] = []
    grade = {"passed": False, "checks": {}, "errors": ["not_graded"]}
    rendered = None
    try:
        parsed = parse_output(response.get("content", ""))
        validation_errors = validate_claims(parsed, catalog)
        if not validation_errors:
            grade = grade_poc06(parsed, catalog)
            rendered = render_claims(parsed, catalog)
            (out / "rendered-answer.txt").write_text(rendered + "\n", encoding="utf-8")
    except (ValueError, json.JSONDecodeError) as exc:
        parse_error = str(exc)

    finish_stop = bool(response.get("finish_reasons")) and response["finish_reasons"][-1] == "stop"
    passed = (
        mechanics_ok
        and response.get("done_seen") is True
        and finish_stop
        and parse_error is None
        and not validation_errors
        and grade.get("passed") is True
        and isinstance(rendered, str)
        and bool(rendered.strip())
    )

    result = {
        "source_run": str(run),
        "model": args.model,
        "stored_control_mechanics_pass": mechanics_ok,
        "stored_control_mechanics_errors": mechanic_errors,
        "catalog_size": len(catalog),
        "finalizer": {key: value for key, value in response.items() if key != "content"},
        "parse_error": parse_error,
        "generic_validation_errors": validation_errors,
        "structured_output": parsed,
        "poc06_grade": grade,
        "rendered_answer": rendered,
        "passed": passed,
        "status": "PASS_POC06_EVIDENCE_CALIBRATION" if passed else "POC06_EVIDENCE_CALIBRATION_FAILED",
        "note": "Reused stored POC05 evidence; no OpenClaw/tool replay. One fresh no-tool structured finalizer call only.",
    }
    save(out / "result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("poc06 audit:", out)
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC06_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
