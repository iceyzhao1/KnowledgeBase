from __future__ import annotations

from llm_service.models import (
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    RerankRequest,
    RerankResponse,
    RerankResult,
)
from llm_service.providers.model_base import ModelProviderProtocol


class ModelService:
    """Synchronous-style model facade exposed over HTTP for mining/serving."""

    def __init__(self, provider: ModelProviderProtocol):
        self._provider = provider

    async def embed(self, body: EmbeddingRequest) -> EmbeddingResponse:
        raw = await self._provider.embed(
            body.input,
            model=body.model,
            dimensions=body.dimensions,
        )
        data = sorted(raw.get("data", []), key=lambda item: item.get("index", 0))
        return EmbeddingResponse(
            model=raw.get("model") or body.model or "",
            data=[
                EmbeddingData(
                    index=int(item.get("index", idx)),
                    embedding=item.get("embedding", []),
                )
                for idx, item in enumerate(data)
            ],
            usage=raw.get("usage"),
        )

    async def rerank(self, body: RerankRequest) -> RerankResponse:
        raw = await self._provider.rerank(
            body.query,
            body.documents,
            model=body.model,
            top_n=body.top_n,
        )
        return RerankResponse(
            model=raw.get("model") or body.model or "",
            results=[
                RerankResult(
                    index=int(item.get("index", idx)),
                    relevance_score=float(item.get("relevance_score", 0.0)),
                    document=item.get("document"),
                )
                for idx, item in enumerate(raw.get("results", []))
            ],
        )
