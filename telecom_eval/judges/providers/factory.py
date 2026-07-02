"""Provider factory.

优先级：claude_cli > mock > llm_service > openai_compat。
第二版只实现 claude_cli 和 mock；后两者作为扩展点占位，调用即报错。
"""

from __future__ import annotations

import os

from .base import JudgeProvider
from .claude_cli import ClaudeCLIConfig, ClaudeCLIProvider
from .eval_roster import EvalRosterConfig, EvalRosterProvider
from .mock import MockJudgeProvider

PROVIDER_PRIORITY = ("eval_roster", "claude_cli", "mock", "llm_service", "openai_compat")


def build_provider(name: str, config: ClaudeCLIConfig | EvalRosterConfig | None = None) -> JudgeProvider:
    if name == "eval_roster":
        if config is not None and not isinstance(config, EvalRosterConfig):
            raise TypeError("eval_roster provider requires EvalRosterConfig")
        roster_config = config or EvalRosterConfig(
            roster_json=os.environ.get("EVAL_ROSTER", ""),
            channels_json=os.environ.get("EVAL_ANSWER_CHANNELS", ""),
            preferred_id=os.environ.get("EVAL_JUDGE_MODEL_ID", ""),
        )
        return EvalRosterProvider(roster_config)
    if name == "claude_cli":
        if config is not None and not isinstance(config, ClaudeCLIConfig):
            raise TypeError("claude_cli provider requires ClaudeCLIConfig")
        return ClaudeCLIProvider(config)
    if name == "mock":
        return MockJudgeProvider()
    if name in ("llm_service", "openai_compat"):
        raise NotImplementedError(
            f"provider {name!r} 是后续扩展点，第二版未实现；请使用 claude_cli 或 mock。"
        )
    raise ValueError(f"未知 judge provider: {name!r}")
