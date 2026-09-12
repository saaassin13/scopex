from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit


REQUEST_LIMIT = 4 * 1024 * 1024
RESPONSE_LIMIT = 8 * 1024 * 1024


class StopBeforeForward(RuntimeError):
    """Raised by the control plane to stop at a safe model-request boundary."""


class RequestRejected(RuntimeError):
    """Raised by request policy when a model request violates runtime invariants."""


def strict_json(raw: bytes) -> dict[str, Any]:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    def invalid_constant(_):
        raise ValueError("non-JSON numeric constant")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def loopback_v1(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if (
        not local
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise ValueError("model endpoint must be a credential-free loopback /v1 URL")
    return parsed.scheme, host, parsed.port or (443 if parsed.scheme == "https" else 80)


def _save_json(path: Path, value: Any) -> None:
    """Atomically publish one complete JSON audit snapshot.

    Audit readers must never observe a target file after truncation but before
    its JSON payload is complete. Write and fsync a sibling temporary file,
    then atomically replace the target name.
    """

    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


RequestHook = Callable[[int, dict[str, Any]], None]


class ModelProxy(ThreadingHTTPServer):
    """Loopback OpenAI proxy with exact request forwarding and local wire audit."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        *,
        audit_dir: Path,
        upstream_base_url: str,
        upstream_api_key: str,
        local_token: str,
        max_requests: int = 12,
        deadline_s: float = 300.0,
        on_request: RequestHook | None = None,
    ) -> None:
        if not local_token or "\n" in local_token or "\r" in local_token:
            raise ValueError("local proxy token must be a non-empty single line")
        if max_requests <= 0 or deadline_s <= 0:
            raise ValueError("proxy request/deadline budgets must be positive")
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.upstream = loopback_v1(upstream_base_url)
        self.upstream_api_key = upstream_api_key
        self.local_token = local_token
        self.max_requests = max_requests
        self.deadline_s = float(deadline_s)
        self.on_request = on_request
        self.started = time.monotonic()
        self.records: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.connections: set[http.client.HTTPConnection] = set()
        self.stopping = False
        super().__init__(("127.0.0.1", 0), _ProxyHandler)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}/v1"

    @property
    def deadline(self) -> float:
        return self.started + self.deadline_s

    def cancel(self) -> None:
        with self.lock:
            self.stopping = True
            connections = list(self.connections)
        for connection in connections:
            try:
                if connection.sock:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass

    def upstream_connection(self, timeout_s: float) -> http.client.HTTPConnection:
        scheme, host, port = self.upstream
        cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        return cls(host, port, timeout=timeout_s)


class _ProxyHandler(BaseHTTPRequestHandler):
    server: ModelProxy

    def log_message(self, *_):
        pass

    def _json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        proxy = self.server
        self.connection.settimeout(10)
        if self.path != "/v1/chat/completions":
            self._json_error(404, "unsupported local proxy path")
            return
        if self.headers.get("Authorization") != "Bearer " + proxy.local_token:
            self._json_error(403, "local proxy authorization failed")
            return

        record: dict[str, Any] | None = None
        record_persisted = False
        upstream: http.client.HTTPConnection | None = None
        response_started = False

        def persist_record() -> None:
            nonlocal record_persisted
            if record is None or record_persisted:
                return
            record["end_s"] = round(time.monotonic() - proxy.started, 4)
            _save_json(proxy.audit_dir / f"wire-{record['index']:02d}-meta.json", record)
            record_persisted = True

        try:
            if self.headers.get("Transfer-Encoding"):
                raise RequestRejected("transfer encoding is not accepted")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= REQUEST_LIMIT:
                raise RequestRejected("invalid request length")
            body = self.rfile.read(length)
            if len(body) != length:
                raise RequestRejected("short request body")
            payload = strict_json(body)

            with proxy.lock:
                if proxy.stopping:
                    raise StopBeforeForward("proxy is stopping")
                if time.monotonic() >= proxy.deadline:
                    raise RequestRejected("task deadline reached")
                if len(proxy.records) >= proxy.max_requests:
                    raise RequestRejected("model request budget reached")
                index = len(proxy.records) + 1
                record = {
                    "index": index,
                    "forwarded": False,
                    "start_s": round(time.monotonic() - proxy.started, 4),
                    "message_count": len(payload.get("messages", []))
                    if isinstance(payload.get("messages"), list) else None,
                }
                proxy.records.append(record)

            (proxy.audit_dir / f"wire-{index:02d}-request.json").write_bytes(body)

            if proxy.on_request is not None:
                proxy.on_request(index, payload)

            remaining = proxy.deadline - time.monotonic()
            if remaining <= 0:
                raise RequestRejected("task deadline reached")
            upstream = proxy.upstream_connection(remaining)
            with proxy.lock:
                if proxy.stopping:
                    raise StopBeforeForward("proxy is stopping")
                proxy.connections.add(upstream)

            headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
            if proxy.upstream_api_key:
                headers["Authorization"] = "Bearer " + proxy.upstream_api_key
            record["forwarded"] = True
            upstream.request("POST", "/v1/chat/completions", body=body, headers=headers)
            response = upstream.getresponse()
            record["http_status"] = response.status
            content_type = response.getheader("Content-Type", "application/octet-stream")
            record["content_type"] = content_type

            self.send_response(response.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            self.close_connection = True

            count = 0
            with (proxy.audit_dir / f"wire-{index:02d}-response.bin").open("xb") as trace:
                while True:
                    if proxy.stopping or time.monotonic() >= proxy.deadline:
                        raise TimeoutError("task stopped or deadline reached")
                    chunk = response.read1(16384)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > RESPONSE_LIMIT:
                        raise RequestRejected("upstream response audit limit exceeded")
                    trace.write(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            record["response_bytes"] = count
            record["response_complete"] = True

        except StopBeforeForward as exc:
            if record is not None:
                record["blocked"] = "safe_stop"
                record["error_type"] = type(exc).__name__
                record["error_message"] = str(exc)[:300]
            if not response_started:
                # Commit the authoritative decision before a Content-Length
                # error response lets the client return to its caller.
                persist_record()
                self._json_error(409, "request stopped before model forwarding")
        except RequestRejected as exc:
            if record is not None:
                record["blocked"] = "policy"
                record["error_type"] = type(exc).__name__
                record["error_message"] = str(exc)[:300]
            if not response_started:
                persist_record()
                self._json_error(422, str(exc)[:300])
        except Exception as exc:
            if record is not None:
                record["blocked"] = "proxy_error"
                record["error_type"] = type(exc).__name__
                record["error_message"] = str(exc)[:300]
            if not response_started:
                try:
                    persist_record()
                    self._json_error(502, "local model proxy failed; inspect audit")
                except OSError:
                    pass
        finally:
            persist_record()
            if upstream is not None:
                with proxy.lock:
                    proxy.connections.discard(upstream)
                upstream.close()
