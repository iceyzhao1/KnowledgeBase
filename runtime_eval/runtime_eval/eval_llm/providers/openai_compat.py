"""OpenAI 兼容接口的通用 provider（Chat Completions）。

DeepSeek、MiniMax、通义、本地 vLLM 等大多暴露 OpenAI 兼容的
``POST {base_url}/chat/completions`` 接口。它们的差别只在 base_url / api_key / 模型名，
所以这里写一个通用 provider：构造时显式吃 base_url + api_key + model，
不从全局 LLMConfig 取（凭据按「通道」分别配置，见 config.answer_channels）。

新增一个 OpenAI 兼容厂商 = 在 EVAL_ANSWER_CHANNELS 配一行 {base_url, api_key, model}，
花名册再加一行指向该通道，不用改代码。
"""

from __future__ import annotations

from ...shared.models import TokenUsage
from .base import ChatResult


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        label: str = "openai_compat",
    ):
        if not base_url:
            raise RuntimeError(f"通道 {label} 缺 base_url，无法调用 OpenAI 兼容接口")
        if not model:
            raise RuntimeError(f"通道 {label} 缺 model，无法确定要调哪个模型")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self.timeout = timeout
        self.label = label

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

        url = f"{self.base_url}/chat/completions"
        messages = []
        if system and system.strip():
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        resp = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(
                f"{self.label} OpenAI兼容接口 {resp.status_code} @ {url} "
                f"[model {self.model}]: {resp.text[:600]}"
            )

        data = resp.json()
        choices = data.get("choices") or []
        text = ""
        if choices:
            text = str((choices[0].get("message") or {}).get("content") or "")
        usage = _usage(data.get("usage"))
        return ChatResult(text=text, usage=usage, raw=data)


def _usage(raw: object) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    prompt = int(raw.get("prompt_tokens", 0) or 0)
    completion = int(raw.get("completion_tokens", 0) or 0)
    total = int(raw.get("total_tokens", 0) or 0) or (prompt + completion)
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )
