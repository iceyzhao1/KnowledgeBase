"""Select an LLM provider from configuration."""

from __future__ import annotations

from ..config import LLMConfig
from .base import LLMProvider


def build_provider(config: LLMConfig) -> LLMProvider:
    if config.provider == "anthropic":
        from .anthropic import AnthropicProvider

        return AnthropicProvider(config)
    if config.provider == "llm_service":
        from .llm_service import LLMServiceProvider

        return LLMServiceProvider(config)
    if config.provider == "claude_cli":
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(config)
    if config.provider == "mock":
        from .mock import MockProvider

        return MockProvider(config)
    raise ValueError(f"unknown provider: {config.provider}")


def build_answer_provider(config: LLMConfig, channel: str, model: str) -> LLMProvider:
    """按「通道名 + 模型名」给 L2 作答现造一个 provider（每题作答各取一个）。

    与 build_provider 不同：build_provider 按 EVAL_LLM_PROVIDER 绑死「这个服务用谁」，
    只造一次；这里是 L2 答案对照层让花名册里不同模型各写一版答案——同一个 eval-llm
    进程要按请求切换厂商/档位，所以按 channel 现场派发：

    - ``claude_cli``：复用已登录的 claude CLI，把 --model 换成花名册点名的档位
      （haiku/sonnet/opus），白嫖订阅、不花 API 钱。
    - 其它通道：去 config.answer_channels 查该通道的 {type, base_url, api_key, model}，
      目前 type=openai_compat（deepseek/minimax/通义/vLLM 都是这套）现造一个通用
      OpenAI 兼容 provider。花名册的 model 优先，没写就用通道里配的 model。
    """

    from dataclasses import replace

    if channel == "claude_cli":
        from .claude_cli import ClaudeCLIProvider

        return ClaudeCLIProvider(replace(config, claude_cli_model=model or config.claude_cli_model))

    chan_cfg = config.answer_channels.get(channel)
    if not chan_cfg:
        raise ValueError(
            f"作答通道 {channel!r} 没配凭据；请在 EVAL_ANSWER_CHANNELS 里加一条 "
            f"{{base_url, api_key, model}}，或把花名册改回内置 claude_cli 通道"
        )
    chan_type = (chan_cfg.get("type") or "openai_compat").strip().lower()
    if chan_type == "openai_compat":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(
            base_url=chan_cfg.get("base_url", ""),
            api_key=chan_cfg.get("api_key", ""),
            model=model or chan_cfg.get("model", ""),
            timeout=config.request_timeout,
            label=channel,
        )
    raise ValueError(f"作答通道 {channel!r} 的 type={chan_type!r} 暂不支持")
