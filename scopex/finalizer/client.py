from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import json
import time
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class FinalizerResponse:
    content: str
    headers_s: float
    first_content_s: float | None
    elapsed_s: float
    finish_reasons: tuple[str, ...]
    done_seen: bool
    usage: dict[str, Any] | None


class StreamingFinalizerClient:
    """Small loopback OpenAI-compatible client for fresh no-tool finalization."""

    def __init__(self, base_url: str, *, api_key: str = "", timeout_s: int = 120) -> None:
        self.host, self.port, self.https = self._endpoint(base_url)
        self.api_key = api_key
        self.timeout_s = timeout_s

    @staticmethod
    def _endpoint(base_url: str) -> tuple[str, int, bool]:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        if host == "localhost":
            host = "127.0.0.1"
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if (
            not is_loopback
            or parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"
        ):
            raise ValueError("finalizer endpoint must be a credential-free loopback /v1 URL")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port, parsed.scheme == "https"

    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 512,
        temperature: float = 0,
    ) -> FinalizerResponse:
        if not model:
            raise ValueError("model is required")
        if not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens out of range")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        return self._stream(body)

    def _stream(self, body: dict[str, Any]) -> FinalizerResponse:
        cls = http.client.HTTPSConnection if self.https else http.client.HTTPConnection
        connection = cls(self.host, self.port, timeout=self.timeout_s)
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key

        start = time.monotonic()
        first_content: float | None = None
        chunks: list[str] = []
        finish: list[str] = []
        usage: dict[str, Any] | None = None
        done = False
        headers_s = 0.0
        try:
            connection.request("POST", "/v1/chat/completions", body=encoded, headers=headers)
            response = connection.getresponse()
            headers_s = time.monotonic() - start
            if response.status != 200:
                detail = response.read(65536).decode("utf-8", errors="replace")
                raise ValueError(f"finalizer HTTP {response.status}: {detail[:500]}")

            while True:
                raw = response.readline()
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
                    reason = choice.get("finish_reason")
                    if reason is not None:
                        finish.append(str(reason))
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        if first_content is None:
                            first_content = time.monotonic() - start
                        chunks.append(content)

            elapsed = time.monotonic() - start
            return FinalizerResponse(
                content="".join(chunks),
                headers_s=round(headers_s, 4),
                first_content_s=round(first_content, 4) if first_content is not None else None,
                elapsed_s=round(elapsed, 4),
                finish_reasons=tuple(finish),
                done_seen=done,
                usage=usage,
            )
        finally:
            connection.close()
