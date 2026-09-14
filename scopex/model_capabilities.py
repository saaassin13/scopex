"""One bounded image-capacity profile for investigation and fresh validation.

The limit is per complete model request, not per view_image call. This module
never removes images, rewrites conversation history or increases server limits.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

IMAGE_LIMIT_ENV = "SCOPEX_MAX_IMAGES_PER_PROMPT"
DEFAULT_MAX_IMAGES_PER_PROMPT = 4
MAX_SUPPORTED_IMAGES_PER_PROMPT = 12
MAX_IMAGES_PER_TOOL_CALL = 2


def resolve_image_limit(value: int | None = None) -> int:
    if value is None:
        raw = os.environ.get(IMAGE_LIMIT_ENV, str(DEFAULT_MAX_IMAGES_PER_PROMPT))
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{IMAGE_LIMIT_ENV} must be an integer between 1 and 12") from exc
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_SUPPORTED_IMAGES_PER_PROMPT:
        raise ValueError(f"image prompt limit must be between 1 and {MAX_SUPPORTED_IMAGES_PER_PROMPT}")
    return value


def count_request_images(payload: Mapping[str, Any]) -> int:
    """Count attachments across *all* messages, including replayed tool images.

    Duplicate URLs still count: servers constrain image items, not distinct
    filenames. Text mentioning a path is not an image attachment.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    count = 0
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        parts = message.get("content")
        if not isinstance(parts, list):
            continue
        count += sum(
            1 for part in parts
            if isinstance(part, Mapping)
            and part.get("type") in ("image_url", "input_image", "image")
        )
    return count


def validate_request_images(payload: Mapping[str, Any], maximum: int) -> None:
    maximum = resolve_image_limit(maximum)
    count = count_request_images(payload)
    if count > maximum:
        raise ValueError(
            f"image_prompt_capacity_exceeded:{count}>{maximum}; "
            "the limit counts images across all messages, not per tool call; "
            "align the ScopeX profile and vLLM --limit-mm-per-prompt"
        )


def render_image_capacity_context() -> str:
    maximum = resolve_image_limit()
    per_call = min(MAX_IMAGES_PER_TOOL_CALL, maximum)
    return (
        f"Image capacity: at most {maximum} image attachments in the entire model prompt "
        f"(all history messages combined), and at most {per_call} originals per view_image call. "
        "Repeated calls accumulate images; splitting a batch does not reset the prompt limit. "
        f"Plan a representative set of at most {maximum} originals for this run, avoid reopening "
        "the same image, and do not exceed the remaining cumulative allowance. "
        "The Fresh Finalizer uses the same capacity and re-opens the admitted originals. "
        "If this bounded sample is insufficient, report limited coverage rather than claiming "
        "the whole time window is normal. Do not replace visual judgement with image metrics."
    )
