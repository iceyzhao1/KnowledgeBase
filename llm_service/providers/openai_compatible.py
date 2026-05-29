from __future__ import annotations

import logging
import os
import time

import httpx

from llm_service.providers.base import ProviderError, ProviderResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        headers: dict | None = None,
        timeout: int = 30,
        bypass_proxy: bool = False,
    ):
        self._url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._extra_headers = headers or {}
        self._timeout = timeout
        transport = httpx.AsyncHTTPTransport() if bypass_proxy else None
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=not bypass_proxy)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    @property
    def default_model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict],
        params: dict,
        *,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        body = {
            "model": self._model,
            "messages": messages,
            **params,
        }
        if response_format is not None:
            body["response_format"] = response_format
        started = time.monotonic()
        logger.info(
            "openai_provider_http_start pid=%s provider_id=%s url=%s model=%s message_count=%s",
            os.getpid(),
            id(self),
            self._url,
            self._model,
            len(messages),
        )
        try:
            resp = await self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as e:
            logger.info(
                "openai_provider_http_error pid=%s provider_id=%s error_type=timeout elapsed_ms=%s",
                os.getpid(),
                id(self),
                int((time.monotonic() - started) * 1000),
            )
            raise ProviderError("timeout", str(e)) from e
        except httpx.ConnectError as e:
            logger.info(
                "openai_provider_http_error pid=%s provider_id=%s error_type=connection_error elapsed_ms=%s",
                os.getpid(),
                id(self),
                int((time.monotonic() - started) * 1000),
            )
            raise ProviderError("connection_error", str(e)) from e
        logger.info(
            "openai_provider_http_end pid=%s provider_id=%s status_code=%s elapsed_ms=%s",
            os.getpid(),
            id(self),
            resp.status_code,
            int((time.monotonic() - started) * 1000),
        )

        if resp.status_code == 429:
            raise ProviderError("rate_limited", resp.text)
        if resp.status_code >= 500:
            raise ProviderError("server_error", f"HTTP {resp.status_code}: {resp.text}")
        if resp.status_code >= 400:
            raise ProviderError("client_error", f"HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            raise ProviderError(
                "invalid_response", f"Non-JSON response from provider: {e}"
            ) from e
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return ProviderResponse(
            output_text=content,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            raw_response=data,
        )
