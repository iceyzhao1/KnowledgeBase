"""Judge provider backed by the local Claude Code CLI (``claude -p``).

复用已登录的 ``claude`` 命令作为 Judge LLM，无需在本仓库铺 API key。行为借鉴
runtime_eval 的成熟实现（但不 import 它）：解析可执行（Windows 优先 ``.cmd``）、
``claude -p --output-format json``、prompt 走 stdin、临时工作目录、清理
ANTHROPIC_*/CLAUDECODE/CLAUDE_CODE_* 子环境、解析 JSON 与 token usage。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .base import ProviderResult, ProviderUsage

_AUTH_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
)
_HOST_MANAGED_ENV_VARS = ("CLAUDECODE",)
_HOST_MANAGED_PREFIX = "CLAUDE_CODE_"


@dataclass
class ClaudeCLIConfig:
    bin: str = "claude"
    model: str = ""
    timeout: int = 600
    extra_args: str = ""
    proxy: str = ""


def _exists(path: str) -> bool:
    return Path(path).exists()


def resolve_claude_bin(bin_name: str | None = None) -> str:
    name = bin_name or "claude"
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(
            f"找不到可执行文件 {name!r}，请确认 claude CLI 已安装且在 PATH 上"
            "（或用 TELECOM_EVAL_CLAUDE_BIN 指定全路径）。"
        )
    if sys.platform == "win32" and resolved.lower().endswith(".ps1"):
        cmd_sibling = resolved[:-4] + ".cmd"
        if shutil.which(cmd_sibling) or _exists(cmd_sibling):
            resolved = cmd_sibling
    return resolved


def clean_child_env(proxy: str | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in _AUTH_ENV_VARS
        and k not in _HOST_MANAGED_ENV_VARS
        and not k.upper().startswith("ANTHROPIC_")
        and not k.upper().startswith(_HOST_MANAGED_PREFIX)
    }
    if proxy and proxy.strip():
        p = proxy.strip()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[key] = p
        env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        env.setdefault("no_proxy", env["NO_PROXY"])
    return env


def wrap_for_windows(resolved: str, argv: list[str]) -> list[str]:
    if sys.platform == "win32" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *argv]
    return argv


def _parse_result(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("claude CLI 无输出（stdout 为空）。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"claude CLI 输出非 JSON：{text[:300]}")


def _usage(raw: object) -> ProviderUsage:
    if not isinstance(raw, dict):
        return ProviderUsage()
    prompt = (
        int(raw.get("input_tokens", 0) or 0)
        + int(raw.get("cache_creation_input_tokens", 0) or 0)
        + int(raw.get("cache_read_input_tokens", 0) or 0)
    )
    completion = int(raw.get("output_tokens", 0) or 0)
    return ProviderUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(self, config: ClaudeCLIConfig | None = None):
        self.config = config or ClaudeCLIConfig()
        try:
            self.resolved = resolve_claude_bin(self.config.bin)
        except RuntimeError as exc:
            raise RuntimeError(f"TELECOM_EVAL_JUDGE_PROVIDER=claude_cli {exc}") from exc

    def _argv(self) -> list[str]:
        argv = [self.resolved, "-p", "--output-format", "json"]
        if self.config.model:
            argv += ["--model", self.config.model]
        if self.config.extra_args:
            argv += shlex.split(self.config.extra_args)
        return wrap_for_windows(self.resolved, argv)

    def invoke(self, *, system: str, user: str) -> ProviderResult:
        prompt = f"{system.strip()}\n\n{user}" if system and system.strip() else user
        child_env = clean_child_env(proxy=self.config.proxy)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="telecom-eval-claude-") as workdir:
            try:
                proc = subprocess.run(
                    self._argv(),
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=workdir,
                    env=child_env,
                    timeout=self.config.timeout,
                )
            except FileNotFoundError as exc:  # pragma: no cover - PATH race
                raise RuntimeError(f"无法启动 claude CLI：{exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"claude CLI 调用超时（>{self.config.timeout}s），可调大 TELECOM_EVAL_CLAUDE_TIMEOUT。"
                ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 退出码 {proc.returncode}："
                f"{(proc.stderr or proc.stdout).strip()[:500]}"
            )

        data = _parse_result(proc.stdout)
        if data.get("is_error"):
            raise RuntimeError(
                f"claude CLI 返回错误：{data.get('result') or data.get('subtype') or 'unknown'}"
            )
        text = str(data.get("result") or "")
        return ProviderResult(text=text, usage=_usage(data.get("usage")), raw=data, latency_ms=latency_ms)
