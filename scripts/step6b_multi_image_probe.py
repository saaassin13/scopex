#!/usr/bin/env python3
"""Validate a bounded many-image workflow with real OpenClaw + local vision model.

The fixture contains many immutable original images. OpenClaw must visually screen
that set, write its selected filenames to task scratch, and individually re-open
only the small original subset it relies on. ScopeX projects multi-image views as
screening only; singleton original views become SHA-verified image Evidence for
the existing Fresh Finalizer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.agent.docker_host import resolve_local_docker_host
from scopex.agent.runtime import OpenClawTaskRuntime, OpenClawTaskSpec
from scopex.agent.trace import load_audit_trace
from scopex.evidence.catalog import EvidenceCatalog
from scopex.evidence.collector import EvidenceCollector
from scopex.evidence.media import EvidenceMediaLoader
from scopex.evidence.projector import OpenClawEvidenceProjector
from scopex.events.progress import InMemoryEventSink
from scopex.finalizer.client import StreamingFinalizerClient
from scopex.finalizer.structured import StructuredFinalizer
from scopex.runtime.stop import SafeStopGate


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)


def write_square_png(path: Path, *, red: bool, width: int = 160, height: int = 160) -> None:
    bg = (235, 235, 235)
    fg = (235, 45, 45) if red else (45, 90, 220)
    lo_x, hi_x = width // 4, width * 3 // 4
    lo_y, hi_y = height // 4, height * 3 // 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            color = fg if lo_x <= x < hi_x and lo_y <= y < hi_y else bg
            raw.extend(color)
    payload = b"\x89PNG\r\n\x1a\n"
    payload += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    payload += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    payload += _png_chunk(b"IEND", b"")
    path.write_bytes(payload)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_fixture(images: Path, count: int) -> tuple[list[str], dict[str, str]]:
    images.mkdir(parents=True, exist_ok=True)
    # Spread the positives across the directory so one early sample cannot pass.
    positive_indexes = sorted({count // 7, count // 2, count - 5})
    expected: list[str] = []
    hashes: dict[str, str] = {}
    for i in range(count):
        name = f"frame_{i:03d}.png"
        path = images / name
        is_red = i in positive_indexes
        write_square_png(path, red=is_red)
        if is_red:
            expected.append(name)
        hashes[name] = sha256(path)
    return expected, hashes


def parse_wire_usage(audit_dir: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(audit_dir.glob("wire-*-response.bin")):
        raw = path.read_bytes().decode("utf-8", errors="replace")
        candidates = []
        if raw.strip().startswith("{"):
            try:
                candidates.append(json.loads(raw))
            except ValueError:
                pass
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                candidates.append(json.loads(body))
            except ValueError:
                continue
        for value in candidates:
            if isinstance(value, dict) and isinstance(value.get("usage"), dict) and value["usage"]:
                out.append(dict(value["usage"]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--sandbox-image", required=True)
    ap.add_argument("--openclaw-bin", type=Path, default=Path.home() / ".openclaw/bin/openclaw")
    ap.add_argument("--data-root", type=Path, default=ROOT / ".local" / "step6b-multi-image")
    ap.add_argument("--image-count", type=int, default=48)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--max-requests", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--finalizer-max-tokens", type=int, default=768)
    ap.add_argument("--api-key-env", default="SCOPEX_API_KEY")
    args = ap.parse_args(argv)

    if not sys.platform.startswith("linux") or os.geteuid() == 0:
        raise ValueError("run on Spark Linux as the ordinary user, not sudo")
    if not 12 <= args.image_count <= 120:
        raise ValueError("--image-count must be between 12 and 120")
    if not 120 <= args.timeout <= 1200:
        raise ValueError("--timeout must be between 120 and 1200")
    if not 4 <= args.max_requests <= 30:
        raise ValueError("--max-requests must be between 4 and 30")

    cli = args.openclaw_bin.expanduser().resolve()
    if not cli.is_file() or not os.access(cli, os.X_OK):
        raise ValueError("OpenClaw CLI is not executable")
    api_key = os.environ.get(args.api_key_env, "")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("invalid API key environment value")

    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    root = args.data_root.expanduser().resolve() / tag
    input_root = root / "input"
    images = input_root / "images"
    scratch = root / "scratch"
    workspace = root / "workspace"
    runtime_root = root / "runtime"
    audit_root = root / "agent-turns"
    for path in (root, input_root, images, scratch, workspace, runtime_root, audit_root):
        path.mkdir(parents=True, mode=0o700, exist_ok=True)

    expected, before_hashes = make_fixture(images, args.image_count)
    data_bind = f"{input_root}:/agent-data:ro"
    scratch_bind = f"{scratch}:/task-scratch:rw"
    task_id = "step6b-img-" + uuid.uuid4().hex[:10]
    agent_id = "sximg" + uuid.uuid4().hex[:8]
    session_key = f"agent:{agent_id}:{task_id}"

    task = f"""分析 /agent-data/images 中的 {args.image_count} 张 PNG 图片。
