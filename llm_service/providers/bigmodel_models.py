from __future__ import annotations

import httpx

from llm_service.providers.model_base import ModelProviderError


class BigModelProvider:
    """BigModel modality provider for embeddings and rerank."""

    def __init__(
        self,
        *,
        embedding_api_key: str = "",
        embedding_base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        embedding_model: str = "embedding-3",
        rerank_api_key: str = "",
        rerank_base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        rerank_model: str = "rerank",
        timeout: int = 60,
        bypass_proxy: bool = False,
    ) -> None:
        self._embedding_api_key = embedding_api_key
        self._embedding_base_url = embedding_base_url.rstrip("/")
        self._embedding_model = embedding_model
        self._rerank_api_key = rerank_api_key
        self._rerank_base_url = rerank_base_url.rstrip("/")
        self._rerank_model = rerank_model
        self._timeout = timeout
        self._bypass_proxy = bypass_proxy

    def _headers(self, api_key: str, capability: str) -> dict[str, str]:
        if not api_key:
            raise ModelProviderError(
                "not_configured",
                f"BigModel API key is not configured for {capability}",
            )
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def _post(
        self,
        base_url: str,
        api_key: str,
        capability: str,
        path: str,
        payload: dict,
    ) -> dict:
        transport = httpx.AsyncHTTPTransport() if self._bypass_proxy else None
        async with httpx.AsyncClient(transport=transport, timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{base_url}/{path.lstrip('/')}",
                    json=payload,
                    headers=self._headers(api_key, capability),
                )
            except httpx.TimeoutException as e:
                raise ModelProviderError("timeout", str(e)) from e
            except httpx.ConnectError as e:
                raise ModelProviderError("connection_error", str(e)) from e

        if resp.status_code == 429:
            raise ModelProviderError("rate_limited", resp.text)
        if resp.status_code >= 500:
            raise ModelProviderError("server_error", f"HTTP {resp.status_code}: {resp.text}")
        if resp.status_code >= 400:
            raise ModelProviderError("client_error", f"HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> dict:
        payload: dict = {
            "model": model or self._embedding_model,
            "input": texts,
        }
        if dimensions is not None:
            payload["dimensions"] = dimensions
        data = await self._post(
            self._embedding_base_url,
            self._embedding_api_key,
            "embedding",
            "/embeddings",
            payload,
        )
        return {
            "model": payload["model"],
            "data": data.get("data", []),
            "usage": data.get("usage"),
        }

    async def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        model: str | None = None,
        top_n: int | None = None,
    ) -> dict:
        payload = {
            "model": model or self._rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n or len(documents),
        }
        data = await self._post(
            self._rerank_base_url,
            self._rerank_api_key,
            "rerank",
            "/rerank",
            payload,
        )
        return {
            "model": payload["model"],
            "results": data.get("results", []),
        }
