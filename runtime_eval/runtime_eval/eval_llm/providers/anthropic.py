"""Direct Anthropic API provider.

Talks to the Messages API with a single ``httpx`` POST — no hard dependency on
the official SDK, and no hidden fallback chain that could mask the real error.
On any non-200 the raw response body and the (masked) key fingerprint are
surfaced so a 403 tells you *which* key was actually sent and *why* it was
rejected, instead of an opaque ``403 Forbidden``.
"""

from __future__ import annotations

from ...shared.models import TokenUsage
from ..config import LLMConfig
from .base import ChatResult


def _fingerprint(key: str) -> str:
    """Mask a key for safe logging: head + tail + length, never the middle."""
    if not key:
        return "<empty>"
    if len(key) <= 12:
        return f"len={len(key)} ...{key[-2:]}"
    return f"{key[:10]}…{key[-4:]} len={len(key)}"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, config: LLMConfig):
        if not config.anthropic_api_key:
            raise RuntimeError(
                "EVAL_LLM_PROVIDER=anthropic 需要在 .env 设置 ANTHROPIC_API_KEY"
            )
        self.config = config

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> ChatResult:
        # json_mode is advisory for Anthropic; we steer via the system prompt
        # upstream. The Messages API has no dedicated JSON switch.
        import httpx  # noqa: PLC0415

        base = (self.config.anthropic_base_url or "https://api.anthropic.com").rstrip("/")
        key = self.config.anthropic_api_key or ""
        url = f"{base}/v1/messages"

        resp = httpx.post(
            url,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.config.anthropic_model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.config.request_timeout,
        )

        if resp.status_code != 200:
            # Surface everything needed to diagnose without leaking the full key.
            raise RuntimeError(
                f"Anthropic API {resp.status_code} @ {url} "
                f"[key {_fingerprint(key)}, model {self.config.anthropic_model}]: "
                f"{resp.text[:600]}"
            )

        data = resp.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        u = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=u.get("input_tokens", 0) or 0,
            completion_tokens=u.get("output_tokens", 0) or 0,
            total_tokens=(u.get("input_tokens", 0) or 0) + (u.get("output_tokens", 0) or 0),
        )
        return ChatResult(text=text, usage=usage, raw=data)
