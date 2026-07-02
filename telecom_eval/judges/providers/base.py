from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProviderUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ProviderResult:
    text: str
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    raw: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0


@runtime_checkable
class JudgeProvider(Protocol):
    name: str

    def invoke(self, *, system: str, user: str) -> ProviderResult:
        ...
