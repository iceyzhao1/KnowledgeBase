"""Provider backed by the local Claude Code CLI (``claude -p``).

This lets the framework reuse an already-installed, already-authenticated
``claude`` command as its generation/judging LLM — no API key plumbing in this
repo. The prompt is fed over stdin (so large documents don't hit argv limits)
and ``--output-format json`` gives us the assistant text plus token usage.

The CLI is run in a throwaway working directory so it does *not* pick up this
project's ``CLAUDE.md`` / local context, keeping generation focused on the
document we pass in.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

from ...shared.models import TokenUsage
from ..config import LLMConfig

# Stripped from the child env so the CLI uses its own `claude login` credentials
# instead of being hijacked into API-key mode by a stray/empty key or a base-url
# override leaking from this repo's .env or the shell. The whole point of the
# claude_cli provider is to reuse the already-logged-in claude.
_AUTH_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
)

# Injected by the Claude Code / Claude Desktop *host* when something is launched
# from inside a managed session. They tell a child ``claude`` to fetch its token
# from the host over an IPC channel instead of using its own ``claude login``
# OAuth. A long-running eval-llm started from inside the IDE inherits these, but
# its detached ``claude -p`` subprocess can't reach that channel — the result is
# ``403 Request not allowed``. Strip them so the child falls back to standalone
# OAuth. (Stripping is safe even when the host channel *is* reachable: standalone
# login works in both cases.)
_HOST_MANAGED_ENV_VARS = ("CLAUDECODE",)
_HOST_MANAGED_PREFIX = "CLAUDE_CODE_"
from .base import ChatResult


def resolve_claude_bin(bin_name: str | None = None) -> str:
    """Locate the ``claude`` executable, preferring a Windows ``.cmd`` shim.

    Shared by the provider and the agent runner so both launch the CLI the same
    way. Raises ``RuntimeError`` with a Chinese hint if not found on PATH.
    """

    name = bin_name or "claude"
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(
            f"找不到可执行文件 {name!r}，"
            "请确认 claude CLI 已安装且在 PATH 上（或用 EVAL_CLAUDE_BIN 指定全路径）。"
        )
    # On Windows `claude` is a .cmd/.ps1 shim that CreateProcess can't launch
    # directly; PowerShell's Get-Command may also resolve the .ps1. Prefer the
    # .cmd sibling and route through `cmd /c` so stdin/args pass cleanly.
    if sys.platform == "win32" and resolved.lower().endswith(".ps1"):
        cmd_sibling = resolved[:-4] + ".cmd"
        if shutil.which(cmd_sibling) or _exists(cmd_sibling):
            resolved = cmd_sibling
    return resolved


def clean_child_env(proxy: str | None = None) -> dict[str, str]:
    """Child env with every ANTHROPIC_*/host-managed var stripped.

    Drop named auth vars plus any base-url/token leaking from a global .env,
    conda activate hook, or system env — a stray ``ANTHROPIC_BASE_URL`` pointing
    at a gateway is the classic cause of ``403 Request not allowed``. Stripping it
    forces the CLI back onto its own ``claude login`` OAuth.

    ``proxy`` (e.g. ``http://127.0.0.1:7892``) is injected as HTTP(S)_PROXY when
    given. This matters in regions where ``api.anthropic.com`` is geo-blocked:
    a server launched from a terminal *without* proxy env connects directly and
    Anthropic answers ``403 Request not allowed``. Setting the proxy here makes
    the child reach the API regardless of how the parent was launched.
    """

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
        # Keep localhost calls (if any) off the proxy; don't clobber a richer
        # NO_PROXY the user already set.
        env.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
        env.setdefault("no_proxy", env["NO_PROXY"])

    return env


def wrap_for_windows(resolved: str, argv: list[str]) -> list[str]:
    """Route a ``.cmd``/``.bat`` shim through ``cmd /c`` on Windows."""

    if sys.platform == "win32" and resolved.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *argv]
    return argv


class ClaudeCLIProvider:
    name = "claude_cli"

    def __init__(self, config: LLMConfig):
        self.config = config
        self.bin = config.claude_cli_bin or "claude"
        try:
            self.resolved = resolve_claude_bin(self.bin)
        except RuntimeError as exc:
            raise RuntimeError(f"EVAL_LLM_PROVIDER=claude_cli {exc}") from exc

    def _argv(self) -> list[str]:
        argv = [self.resolved, "-p", "--output-format", "json"]
        if self.config.claude_cli_model:
            argv += ["--model", self.config.claude_cli_model]
        if self.config.claude_cli_extra_args:
            argv += shlex.split(self.config.claude_cli_extra_args)
        return wrap_for_windows(self.resolved, argv)

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> ChatResult:
        # claude -p has no system/user split or temperature flag, so we fold the
        # system prompt into the single piped prompt. json_mode is advisory — the
        # prompt itself already asks for the JSON schema; downstream parsing is
        # tolerant via extract_json.
        prompt = f"{system.strip()}\n\n{user}" if system and system.strip() else user

        child_env = clean_child_env(proxy=self.config.claude_cli_proxy)

        with tempfile.TemporaryDirectory(prefix="eval-claude-") as workdir:
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
                    timeout=self.config.claude_cli_timeout,
                )
            except FileNotFoundError as exc:  # pragma: no cover - PATH race
                raise RuntimeError(f"无法启动 claude CLI：{exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"claude CLI 调用超时（>{self.config.claude_cli_timeout}s），"
                    "可调大 EVAL_CLAUDE_TIMEOUT。"
                ) from exc

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI 退出码 {proc.returncode}：{(proc.stderr or proc.stdout).strip()[:500]}"
            )

        data = _parse_result(proc.stdout)
        if data.get("is_error"):
            raise RuntimeError(
                f"claude CLI 返回错误：{data.get('result') or data.get('subtype') or 'unknown'}"
            )

        text = str(data.get("result") or "")
        return ChatResult(text=text, usage=_usage(data.get("usage")), raw=data)


def _exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).exists()


def _parse_result(stdout: str) -> dict:
    """Parse the CLI's JSON result object, tolerating leading/trailing noise."""

    text = (stdout or "").strip()
    if not text:
        raise RuntimeError("claude CLI 无输出（stdout 为空）。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span (tolerate leading/trailing noise).
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"claude CLI 输出非 JSON：{text[:300]}")


def _usage(raw: object) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    prompt = (
        int(raw.get("input_tokens", 0) or 0)
        + int(raw.get("cache_creation_input_tokens", 0) or 0)
        + int(raw.get("cache_read_input_tokens", 0) or 0)
    )
    completion = int(raw.get("output_tokens", 0) or 0)
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )
