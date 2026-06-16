"""Configuration for the eval-llm service.

Everything that varies between environments is read from a ``.env`` file (or real
environment variables). The most important switch is ``EVAL_LLM_PROVIDER`` which
selects how the framework reaches "外接 claude" — either the Anthropic API
directly, the in-repo ``llm_service``, or a deterministic ``mock`` used for tests.

Only this service holds model credentials.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:  # optional dependency; service still works without it
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is in requirements but stay defensive
    load_dotenv = None  # type: ignore[assignment]


Provider = Literal["anthropic", "llm_service", "claude_cli", "mock"]

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent  # runtime_eval/ (repo subproject root)
REPO_ROOT = PROJECT_DIR.parent


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


@dataclass
class LLMConfig:
    """Resolved eval-llm runtime configuration."""

    provider: Provider = "mock"

    # --- Anthropic direct ---
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_max_tokens: int = 4096

    # --- llm_service ---
    llm_service_base_url: str = "http://localhost:8900"
    llm_service_caller_domain: str = "eval"

    # --- claude CLI (claude -p) ---
    claude_cli_bin: str = "claude"
    claude_cli_model: str = ""  # 空=用 claude 自身默认模型
    claude_cli_timeout: int = 600  # 出题/裁判单次调用上限(秒)
    claude_cli_extra_args: str = ""  # 追加给 claude 的原始参数，空格分隔
    # 给 claude 子进程注入的代理（如 http://127.0.0.1:7892）。国内直连
    # api.anthropic.com 会被地域封锁回 403，配上代理即可绕过。空=不注入，
    # 沿用进程已有的 HTTP(S)_PROXY（若有）。
    claude_cli_proxy: str = ""

    # --- agent 跑批（claude -p + 现成 MCP，自动检索知识库） ---
    # 复用已登录的 claude CLI，挂载用户自带的 MCP，让 claude 自己调用工具检索知识库，
    # 省去人工逐题「检索数据库」。这里只配置「怎么跑」，不碰任何模型凭据。
    agent_mcp_config: str = ""  # --mcp-config 指向的 MCP 配置文件路径（必填才能跑批）
    agent_allowed_tools: str = ""  # --allowedTools 逗号分隔；空=不加该参数
    agent_permission_mode: str = ""  # --permission-mode（如 acceptEdits/bypassPermissions）
    agent_model: str = ""  # 空=用 claude 自身默认模型
    agent_max_turns: int = 0  # >0 才加 --max-turns，限制 agent 自循环步数
    agent_timeout: int = 600  # 单题 agent 调用上限(秒)，含多轮工具调用
    agent_extra_args: str = ""  # 追加给 claude 的原始参数，空格分隔
    agent_system_prompt: str = ""  # 折进 prompt 的系统提示（指导 agent 如何检索/回答）

    # --- L2 答案对照层：多厂商「作答通道」凭据 ---
    # 一张「通道名 -> 凭据」的表，供 L2 让花名册里不同模型各自作答时按需取用。
    # claude_cli 通道复用上面的 claude CLI（白嫖订阅）；其余如 deepseek/minimax
    # 都走 OpenAI 兼容接口，每条配 {type, base_url, api_key, model}。
    # 从 EVAL_ANSWER_CHANNELS 的 JSON 文本加载，形如：
    #   {"deepseek": {"type":"openai_compat","base_url":"https://api.deepseek.com/v1",
    #                  "api_key":"sk-...","model":"deepseek-chat"}}
    answer_channels: dict[str, dict[str, str]] = field(default_factory=dict)

    # --- generation / judging behaviour ---
    temperature: float = 0.2
    request_timeout: int = 120


def _as_answer_channels(value: str | None) -> dict[str, dict[str, str]]:
    """解析 EVAL_ANSWER_CHANNELS 的 JSON 文本为「通道名 -> 凭据 dict」。

    空/缺省 → 空表（只有内置 claude_cli 通道可用）。格式不对就抛错，别静默
    吞掉——否则配错凭据会表现为「某厂商悄悄没参赛」，很难排查。
    """

    text = (value or "").strip()
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"EVAL_ANSWER_CHANNELS 不是合法 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("EVAL_ANSWER_CHANNELS 必须是 JSON 对象（通道名->凭据）")
    out: dict[str, dict[str, str]] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"EVAL_ANSWER_CHANNELS 通道 {name!r} 的值必须是对象")
        out[str(name)] = {str(k): str(v) for k, v in cfg.items()}
    return out


def load_config(env_file: str | os.PathLike[str] | None = None) -> LLMConfig:
    """Load configuration from ``.env`` + environment variables."""

    if load_dotenv is not None:
        candidates = (
            [Path(env_file)]
            if env_file is not None
            else [PROJECT_DIR / ".env", REPO_ROOT / ".env"]
        )
        # override=True so the .env file is the source of truth for credentials.
        # Otherwise a stale/empty ANTHROPIC_API_KEY left exported in the shell (or
        # injected by a PyCharm run-config / conda activate hook) silently shadows
        # the key in .env and the API returns 403 with the wrong key.
        for candidate in candidates:
            if candidate.exists():
                load_dotenv(candidate, override=True)

    # Accept EVAL_LLM_PROVIDER (new) with EVAL_LLM_BACKEND (v1) fallback.
    provider = (
        os.getenv("EVAL_LLM_PROVIDER") or os.getenv("EVAL_LLM_BACKEND") or "mock"
    ).strip().lower()
    if provider not in ("anthropic", "llm_service", "claude_cli", "mock"):
        raise ValueError(
            "EVAL_LLM_PROVIDER must be one of "
            f"anthropic|llm_service|claude_cli|mock, got {provider!r}"
        )

    return LLMConfig(
        provider=provider,  # type: ignore[arg-type]
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL"),
        anthropic_model=os.getenv("EVAL_ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        anthropic_max_tokens=_as_int(os.getenv("EVAL_ANTHROPIC_MAX_TOKENS"), 4096),
        llm_service_base_url=os.getenv("EVAL_LLM_SERVICE_URL", "http://localhost:8900"),
        llm_service_caller_domain=os.getenv("EVAL_LLM_SERVICE_CALLER", "eval"),
        claude_cli_bin=os.getenv("EVAL_CLAUDE_BIN", "claude"),
        claude_cli_model=os.getenv("EVAL_CLAUDE_MODEL", ""),
        claude_cli_timeout=_as_int(os.getenv("EVAL_CLAUDE_TIMEOUT"), 600),
        claude_cli_extra_args=os.getenv("EVAL_CLAUDE_EXTRA_ARGS", ""),
        # 显式配置优先；未配则沿用进程已有的 HTTPS/HTTP/ALL_PROXY（启动窗口若带代理就自动用）。
        claude_cli_proxy=(
            os.getenv("EVAL_CLAUDE_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("ALL_PROXY")
            or ""
        ),
        agent_mcp_config=os.getenv("EVAL_AGENT_MCP_CONFIG", ""),
        agent_allowed_tools=os.getenv("EVAL_AGENT_ALLOWED_TOOLS", ""),
        agent_permission_mode=os.getenv("EVAL_AGENT_PERMISSION_MODE", ""),
        agent_model=os.getenv("EVAL_AGENT_MODEL", ""),
        agent_max_turns=_as_int(os.getenv("EVAL_AGENT_MAX_TURNS"), 0),
        agent_timeout=_as_int(os.getenv("EVAL_AGENT_TIMEOUT"), 600),
        agent_extra_args=os.getenv("EVAL_AGENT_EXTRA_ARGS", ""),
        agent_system_prompt=os.getenv("EVAL_AGENT_SYSTEM", ""),
        answer_channels=_as_answer_channels(os.getenv("EVAL_ANSWER_CHANNELS")),
        temperature=_as_float(os.getenv("EVAL_TEMPERATURE"), 0.2),
        request_timeout=_as_int(os.getenv("EVAL_REQUEST_TIMEOUT"), 120),
    )
