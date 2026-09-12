#!/usr/bin/env python3
"""Finalize a recorded POC03 investigation from a compact evidence catalog.

This is phase B of the POC03 two-phase design:

  OpenClaw investigation -> Evidence Context Builder -> fresh no-tool finalizer

The finalizer never receives assistant tool-call history or tool-role messages.
It sees only business background plus exact source-log lines that were actually
observed in recorded tool results. Those lines are assigned deterministic E1,
E2, ... references. The model cites refs; Python expands refs back to exact raw
log lines and reuses the existing POC03 business grader.

Python >=3.10, standard library only. No automatic retry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import poc02_run as p2
import poc03_run as p3

DEFAULT_FOCUS = "CalLeftCamStartFollowPt failed"
DEFAULT_MAX_EVIDENCE = 12
DEFAULT_MAX_TOKENS = 384


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj):
    with path.open("x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def tool_history(run: Path):
    """Return unique recorded tool calls and their latest recorded returns."""
    calls: dict[str, dict] = {}
    results: dict[str, str] = {}
    for wire in sorted(run.glob("wire-*-request.json")):
        payload = load_json(wire)
        for message in payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                        continue
                    func = call.get("function") or {}
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (TypeError, ValueError):
                        args = {}
                    calls[call["id"]] = {
                        "name": func.get("name"),
                        "arguments": args if isinstance(args, dict) else {},
                    }
            elif message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
                results[message["tool_call_id"]] = p3.text_content(message.get("content"))
    return calls, results


def extract_knowledge(calls: dict, results: dict) -> str:
    rows = []
    suffix = f"/skills/{p3.SKILL_NAME}/references/cow-disinfect-log.md"
    for cid, call in calls.items():
        args = call.get("arguments") or {}
        path = args.get("path", args.get("file_path"))
        content = results.get(cid, "")
        if call.get("name") == "read" and isinstance(path, str) and path.endswith(suffix):
            if p3.KNOWLEDGE_MARKER in content:
                rows.append(content)
    if not rows:
        raise ValueError("recorded investigation did not prove knowledge reference was read")
    return max(rows, key=len)


def observed_line_indices(source_text: str, results: dict[str, str]) -> list[int]:
    """Find exact source lines that occurred inside recorded tool results.

    Matching exact source-line substrings also handles grep -n prefixes and
    cat -A suffixes without trusting line numbers emitted by a command.
    """
    lines = source_text.splitlines()
    contents = [value for value in results.values() if isinstance(value, str) and value]
    observed = []
    for idx, line in enumerate(lines):
        if not line:
            continue
        if any(line in content for content in contents):
            observed.append(idx)
    return observed


def _score_line(line: str, idx: int, anchors: list[int], focus: str) -> int:
    lower = line.lower()
    score = 0
    if focus and focus.lower() in lower:
        score += 200
    if "[error]" in lower:
        score += 80
    if "[warn]" in lower:
        score += 60
    if any(word in lower for word in ("failed", "invalid", "exception")):
        score += 70
    if any(word in lower for word in ("succeeded", "recovered", "recover")):
        score += 65
    if anchors:
        distance = min(abs(idx - anchor) for anchor in anchors)
        score += max(0, 48 - 4 * distance)
    return score


def build_catalog(source_text: str, results: dict[str, str], focus: str,
                  max_evidence: int = DEFAULT_MAX_EVIDENCE) -> list[dict]:
    if max_evidence < 4 or max_evidence > 40:
        raise ValueError("max_evidence must be between 4 and 40")
    lines = source_text.splitlines()
    observed = observed_line_indices(source_text, results)
    if not observed:
        raise ValueError("no exact source-log lines were observed in recorded tool results")

    anchors = [idx for idx in observed if focus.lower() in lines[idx].lower()] if focus else []
    scored = [(_score_line(lines[idx], idx, anchors, focus), idx) for idx in observed]
    # Keep the highest-value evidence, but restore source order for final context.
    selected = sorted(idx for _score, idx in sorted(scored, key=lambda x: (-x[0], x[1]))[:max_evidence])
    return [
        {"ref": f"E{n}", "source_line": idx + 1, "raw_line": lines[idx]}
        for n, idx in enumerate(selected, 1)
    ]


def finalizer_prompts(focus: str, knowledge: str, catalog: list[dict]):
    system = """你是设备故障诊断结果整理器。调查阶段已经结束。\n\n只能根据提供的业务背景和证据目录完成最终结论；禁止继续调查、调用工具、请求额外信息或编造根因。业务背景只能帮助解释，事件事实必须由 E 编号证据支持。\n\n输出必须简短：evidence 最多4条，facts最多3条，inferences最多1条，unknowns最多2条。只输出一个 JSON 对象，可以有或没有 Markdown json fence。"""

    evidence_text = "\n".join(
        f"{row['ref']} | source line {row['source_line']} | {row['raw_line']}"
        for row in catalog
    )
    user = f"""诊断目标：{focus}\n\n【业务背景】\n{knowledge}\n\n【证据目录】\n{evidence_text}\n\n只输出以下结构：\n{{\n  \"direct_trigger\": \"简短描述\",\n  \"persistence\": \"transient|persistent|unknown\",\n  \"recovery\": {{\"observed\": true|false|null, \"evidence_ref\": \"E编号或空字符串\"}},\n  \"evidence\": [{{\"role\": \"upstream|trigger|failure|recovery\", \"ref\": \"E编号\"}}],\n  \"facts\": [\"最多3条\"],\n  \"inferences\": [\"最多1条\"],\n  \"unknowns\": [\"最多2条\"],\n  \"conclusion\": \"一句话\",\n  \"confidence\": \"high|medium|low\"\n}}\n\n不要复制原始日志全文；只引用 E 编号。"""
    return system, user


def parse_compact(text: str) -> dict:
    raw = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("finalizer output must be one JSON object")
    return value


def validate_compact(obj: dict, catalog: list[dict]) -> list[str]:
    errors = []
    valid_refs = {row["ref"] for row in catalog}
    if not isinstance(obj.get("direct_trigger"), str):
        errors.append("direct_trigger")
    if obj.get("persistence") not in {"transient", "persistent", "unknown"}:
        errors.append("persistence")
    recovery = obj.get("recovery")
    if not isinstance(recovery, dict) or recovery.get("observed") not in {True, False, None}:
        errors.append("recovery")
    else:
        ref = recovery.get("evidence_ref", "")
        if not isinstance(ref, str) or (ref and ref not in valid_refs):
            errors.append("recovery.evidence_ref")
        if recovery.get("observed") is True and not ref:
            errors.append("recovery.evidence_ref_required")

    evidence = obj.get("evidence")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        errors.append("evidence")
    else:
        for item in evidence:
            if not isinstance(item, dict) or item.get("role") not in {
                "upstream", "trigger", "failure", "recovery"
            } or item.get("ref") not in valid_refs:
                errors.append("evidence.item")
                break

    for key, limit in (("facts", 3), ("inferences", 1), ("unknowns", 2)):
        value = obj.get(key)
        if not isinstance(value, list) or len(value) > limit or not all(isinstance(x, str) for x in value):
            errors.append(key)
    if not isinstance(obj.get("conclusion"), str):
        errors.append("conclusion")
    if obj.get("confidence") not in {"high", "medium", "low"}:
        errors.append("confidence")
    return errors


def expand_compact(obj: dict, catalog: list[dict]) -> dict:
    by_ref = {row["ref"]: row["raw_line"] for row in catalog}
    recovery = obj["recovery"]
    recovery_ref = recovery.get("evidence_ref", "")
    return {
        "direct_trigger": obj["direct_trigger"],
        "persistence": obj["persistence"],
        "recovery": {
            "observed": recovery.get("observed"),
            "raw_line": by_ref.get(recovery_ref, ""),
        },
        "evidence": [
            {"role": item["role"], "raw_line": by_ref[item["ref"]]}
            for item in obj["evidence"]
        ],
        "facts": obj["facts"],
        "inferences": obj["inferences"],
        "unknowns": obj["unknowns"],
        "conclusion": obj["conclusion"],
        "confidence": obj["confidence"],
    }


def endpoint(url: str):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    if parsed.scheme != "http" or host != "127.0.0.1" or parsed.username or parsed.password:
        raise ValueError("finalizer only accepts a credential-free loopback http endpoint")
    if parsed.path.rstrip("/") != "/v1" or parsed.query or parsed.fragment:
        raise ValueError("base URL must end at /v1")
    return host, parsed.port or 80


def stream_finalizer(base_url: str, api_key: str, body: dict, timeout_s: int):
    host, port = endpoint(base_url)
    con = http.client.HTTPConnection(host, port, timeout=timeout_s)
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    start = time.monotonic()
    first_content = None
    chunks = []
    usage = None
    finish = []
    done = False
    try:
        con.request("POST", "/v1/chat/completions", body=encoded, headers=headers)
        resp = con.getresponse()
        headers_s = time.monotonic() - start
        if resp.status != 200:
            detail = resp.read(65536).decode("utf-8", errors="replace")
            raise ValueError(f"finalizer HTTP {resp.status}: {detail[:500]}")
        while True:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                break
            if not payload:
                continue
            event = json.loads(payload)
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            for choice in event.get("choices", []):
                if choice.get("finish_reason") is not None:
                    finish.append(choice["finish_reason"])
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    if first_content is None:
                        first_content = time.monotonic() - start
                    chunks.append(content)
        elapsed = time.monotonic() - start
        return {
            "content": "".join(chunks),
            "headers_s": round(headers_s, 4),
            "first_content_s": round(first_content, 4) if first_content is not None else None,
            "elapsed_s": round(elapsed, 4),
            "finish_reasons": finish,
            "done_seen": done,
            "usage": usage,
        }
    finally:
        con.close()


def source_from_run(run_result: dict):
    basis = run_result.get("security_basis") or {}
    pf = Path(basis.get("poc02_preflight", "")).resolve()
    if not pf.is_dir():
        raise ValueError("run result does not reference a valid POC02 preflight")
    preflight = p2.read(pf / "result.json")
    if preflight.get("status") != "PREFLIGHT_PASS_NOT_MODEL_EVAL":
        raise ValueError("referenced POC02 preflight is not passed")
    ref = p2.read(pf.parent / "reference.json")
    stage = Path(ref["staging_directory"]).resolve()
    source = stage / "input.log"
    if not source.is_file() or p2.sha(source.read_bytes()) != run_result.get("input_sha256"):
        raise ValueError("validated input.log no longer matches the POC03 run")
    return pf, source


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True, help="existing POC03 audit directory")
    ap.add_argument("--base-url", required=True, help="loopback OpenAI-compatible /v1 URL")
    ap.add_argument("--model", required=True)
    ap.add_argument("--focus", default=DEFAULT_FOCUS)
    ap.add_argument("--max-evidence", type=int, default=DEFAULT_MAX_EVIDENCE)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args(argv)

    run = args.run.resolve()
    if not run.is_dir() or not (run / "result.json").is_file():
        raise ValueError("--run must be a POC03 audit directory with result.json")
    if args.max_tokens < 128 or args.max_tokens > 768:
        raise ValueError("max-tokens must be between 128 and 768")
    if args.timeout < 30 or args.timeout > 600:
        raise ValueError("timeout must be between 30 and 600 seconds")

    result = load_json(run / "result.json")
    if result.get("synthetic_response") is not False or result.get("model") != args.model:
        raise ValueError("run/model mismatch or synthetic run")
    _pf, source = source_from_run(result)
    source_text = source.read_text(encoding="utf-8")
    calls, results = tool_history(run)
    trace = p3.collect_trace(run)
    knowledge = extract_knowledge(calls, results)
    catalog = build_catalog(source_text, results, args.focus, args.max_evidence)
    system_prompt, user_prompt = finalizer_prompts(args.focus, knowledge, catalog)

    # Use the same API-key environment selected by the validated POC02 baseline.
    basis_pf = Path(result["security_basis"]["poc02_preflight"]).resolve()
    _ref, _cfg, _case, key_env = p2.bundle(basis_pf)
    api_key = os.environ.get(key_env, "")

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
    out = run / ("finalize-" + stamp)
    out.mkdir(mode=0o700)
    save_json(out / "evidence-catalog.json", catalog)
    save_json(out / "request.json", body)

    final = {
        "status": "FINALIZER_NOT_PASSED",
        "automatic_retry": False,
        "source_run": str(run),
        "model": args.model,
        "focus": args.focus,
        "catalog_size": len(catalog),
        "trace_prerequisites": {
            "skill_read": trace.get("skill_read"),
            "knowledge_read": trace.get("knowledge_read"),
            "recovery_seen_in_tool_result": trace.get("recovery_seen_in_tool_result"),
        },
    }
    try:
        response = stream_finalizer(args.base_url, api_key, body, args.timeout)
        final["response"] = {k: v for k, v in response.items() if k != "content"}
        (out / "response.txt").write_text(response["content"], encoding="utf-8")
        if response["finish_reasons"] and response["finish_reasons"][-1] == "length":
            final["status"] = "FINALIZER_LENGTH_LIMIT"
        elif not response["done_seen"]:
            final["status"] = "FINALIZER_INCOMPLETE_STREAM"
        else:
            compact = parse_compact(response["content"])
            compact_errors = validate_compact(compact, catalog)
            final["compact_errors"] = compact_errors
            if compact_errors:
                final["status"] = "FINALIZER_SCHEMA_FAILED"
            else:
                expanded = expand_compact(compact, catalog)
                save_json(out / "compact-answer.json", compact)
                save_json(out / "final-answer.json", expanded)
                grade = p3.grade_answer(json.dumps(expanded, ensure_ascii=False), source_text, trace)
                final["grade"] = grade
                final["status"] = "PASS_POC03_TWO_PHASE" if grade.get("passed") else "FINALIZER_GRADE_FAILED"
    except Exception as exc:
        final["error"] = type(exc).__name__ + ": " + str(exc)[:500]

    save_json(out / "result.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2))
    print("finalizer audit:", out)
    return 0 if final["status"] == "PASS_POC03_TWO_PHASE" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC03_FINALIZE_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