其中少量图片中央是明显红色方块，其余中央是蓝色方块。请找出所有中央红色方块图片的文件名。
必须实际使用 view_image 做视觉判断，不能只根据文件名猜测。调查方法由你决定。
将最终选择按文件名升序写入 /task-scratch/selected.json，格式严格为：
{{"red_images":["frame_xxx.png"]}}
完成后简短回复结果。"""

    events = InMemoryEventSink()
    spec = OpenClawTaskSpec(
        cli_path=cli,
        model_id=args.model,
        upstream_base_url=args.base_url,
        upstream_api_key=api_key,
        workspace=workspace,
        runtime_root=runtime_root,
        audit_root=audit_root,
        image=args.sandbox_image,
        docker_host=resolve_local_docker_host(),
        agent_id=agent_id,
        uid=os.getuid(),
        gid=os.getgid(),
        timeout_s=args.timeout,
        max_requests=args.max_requests,
        max_tokens=args.max_tokens,
        tools=("read", "exec", "process", "view_image"),
        sandbox_binds=(data_bind,),
        task_scratch_bind=scratch_bind,
        exec_host="sandbox",
        exec_mode="full",
        compaction_enabled=True,
    )
    runtime = OpenClawTaskRuntime(
        task_id=task_id,
        session_key=session_key,
        spec=spec,
        events=events,
        stop_gate=SafeStopGate(),
    )

    result = {
        "status": "STEP6B_MULTI_IMAGE_FAILED",
        "root": str(root),
        "model": args.model,
        "image_count": args.image_count,
        "expected_red_images": expected,
        "data_bind": data_bind,
        "scratch_bind": scratch_bind,
    }

    try:
        turn = runtime.run_turn(task, turn_name="turn-001")
        completed = bool(
            turn.process.returncode == 0
            and turn.process.stop_reason is None
            and turn.cli_outcome is not None
            and turn.cli_outcome.completed
        )
        trace = load_audit_trace(turn.audit_dir)
        image_calls = [call for call in trace.calls if call.name == "view_image"]
        call_shapes = []
        batch_calls = 0
        singleton_calls = 0
        for call in image_calls:
            paths = []
            one = call.arguments.get("path")
            if isinstance(one, str) and one:
                paths.append(one)
            many = call.arguments.get("paths")
            if isinstance(many, list):
                paths.extend(x for x in many if isinstance(x, str) and x)
            paths = list(dict.fromkeys(paths))
            if len(paths) > 1:
                batch_calls += 1
            elif len(paths) == 1:
                singleton_calls += 1
            call_shapes.append({"tool_call_id": call.id, "path_count": len(paths), "paths": paths})

        catalog = EvidenceCatalog(task_id, session_key)
        projector = OpenClawEvidenceProjector(
            EvidenceCollector(catalog, InMemoryEventSink()),
            sandbox_binds=(data_bind,),
            exec_host="sandbox",
        )
        projector.process_trace(trace)
        image_evidence = [item for item in catalog.items if item.metadata.get("evidence_type") == "image"]
        image_sources = [item.source for item in image_evidence]
        evidence_names = sorted(Path(source).name for source in image_sources)

        selected_path = scratch / "selected.json"
        selected_error = None
        actual = None
        try:
            actual = json.loads(selected_path.read_text(encoding="utf-8"))
        except Exception as exc:
            selected_error = type(exc).__name__ + ": " + str(exc)
        actual_names = sorted(actual.get("red_images", [])) if isinstance(actual, dict) and isinstance(actual.get("red_images"), list) else []
        selection_correct = actual_names == expected
        expected_evidence_present = set(expected).issubset(set(evidence_names))
        claim_grade_bounded = len(image_evidence) <= 4
        source_unchanged = all(sha256(images / name) == digest for name, digest in before_hashes.items())

        finalizer_valid = False
        finalizer_error = None
        finalizer_image_refs = []
        finalizer_rendered = None
        if image_evidence and claim_grade_bounded and expected_evidence_present:
            finalizer = StructuredFinalizer(
                StreamingFinalizerClient(args.base_url, api_key=api_key, timeout_s=180),
                model=args.model,
                max_tokens=args.finalizer_max_tokens,
                media_loader=EvidenceMediaLoader((data_bind,), max_images=4),
            )
            final = finalizer.run(user_request=task, catalog=catalog)
            finalizer_valid = final.valid
            finalizer_error = final.parse_error or (
                ",".join(final.finalization.errors) if final.finalization is not None and final.finalization.errors else None
            )
            finalizer_image_refs = list(final.image_evidence_refs)
            finalizer_rendered = final.finalization.rendered if final.finalization is not None else None

        usages = parse_wire_usage(turn.audit_dir)
        largest_prompt_tokens = max(
            (int(u.get("prompt_tokens", 0)) for u in usages if isinstance(u.get("prompt_tokens"), (int, float))),
            default=0,
        )
        result.update({
            "completed": completed,
            "answer": turn.cli_outcome.answer if turn.cli_outcome else None,
            "wall_s": turn.process.wall_s,
            "forwarded_requests": sum(1 for row in turn.proxy_records if row.get("forwarded") is True),
            "wire_usage": usages,
            "largest_prompt_tokens": largest_prompt_tokens,
            "view_image_calls": len(image_calls),
            "batch_image_calls": batch_calls,
            "singleton_image_calls": singleton_calls,
            "image_call_shapes": call_shapes,
            "image_evidence_count": len(image_evidence),
            "image_evidence_sources": image_sources,
            "claim_grade_bounded": claim_grade_bounded,
            "expected_evidence_present": expected_evidence_present,
            "selected_path": str(selected_path),
            "selected_error": selected_error,
            "actual_red_images": actual_names,
            "selection_correct": selection_correct,
            "source_unchanged": source_unchanged,
            "finalizer_valid": finalizer_valid,
            "finalizer_error": finalizer_error,
            "finalizer_image_refs": finalizer_image_refs,
            "finalizer_rendered": finalizer_rendered,
        })
        passed = all((
            completed,
            selection_correct,
            source_unchanged,
            batch_calls >= 1,
            claim_grade_bounded,
            expected_evidence_present,
            finalizer_valid,
        ))
        if passed:
            result["status"] = "PASS_STEP6B_MULTI_IMAGE_WORKING_SET"
        else:
            failures = []
            for name, ok in (
                ("completed", completed),
                ("selection_correct", selection_correct),
                ("source_unchanged", source_unchanged),
                ("batch_screening_used", batch_calls >= 1),
                ("claim_grade_bounded", claim_grade_bounded),
                ("expected_evidence_present", expected_evidence_present),
                ("finalizer_valid", finalizer_valid),
            ):
                if not ok:
                    failures.append(name)
            result["failure_reasons"] = failures
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:1600]
    finally:
        try:
            cleanup = runtime.close()
            result["sandbox_cleanup"] = {
                "container_ids": list(cleanup.container_ids),
                "warnings": list(cleanup.warnings),
            }
        except Exception as exc:
            result["cleanup_error"] = type(exc).__name__ + ": " + str(exc)[:500]
        save_json(root / "result.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("Step 6B multi-image audit:", root, file=sys.stderr)

    return 0 if result.get("status") == "PASS_STEP6B_MULTI_IMAGE_WORKING_SET" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print("STEP6B_MULTI_IMAGE_SETUP_ERROR:", str(exc), file=sys.stderr)
        raise SystemExit(2)
