"""参赛模型花名册：每个条目描述一个能基于证据包整合最终答案的模型。

L2 答案对照层把同一份证据包发给花名册里的多个模型各写一版答案，再跟黄金答案比。
花名册是「配置驱动」的：默认内置 Claude 三档（走 claude_cli，吃订阅、不花 API 钱），
也可以用环境变量 EVAL_ROSTER 覆盖成任意厂商组合。

- ``channel`` 指向 eval-llm 那边知道怎么调的「通道」：
  - ``claude_cli``    —— 本机已登录的 claude CLI（claude -p），model 即 --model 档位。
  - 其它（如 ``deepseek`` / ``minimax``） —— 在 eval-llm 的 EVAL_ANSWER_CHANNELS 里
    配 base_url/api_key（OpenAI 兼容接口），这里只要写 channel 名 + model 即可。
- 新增一个厂商 = eval-llm 配一行通道凭据 + 这里花名册加一行。serving 只检索不作答，不进花名册。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class RosterEntry:
    id: str                       # 稳定标识，如 "claude-sonnet"
    label: str                    # 显示名，如 "Claude Sonnet"
    channel: str = "claude_cli"   # 调用通道名（eval-llm 据此选 provider）
    model: str = ""               # 该通道下的具体档位/模型名
    enabled: bool = True          # 是否默认参赛（前端可再勾选）


DEFAULT_ROSTER: list[RosterEntry] = [
    RosterEntry(id="claude-haiku", label="Claude Haiku", channel="claude_cli", model="haiku"),
    RosterEntry(id="claude-sonnet", label="Claude Sonnet", channel="claude_cli", model="sonnet"),
    RosterEntry(id="claude-opus", label="Claude Opus", channel="claude_cli", model="opus"),
]


def load_roster(env_value: str | None) -> list[RosterEntry]:
    """从环境变量 EVAL_ROSTER 的 JSON 文本加载花名册；空/缺省 → 默认 Claude 三档。

    JSON 形如 ``[{"id","label","channel","model","enabled"}, ...]``，缺字段按
    RosterEntry 默认值补。解析失败抛 ValueError（别静默吞，免得花名册悄悄变空）。
    """

    text = (env_value or "").strip()
    if not text:
        return [RosterEntry(**vars(e)) for e in DEFAULT_ROSTER]
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"EVAL_ROSTER 不是合法 JSON：{exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("EVAL_ROSTER 必须是 JSON 数组")
    out: list[RosterEntry] = []
    for row in rows:
        if not isinstance(row, dict) or "id" not in row:
            raise ValueError(f"EVAL_ROSTER 条目缺 id 或格式不对：{row!r}")
        out.append(
            RosterEntry(
                id=str(row["id"]),
                label=str(row.get("label") or row["id"]),
                channel=str(row.get("channel") or "claude_cli"),
                model=str(row.get("model") or ""),
                enabled=bool(row.get("enabled", True)),
            )
        )
    return out


def enabled_roster(entries: list[RosterEntry]) -> list[RosterEntry]:
    """只留默认参赛（enabled=True）的条目。"""

    return [e for e in entries if e.enabled]


def select_roster(entries: list[RosterEntry], ids: list[str] | None) -> list[RosterEntry]:
    """按前端勾选的 id 列表过滤花名册；ids 为空 → 回退到默认参赛的那批。

    保留花名册原有顺序（不按 ids 顺序），便于排行榜稳定。未知 id 静默忽略。
    """

    if not ids:
        return enabled_roster(entries)
    want = set(ids)
    return [e for e in entries if e.id in want]
