#!/usr/bin/env python3
"""Probe the configured local model's image-count capacity before business runs.

Uses tiny generated PNGs. A PASS proves request capacity/transport only, not
full-resolution context fit, image quality judgement or semantic correctness.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scopex.finalizer.client import StreamingFinalizerClient
from scopex.model_capabilities import IMAGE_LIMIT_ENV, resolve_image_limit


def tiny_png(index: int) -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    rgb = bytes(((32 + index * 13) % 256, 96, 160))
    raw = (b'\x00' + rgb * 64) * 64
    data = b'\x89PNG\r\n\x1a\n'
    data += chunk(b'IHDR', struct.pack('!IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


def check_capacity(*, base_url: str, model: str, images: int, api_key: str = '', timeout_s: int = 60) -> dict:
    images = resolve_image_limit(images)
    result = {
        'check': 'model_image_prompt_capacity',
        'model': model,
        'requested_images': images,
        'accepted': False,
        'verification_scope': 'tiny_image_transport_only_not_visual_quality',
    }
    try:
        client = StreamingFinalizerClient(base_url, api_key=api_key, timeout_s=timeout_s)
        response = client.complete(
            model=model,
            system_prompt='这是本机部署的图片容量检查。只回复 OK。',
            user_prompt='仅确认请求成功，不需要分析测试图片。',
            max_tokens=16,
            image_inputs=tuple((f'capacity-probe-{i+1}', tiny_png(i)) for i in range(images)),
        )
        # A length finish is sufficient here: there is deliberately no diagnosis
        # or report to publish, only an input-capacity/transport check.
        result['accepted'] = bool(response.done_seen and response.finish_reasons and response.finish_reasons[-1] in ('stop', 'length'))
        result['finish_reasons'] = list(response.finish_reasons)
        result['elapsed_s'] = response.elapsed_s
        if not result['accepted']:
            result['error'] = 'incomplete_capacity_probe'
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {str(exc)[:600]}'
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-url', default='http://127.0.0.1:18002/v1')
    ap.add_argument('--model', required=True, help='real served model ID from /v1/models')
    ap.add_argument('--images', type=int, default=None)
    ap.add_argument('--timeout', type=int, default=60)
    args = ap.parse_args(argv)
    if not 1 <= args.timeout <= 180:
        ap.error('--timeout must be between 1 and 180 seconds')
    try:
        images = resolve_image_limit(args.images)
    except ValueError as exc:
        ap.error(str(exc))
    result = check_capacity(base_url=args.base_url, model=args.model, images=images,
                            api_key=os.environ.get('SCOPEX_API_KEY', ''), timeout_s=args.timeout)
    result['runtime_profile_environment'] = f'{IMAGE_LIMIT_ENV}={images}'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
