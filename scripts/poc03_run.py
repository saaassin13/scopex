#!/usr/bin/env python3
"""POC03: one real OpenClaw business-diagnosis task with a workspace Skill.

Reuses the passed POC02 native preflight for the validated runtime/sandbox shape,
but stages a fresh read-only workspace containing only input.log and the POC03
skill. The model chooses its own read/exec investigation path. No expected
answer or grading fixture is mounted into the sandbox.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "cow-disinfect-diagnosis"
SKILL_MARKER = "SCOPEX_DIAGNOSIS_SKILL_V1"
KNOWLEDGE_MARKER = "SCOPEX_COW_DISINFECT_KNOWLEDGE_V1"
SKILL_SOURCE = ROOT / "skills" / SKILL_NAME

TASK = """分析 /agent/input.log 中 2026-09-11 07:59:21 左右出现的 `CalLeftCamStartFollowPt failed`。

目标：
1. 说明日志能直接证明的触发条件；
2. 判断在当前日志窗口内它是 transient、persistent 还是 unknown；
3. 判断后续是否恢复，并给出证据；
4. 区分 facts、inferences、unknowns，不把推断写成事实；
5. 给出结论和置信度。

不要只搜索 ERROR 后停止；需要检查足够的前后记录来判断上下游关系和恢复情况。可以使用环境提供的 Skill 和工具，自主选择调查方法，不要求固定命令或固定读取顺序。

最终只输出一个 JSON 对象，不要 Markdown 代码块：
{
  "direct_trigger": "...",
  "persistence": "transient|persistent|unknown",
  "recovery": {"observed": true|false|null, "raw_line": "精确原始日志行或空字符串"},
  "evidence": [
    {"role": "trigger|upstream|failure|recovery|context", "raw_line": "精确原始日志行"}
  ],
  "facts": ["..."],
  "inferences": ["..."],
  "unknowns": ["..."],
  "conclusion": "...",
  "confidence": "high|medium|low"
}

