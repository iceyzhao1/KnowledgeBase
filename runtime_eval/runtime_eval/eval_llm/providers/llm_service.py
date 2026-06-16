"""Provider that routes through the in-repo ``llm_service`` (localhost:8900).

Uses the synchronous ``POST /api/v1/execute`` endpoint with caller-provided
messages, mirroring the "Caller-provided messages (no template)" pattern from
``llm_service/client.py``.
"""

from __future__ import annotations

from ...shared.models import TokenUsage
from ..config import LLMConfig
from .base import ChatResult


class LLMServiceProvider:
    name = "llm_service"

    def __init__(self, config: LLMConfig):
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
        import httpx  # noqa: PLC0415

        payload = {
            "caller_domain": self.config.llm_service_caller_domain,
            "pipeline_stage": "eval",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "expected_output_type": "json_object" if json_mode else "text",
            "params": {"temperature": temperature, "max_tokens": max_tokens},
        }
        base = self.config.llm_service_base_url.rstrip("/")
        resp = httpx.post(
            f"{base}/api/v1/execute",
            json=payload,
            timeout=self.config.request_timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "succeeded":
            err = data.get("error") or {}
            raise RuntimeError(
                f"llm_service execute failed: status={data.get('status')} "
                f"{err.get('error_type')}: {err.get('error_message')}"
            )

        result = data.get("result") or {}
        text = result.get("text_output")
        if not text and result.get("parsed_output") is not None:
            import json  # noqa: PLC0415

            text = json.dumps(result["parsed_output"], ensure_ascii=False)
        text = text or ""

        total = data.get("total_tokens") or 0
        usage = TokenUsage(total_tokens=total)
        return ChatResult(text=text, usage=usage, raw=data)
