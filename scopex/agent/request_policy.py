from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from typing import Any

from scopex.agent.model_proxy import (
    CONTEXT_BUDGET_EXCEEDED,
    CONTEXT_ESTIMATION_UNAVAILABLE,
    ContextBudgetExceeded,
    RequestRejected,
)
from scopex.model_capabilities import count_request_images, resolve_image_limit, validate_request_images


CONTEXT_ESTIMATOR_VERSION = "utf8-json-upper-bound-v2"
DEFAULT_CONTEXT_WINDOW = 32768
DEFAULT_CONTEXT_SAFETY_MARGIN = 1024
DEFAULT_IMAGE_TOKEN_RESERVE = 1024
_JSON_BYTES_PER_TOKEN = 2
_REQUEST_TEMPLATE_OVERHEAD = 128
_IMAGE_BLOCK_TYPES = ("image_url", "input_image", "image")


def _image_payload_chars(payload: dict[str, Any]) -> int:
    """Char size of image attachment data already priced as image tokens.

    Base64 data URIs are transport encoding; the serving stack counts them as
    vision tokens, not text tokens. count_request_images prices each block via
    image_token_reserve, so the same chars must not also inflate the text
    upper bound (double billing rejected in-flight image turns that fit).
    Blocks without a string URL keep their conservative text treatment.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return 0
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        parts = message.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") not in _IMAGE_BLOCK_TYPES:
                continue
            holder = part.get("image_url")
            if not isinstance(holder, dict):
                holder = part.get("input_image")
            if isinstance(holder, dict) and isinstance(holder.get("url"), str):
                total += len(holder["url"])
    return total


@dataclass(frozen=True, slots=True)
class RequestBudget:
    estimated_input_tokens: int
    output_tokens: int
    context_window: int
    safety_margin: int
    image_count: int
    image_tokens: int
    estimate_method: str = CONTEXT_ESTIMATOR_VERSION

    @property
    def projected_tokens(self) -> int:
        return self.estimated_input_tokens + self.output_tokens + self.safety_margin

    @property
    def available_input_tokens(self) -> int:
        return self.context_window - self.output_tokens - self.safety_margin

    @property
    def fits(self) -> bool:
        return self.projected_tokens <= self.context_window

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimate_method": self.estimate_method,
            "estimated_input_tokens": self.estimated_input_tokens,
            "output_tokens": self.output_tokens,
            "context_window": self.context_window,
            "safety_margin": self.safety_margin,
            "available_input_tokens": self.available_input_tokens,
            "projected_tokens": self.projected_tokens,
            "image_count": self.image_count,
            "image_tokens": self.image_tokens,
            "fits": self.fits,
        }


@dataclass(frozen=True, slots=True)
class OpenClawRequestPolicy:
    model_id: str
    max_tokens: int
    approved_tools: frozenset[str] = frozenset({"read", "exec", "process"})

    max_images_per_prompt: int = field(default_factory=resolve_image_limit)
    context_window: int = DEFAULT_CONTEXT_WINDOW
    context_safety_margin: int = DEFAULT_CONTEXT_SAFETY_MARGIN
    image_token_reserve: int = DEFAULT_IMAGE_TOKEN_RESERVE

    def estimate(self, payload: dict[str, Any]) -> RequestBudget:
        if self.context_window <= 0 or self.context_safety_margin < 0 or self.image_token_reserve < 0:
            raise ValueError("context budget settings must be non-negative and context_window positive")
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ContextBudgetExceeded(
                "request context cannot be safely estimated",
                code=CONTEXT_ESTIMATION_UNAVAILABLE,
                details={
                    "estimate_method": CONTEXT_ESTIMATOR_VERSION,
                    "reason": type(exc).__name__,
                },
            ) from exc
        image_count = count_request_images(payload)
        image_tokens = image_count * self.image_token_reserve
        text_chars = max(0, len(encoded) - _image_payload_chars(payload))
        estimated_input = (
            math.ceil(text_chars / _JSON_BYTES_PER_TOKEN)
            + _REQUEST_TEMPLATE_OVERHEAD
            + image_tokens
        )
        return RequestBudget(
            estimated_input_tokens=estimated_input,
            output_tokens=self.max_tokens,
            context_window=self.context_window,
            safety_margin=self.context_safety_margin,
            image_count=image_count,
            image_tokens=image_tokens,
        )

    def validate(self, payload: dict[str, Any]) -> RequestBudget:
        """Validate runtime invariants and conservatively bound model context."""

        problems: list[str] = []
        if payload.get("model") != self.model_id:
            problems.append("model mismatch")

        thinking = payload.get("chat_template_kwargs")
        if not isinstance(thinking, dict) or thinking.get("enable_thinking") is not False:
            problems.append("thinking must be disabled")

        if payload.get("max_tokens") != self.max_tokens:
            problems.append("max_tokens mismatch")
        if "max_completion_tokens" in payload:
            problems.append("conflicting max_completion_tokens")

        rows = payload.get("tools")
        if rows is not None:
            if not isinstance(rows, list):
                problems.append("tools malformed")
            else:
                names: list[str | None] = []
                for row in rows:
                    if not isinstance(row, dict):
                        names.append(None)
                        continue
                    function = row.get("function") or {}
                    name = function.get("name") if isinstance(function, dict) else None
                    names.append(name if isinstance(name, str) and name else None)

                valid_names = [name for name in names if name is not None]
                if (
                    len(valid_names) != len(names)
                    or len(valid_names) != len(set(valid_names))
                    or not set(valid_names).issubset(self.approved_tools)
                ):
                    problems.append("tool surface mismatch")

        if payload.get("tool_choice") not in (None, "auto"):
            problems.append("tool_choice must be auto/omitted")

        try:
            validate_request_images(payload, self.max_images_per_prompt)
        except ValueError as exc:
            problems.append(str(exc))

        if problems:
            raise RequestRejected("; ".join(problems))

        budget = self.estimate(payload)
        if not budget.fits:
            raise ContextBudgetExceeded(
                "request context budget exceeded: "
                f"projected {budget.projected_tokens} > {budget.context_window} tokens",
                code=CONTEXT_BUDGET_EXCEEDED,
                details=budget.as_dict(),
            )
        return budget