所有 evidence.raw_line 必须逐字来自 /agent/input.log。业务知识只能帮助解释字段，不能当作事件证据。"""


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def text_content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            p.get("text", "") for p in value
            if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
    return ""


def parse_answer(answer: str):
    raw = answer.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", raw, re.I)
    if fenced:
        raw = fenced.group(1).strip()
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("final answer must be a JSON object")
    return obj


def find_line_index(lines: list[str], needle: str) -> int | None:
    try:
        return lines.index(needle)
    except ValueError:
        return None


def unsupported_causal_claims(obj) -> list[str]:
    parts = []
    for key in ("inferences", "conclusion"):
        value = obj.get(key)
        if isinstance(value, list):
            parts.extend(str(x) for x in value)
        elif isinstance(value, str):
            parts.append(value)
    text = "\n".join(parts)
    patterns = [
        r"(?:由于|因为|由).{0,24}(相机损坏|网络故障|硬件故障|模型失效|AI模型失效).{0,16}(?:导致|造成|引起|所以)",
        r"(相机损坏|网络故障|硬件故障|模型失效|AI模型失效).{0,16}(?:导致|造成|引起)",
        r"(?:camera damage|network failure|hardware failure|model failure).{0,24}(?:caused|causes|therefore)",
    ]
    hits = []
    for pattern in patterns:
        hits.extend(m.group(0) for m in re.finditer(pattern, text, re.I))
    return hits


def collect_trace(out: Path) -> dict:
    calls: dict[str, dict] = {}
    results: dict[str, str] = {}
    for path in sorted(out.glob("wire-*-request.json")):
        payload = json_load(path)
        for message in payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict) or not isinstance(call.get("id"), str):
                        continue
                    func = call.get("function") or {}
                    args = {}
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except (ValueError, TypeError):
                        pass
                    calls[call["id"]] = {"name": func.get("name"), "arguments": args}
            elif message.get("role") == "tool" and isinstance(message.get("tool_call_id"), str):
                results[message["tool_call_id"]] = text_content(message.get("content"))

    rows = []
    skill_read = False
    knowledge_read = False
    recovery_seen = False
    for cid, call in calls.items():
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        path = args.get("path", args.get("file_path"))
        content = results.get(cid, "")
        if call.get("name") == "read" and isinstance(path, str):
            skill_read |= path.endswith(f"/skills/{SKILL_NAME}/SKILL.md") and SKILL_MARKER in content
            knowledge_read |= path.endswith(
                f"/skills/{SKILL_NAME}/references/cow-disinfect-log.md"
            ) and KNOWLEDGE_MARKER in content
        recovery_seen |= "CalLeftCamStartFollowPt succeeded" in content
        rows.append({
            "id": cid,
            "name": call.get("name"),
            "arguments": args,
            "has_return": cid in results,
            "return_chars": len(content),
        })
    return {
        "tool_calls": rows,
        "tool_call_count": len(rows),
        "skill_read": skill_read,
        "knowledge_read": knowledge_read,
        "recovery_seen_in_tool_result": recovery_seen,
    }


def grade_answer(answer: str, source_text: str, trace: dict) -> dict:
    checks = {}
    errors = []
    try:
        obj = parse_answer(answer)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"passed": False, "schema_ok": False, "errors": [str(exc)]}

    checks["schema_ok"] = (
        isinstance(obj.get("direct_trigger"), str)
        and obj.get("persistence") in {"transient", "persistent", "unknown"}
        and isinstance(obj.get("recovery"), dict)
        and obj["recovery"].get("observed") in {True, False, None}
        and isinstance(obj["recovery"].get("raw_line"), str)
        and isinstance(obj.get("evidence"), list)
        and isinstance(obj.get("facts"), list)
        and isinstance(obj.get("inferences"), list)
        and isinstance(obj.get("unknowns"), list)
        and isinstance(obj.get("conclusion"), str)
        and obj.get("confidence") in {"high", "medium", "low"}
    )
    if not checks["schema_ok"]:
        errors.append("output schema mismatch")

    lines = source_text.splitlines()
    evidence_lines = []
    evidence_schema = True
    for item in obj.get("evidence", []):
        if not isinstance(item, dict) or not isinstance(item.get("raw_line"), str):
            evidence_schema = False
            continue
        evidence_lines.append(item["raw_line"])
    recovery_raw = obj.get("recovery", {}).get("raw_line", "") if isinstance(obj.get("recovery"), dict) else ""
    if recovery_raw:
        evidence_lines.append(recovery_raw)

    checks["evidence_schema_ok"] = evidence_schema and bool(evidence_lines)
    checks["all_evidence_exact_source_lines"] = bool(evidence_lines) and all(
        line in lines for line in evidence_lines
    )

    direct_candidates = [
        line for line in evidence_lines
        if "Cal StartFollowPt failed, left knee and left leg is not detected" in line
    ]
    failure_candidates = [
        line for line in evidence_lines if "CalLeftCamStartFollowPt failed" in line
    ]
    recovery_candidates = [
        line for line in evidence_lines if "CalLeftCamStartFollowPt succeeded" in line
    ]
    checks["direct_trigger_evidence"] = bool(direct_candidates)
    checks["failure_evidence"] = bool(failure_candidates)
    checks["recovery_evidence"] = bool(recovery_candidates)

    trigger_idx = find_line_index(lines, direct_candidates[0]) if direct_candidates else None
    recovery_idx = find_line_index(lines, recovery_candidates[0]) if recovery_candidates else None
    checks["recovery_after_failure"] = (
        trigger_idx is not None and recovery_idx is not None and recovery_idx > trigger_idx
    )
    checks["classified_transient"] = obj.get("persistence") == "transient"
    checks["recovery_observed_true"] = (
        isinstance(obj.get("recovery"), dict) and obj["recovery"].get("observed") is True
    )
    checks["facts_inferences_unknowns_separated"] = (
        isinstance(obj.get("facts"), list)
        and isinstance(obj.get("inferences"), list)
        and isinstance(obj.get("unknowns"), list)
        and len(obj.get("unknowns", [])) >= 1
    )
    claims = unsupported_causal_claims(obj)
    checks["no_unsupported_root_cause"] = not claims
    checks["skill_read"] = trace.get("skill_read") is True
    checks["knowledge_read"] = trace.get("knowledge_read") is True
    checks["recovery_actually_seen_by_agent"] = trace.get("recovery_seen_in_tool_result") is True

    for key, ok in checks.items():
        if not ok:
            errors.append(key)
    return {
        "passed": all(checks.values()),
        "schema_ok": checks["schema_ok"],
        "checks": checks,
        "unsupported_causal_claims": claims,
        "errors": errors,
    }


def stage_workspace(source_input: Path, workspace: Path) -> dict:
    if not SKILL_SOURCE.is_dir():
        raise ValueError(f"missing repo skill: {SKILL_SOURCE}")
    skill = SKILL_SOURCE / "SKILL.md"
    knowledge = SKILL_SOURCE / "references" / "cow-disinfect-log.md"
    for p in (source_input, skill, knowledge):
        if p.is_symlink() or not p.is_file():
            raise ValueError(f"required regular file missing: {p}")

    forbidden = [
        "07:59:21:638", "07:59:21:639", "07:59:21:847",
        "Cal StartFollowPt failed, left knee and left leg is not detected",
        "CalLeftCamStartFollowPt succeeded",
    ]
    visible_knowledge = skill.read_text(encoding="utf-8") + "\n" + knowledge.read_text(encoding="utf-8")
    leaked = [item for item in forbidden if item in visible_knowledge]
    if leaked:
        raise ValueError("skill/reference leaks case fixture: " + ", ".join(leaked))

    workspace.mkdir(parents=True, mode=0o700)
    shutil.copy2(source_input, workspace / "input.log")
    target = workspace / "skills" / SKILL_NAME
    (target / "references").mkdir(parents=True, mode=0o700)
    shutil.copy2(skill, target / "SKILL.md")
    shutil.copy2(knowledge, target / "references" / "cow-disinfect-log.md")
    return {
        "input_sha256": sha_bytes((workspace / "input.log").read_bytes()),
        "skill_sha256": sha_bytes((target / "SKILL.md").read_bytes()),
        "knowledge_sha256": sha_bytes((target / "references" / "cow-disinfect-log.md").read_bytes()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True,
                        help="passed POC02 native-preflight directory")
    parser.add_argument("--model", required=True, help="currently served model ID")
    parser.add_argument("--base-url", required=True, help="loopback OpenAI-compatible /v1 URL")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-requests", type=int, default=8)
    parser.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    args = parser.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run as ordinary Spark Linux user, not sudo")
    if args.timeout < 60 or args.timeout > 900:
        raise ValueError("timeout must be between 60 and 900 seconds")
    if args.max_requests < 2 or args.max_requests > 12:
        raise ValueError("max-requests must be between 2 and 12")

    import poc02_preflight as native
    import poc02_run as p2

    os.umask(0o077)
    pf = args.preflight.resolve()
    old_ref, old_cfg, _old_case, key_env = p2.bundle(pf)
    source_stage = native.check_input(old_ref)
    source_input = source_stage / "input.log"
    p2.endpoint(args.base_url)

    if not re.fullmatch(r"[A-Za-z0-9._:/@+\-]{1,200}", args.model):
        raise ValueError("invalid served model ID")
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key_env):
        raise ValueError("invalid API key environment name")
    api_key = os.environ.get(key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid local API key")

    cli = args.openclaw_bin.absolute()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("known OpenClaw CLI not executable")
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("existing Docker CLI required")

    lock = (pf.parent / "poc03-real-task.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise ValueError("another POC03 real task is running")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = pf.parent / ("poc03-" + tag)
    out.mkdir(mode=0o700)
    runtime = Path.home() / "scopex-poc03-work" / tag
    for d in (runtime / "home", runtime / "state", runtime / "sandboxes"):
        d.mkdir(parents=True, mode=0o700)
    workspace = runtime / "workspace"

    result = {
        "status": "SETUP_FAILED",
        "stage": "setup",
        "errors": [],
        "functional_pass": False,
        "synthetic_response": False,
        "automatic_retry": False,
    }
    server = None
    thread = None
    owned = []
    env = {}
    gate_passed = False

    try:
        staged = stage_workspace(source_input, workspace)
        result["staged"] = staged
        if staged["input_sha256"] != old_ref["input_sha256"]:
            raise ValueError("staged input differs from validated POC02 input")

        ref = copy.deepcopy(old_ref)
        ref["model"] = args.model
        ref["base_url"] = args.base_url
        ref["staging_directory"] = str(workspace.resolve())
        ref["timeout_s"] = args.timeout
        ref["sla_s"] = args.timeout

        metadata_env = {
            k: os.environ[k] for k in
            ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
            if k in os.environ
        }
        host = metadata_env.get("DOCKER_HOST", "")
        if metadata_env.get("DOCKER_CONTEXT") or not host:
            host = p2.load(native.run_command(
                [docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
                metadata_env, out, out, "poc03-docker-context"
            ))
        if not isinstance(host, str) or not host.startswith("unix://"):
            raise ValueError("local Docker socket required")

        config_path = out / "openclaw.json"
        env = native.clean_env(runtime, config_path, host)
        image_id = old_cfg["agents"]["defaults"]["sandbox"]["docker"]["image"]
        native.run_command(
            [docker, "image", "inspect", image_id, "--format", "{{.Id}}"],
            env, out, out, "poc03-image-check"
        )

        info = p2.model_info(ref, api_key)
        result["served_model"] = info
        if info.get("max_model_len") is not None and info["max_model_len"] < 32768:
            raise ValueError("served model context shorter than 32768")

        agent_id = "sx3" + uuid.uuid4().hex[:9]
        token = secrets.token_urlsafe(24)
        prefix = "sxpoc03-" + agent_id + "-"
        result["sandbox_prefix"] = prefix

        def gate():
            nonlocal gate_passed
            if gate_passed:
                return
            ids = native.run_command(
                [docker, "ps", "-aq", "--filter", "name=" + prefix],
                env, out, out, "poc03-sandbox-list"
            ).split()
            if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
                raise ValueError("one native sandbox must exist before first inference")
            item = p2.load(native.run_command(
                [docker, "inspect", ids[0], "--format", "{{json .}}"],
                env, out, out, "poc03-sandbox-inspect"
            ))
            owned.append(ids[0])
            boundary = native.check_container(
                item, workspace.resolve(), runtime / "sandboxes", prefix, image_id, os.getuid()
            )
            result["sandbox"] = boundary
            if boundary["problems"]:
                raise ValueError("sandbox boundary mismatch")
            checks = (
                "set -eu; "
                "test -r /agent/input.log; "
                f"test -r /agent/skills/{SKILL_NAME}/SKILL.md; "
                f"test -r /agent/skills/{SKILL_NAME}/references/cow-disinfect-log.md; "
                f"test ! -e {shlex.quote(str(ROOT))}; "
                "test ! -e /var/run/docker.sock; "
                "sha256sum /agent/input.log "
                f"/agent/skills/{SKILL_NAME}/SKILL.md "
                f"/agent/skills/{SKILL_NAME}/references/cow-disinfect-log.md"
            )
            raw = native.run_command(
                [docker, "exec", ids[0], "/bin/sh", "-c", checks],
                env, out, out, "poc03-boundary-read"
            )
            hashes = [line.split()[0] for line in raw.splitlines() if line.strip()]
            expected = [staged["input_sha256"], staged["skill_sha256"], staged["knowledge_sha256"]]
            if hashes != expected:
                raise ValueError("sandbox input/skill hashes mismatch")
            gate_passed = True

        server = p2.Recorder(out, ref, api_key, token, native, gate, args.max_requests)
        cfg = native.build_config(
            ref, runtime, out, f"http://127.0.0.1:{server.server_port}/v1", token,
            image_id, agent_id, os.getuid(), os.getgid()
        )
        cfg["agents"]["defaults"]["skills"] = [SKILL_NAME]
        cfg["agents"]["entries"][agent_id]["skills"] = [SKILL_NAME]
        p2.save(config_path, cfg)

        version = native.run_command([str(cli), "--version"], env, runtime, out, "poc03-version")
        if not re.search(r"2026\.9\.2\s+\(3928bad\)", version):
            raise ValueError("OpenClaw version changed; POC03 targets validated 2026.9.2 (3928bad)")
        active = native.run_command([str(cli), "config", "file"], env, runtime, out, "poc03-config-file")
        if str(config_path) not in [line.strip() for line in active.splitlines()]:
            raise ValueError("isolated config path mismatch")
        native.run_command([str(cli), "config", "validate"], env, runtime, out, "poc03-config-validate")
        skills = native.run_command([str(cli), "skills", "list"], env, runtime, out, "poc03-skills-list")
        if SKILL_NAME not in skills:
            raise ValueError("workspace skill not visible to isolated agent")
        result["skill_catalog_visible"] = True

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        result.update(
            status="NOT_COMPLETED",
            stage="real_agent",
            model=args.model,
            base_url=args.base_url,
            task_sha256=sha_bytes(TASK.encode("utf-8")),
            input_sha256=staged["input_sha256"],
            timeout_s=args.timeout,
            request_limit=args.max_requests,
        )
        print("[poc03] starting ONE real diagnosis task; no automatic retry", flush=True)
        timing = p2.run_task(
            cli, config_path, env, runtime, out, agent_id, TASK, args.timeout, server
        )
        result.update(timing)

        if timing["stop"] or timing["returncode"] != 0:
            result["status"] = "NOT_COMPLETED"
        else:
            cli_text = (out / "agent.stdout.txt").read_text(encoding="utf-8")
            outcome = p2.cli_outcome(cli_text)
            result["runtime_flags"] = {
                k: v for k, v in outcome.items() if k not in ("answer", "visible_payloads")
            }
            answer, payload_count = p2.extract_answer(cli_text)
            (out / "answer.txt").write_text(answer, encoding="utf-8")
            trace = collect_trace(out)
            p2.save(out / "tool-trace.json", trace)
            grade = grade_answer(answer, (workspace / "input.log").read_text(encoding="utf-8"), trace)
            result["visible_payloads"] = payload_count
            result["trace"] = trace
            result["grade"] = grade
            wire_ok = bool(server.records) and all(
                r.get("forwarded") and r.get("http_status") == 200
                and r.get("response_complete") and not r.get("error_type")
                for r in server.records
            )
            result["wire_ok"] = wire_ok
            result["functional_pass"] = bool(wire_ok and gate_passed and grade["passed"])
            result["status"] = (
                "PASS_POC03_SINGLE_CASE" if result["functional_pass"]
                else "SKILL_OR_EVIDENCE_NOT_PROVED" if grade.get("schema_ok")
                else "NOT_PASSED"
            )

        native.check_input(old_ref)
        result["stage"] = "complete"
    except Exception as exc:
        result["errors"].append(type(exc).__name__ + ": " + str(exc)[:400])
    except KeyboardInterrupt:
        result["errors"].append("operator interrupted")
        result["status"] = "INTERRUPTED"
    finally:
        if server:
            server.cancel()
            if thread:
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()
            result["wire"] = list(server.records)
            result["model_request_attempts"] = sum(bool(r.get("forwarded")) for r in server.records)
            for row in result["wire"]:
                response = out / f"wire-{row['index']:02d}-response.bin" if "index" in row else None
                if response and response.is_file():
                    row["response"] = p2.response_metadata(response, row.get("content_type", ""))
        for cid in owned:
            try:
                native.run_command(
                    [docker, "stop", "--time", "2", cid], env, out, out,
                    "poc03-stop-" + cid[:12], 10
                )
            except Exception:
                result["errors"].append("owned sandbox stop failed: " + cid)
        if result["errors"]:
            result["functional_pass"] = False
            if result["status"] == "PASS_POC03_SINGLE_CASE":
                result["status"] = "NOT_PASSED"
        result["audit_directory"] = str(out)
        result["runtime_directory"] = str(runtime)
        p2.save(out / "result.json", result)
        summary = (
            "# POC03 Skill 业务诊断单次验证\n\n"
            "```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n\n"
            "只评估一次功能可行性，不计重复率。Skill/知识文件不含本案例答案；"
            "原始 wire、工具返回与答案仅留本机审计目录。\n"
        )
        (out / "summary.md").write_text(summary, encoding="utf-8")
        print(summary)
        print("报告：", out / "summary.md")
        lock.close()

    return 0 if result["functional_pass"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as exc:
        print("POC03_SETUP_ERROR:", str(exc), file=sys.stderr)
        sys.exit(2)
