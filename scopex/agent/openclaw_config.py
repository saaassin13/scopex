from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import ipaddress
from urllib.parse import urlsplit


APPROVED_TOOLS = frozenset({"read", "exec", "process"})


@dataclass(frozen=True, slots=True)
class ModelRequestSettings:
    max_tokens: int = 2048
    temperature: float = 0.1
    enable_thinking: bool = False


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    memory: str = "512m"
    memory_swap: str = "512m"
    cpus: float = 1.0
    pids_limit: int = 256
    exec_timeout_s: int = 30


@dataclass(frozen=True, slots=True)
class OpenClawConfigSpec:
    model_id: str
    proxy_base_url: str
    proxy_api_key: str
    workspace: Path
    audit_log: Path
    sandbox_root: Path
    image: str
    agent_id: str
    uid: int
    gid: int
    timeout_s: int = 300
    context_window: int = 32768
    request: ModelRequestSettings = field(default_factory=ModelRequestSettings)
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ("read", "exec", "process")
    container_prefix: str = "scopex-"


def _require_loopback_v1(url: str) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (
        not loopback
        or parsed.scheme not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise ValueError("OpenClaw provider must use a credential-free loopback /v1 proxy URL")


def build_openclaw_config(spec: OpenClawConfigSpec) -> dict:
    """Build the product OpenClaw config from POC02-validated security defaults."""

    _require_loopback_v1(spec.proxy_base_url)
    if not spec.model_id or not spec.agent_id or not spec.image:
        raise ValueError("model_id, agent_id and image are required")
    if not spec.proxy_api_key or "\n" in spec.proxy_api_key or "\r" in spec.proxy_api_key:
        raise ValueError("a non-empty single-line local proxy token is required")
    if spec.uid < 0 or spec.gid < 0:
        raise ValueError("uid/gid must be non-negative")
    if spec.timeout_s <= 0 or spec.request.max_tokens <= 0:
        raise ValueError("timeouts/token limits must be positive")
    if spec.request.enable_thinking is not False:
        raise ValueError("ScopeX validated OpenClaw runtime requires thinking=false")
    if not spec.tools:
        raise ValueError("at least one tool must be configured")
    unknown_tools = set(spec.tools) - APPROVED_TOOLS
    if unknown_tools:
        raise ValueError("unapproved OpenClaw tools: " + ", ".join(sorted(unknown_tools)))

    model_ref = "vllm/" + spec.model_id
    extra_body = {
        "temperature": spec.request.temperature,
        "max_tokens": spec.request.max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    skills = list(spec.skills)
    tools = list(spec.tools)
    prefix = spec.container_prefix + spec.agent_id + "-"

    return {
        "logging": {"file": str(spec.audit_log), "level": "info"},
        "update": {"checkOnStart": False},
        "models": {
            "mode": "replace",
            "providers": {
                "vllm": {
                    "baseUrl": spec.proxy_base_url,
                    "apiKey": spec.proxy_api_key,
                    "api": "openai-completions",
                    "models": [
                        {
                            "id": spec.model_id,
                            "name": "ScopeX local model",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "contextWindow": spec.context_window,
                            "maxTokens": spec.request.max_tokens,
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "compat": {
                                "thinkingFormat": "qwen-chat-template",
                                "maxTokensField": "max_tokens",
                            },
                        }
                    ],
                }
            },
        },
        "agents": {
            "defaults": {
                "model": {"primary": model_ref, "fallbacks": []},
                "models": {
                    model_ref: {
                        "params": {"extra_body": extra_body},
                        "codeMode": False,
                    }
                },
                "workspace": str(spec.workspace),
                "skipBootstrap": True,
                "skills": skills,
                "startupContext": {"enabled": False},
                "contextInjection": "never",
                "embeddedAgent": {"projectSettingsPolicy": "ignore"},
                "thinkingDefault": "off",
                "timeoutSeconds": spec.timeout_s,
                "compaction": {"enabled": False, "memoryFlush": {"enabled": False}},
                "sandbox": {
                    "mode": "all",
                    "scope": "session",
                    "workspaceAccess": "ro",
                    "workspaceRoot": str(spec.sandbox_root),
                    "docker": {
                        "image": spec.image,
                        "containerPrefix": prefix,
                        "workdir": "/workspace",
                        "readOnlyRoot": True,
                        "tmpfs": ["/tmp", "/var/tmp", "/run"],
                        "network": "none",
                        "user": f"{spec.uid}:{spec.gid}",
                        "capDrop": ["ALL"],
                        "pidsLimit": spec.limits.pids_limit,
                        "memory": spec.limits.memory,
                        "memorySwap": spec.limits.memory_swap,
                        "cpus": spec.limits.cpus,
                    },
                    "browser": {"enabled": False},
                },
            },
            "entries": {
                spec.agent_id: {
                    "default": True,
                    "skills": skills,
                    "memory": {"search": {"enabled": False}},
                }
            },
        },
        "tools": {
            "allow": tools,
            "elevated": {"enabled": False},
            "exec": {
                "host": "sandbox",
                "mode": "full",
                "timeoutSeconds": spec.limits.exec_timeout_s,
            },
            "sandbox": {"tools": {"allow": tools}},
            "toolSearch": False,
            "codeMode": {"enabled": False},
        },
        "plugins": {
            "allow": ["vllm"],
            "entries": {"vllm": {"enabled": True}},
            "slots": {"memory": "none"},
        },
    }
