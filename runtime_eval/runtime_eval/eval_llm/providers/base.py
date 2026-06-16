"""Provider protocol shared by all LLM providers used inside eval-llm."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ...shared.models import TokenUsage


@dataclass
class ChatResult:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict | None = None


class LLMProvider(Protocol):
    """Minimal synchronous chat interface.

    Implementations must return the assistant text plus best-effort token usage
    so the framework can report its own LLM cost separately from the agent's.
    """

    name: str

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> ChatResult:
        ...
