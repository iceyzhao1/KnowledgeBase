from __future__ import annotations

import uuid
from datetime import datetime, timezone

from llm_service.models import (
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    RerankRequest,
    RerankResponse,
    RerankResult,
)
from llm_service.providers.model_base import ModelProviderProtocol


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelService:
    """Synchronous-style model facade exposed over HTTP for mining/serving."""

    def __init__(self, provider: ModelProviderProtocol, db=None):
        self._provider = provider
        self._db = db

    def _log_call(self, call_type: str, model: str, input_count: int,
                  status: str, latency_ms: int | None, token_usage: int | None,
                  error_message: str | None) -> None:
        """Append a lightweight log row (fire-and-forget, best-effort)."""
        if self._db is None:
            return
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._db.execute(
                "INSERT INTO agent_llm_model_calls "
                "(id, call_type, model, input_count, status, latency_ms, token_usage, error_message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, call_type, model, input_count, status,
                 latency_ms, token_usage, error_message, _utcnow()),
            ))
        except Exception:
            pass

    async def embed(self, body: EmbeddingRequest) -> EmbeddingResponse:
        t0 = datetime.now(timezone.utc)
        model = body.model or "embedding-3"
        input_count = len(body.input) if isinstance(body.input, list) else 1
        try:
            raw = await self._provider.embed(
                body.input,
                model=model,
                dimensions=body.dimensions,
            )
            latency = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
            usage = raw.get("usage") or {}
            token_usage = usage.get("total_tokens") or usage.get("prompt_tokens")
            self._log_call("embedding", model, input_count, "succeeded", latency, token_usage, None)

            data = sorted(raw.get("data", []), key=lambda item: item.get("index", 0))
            return EmbeddingResponse(
                model=raw.get("model") or model or "",
                data=[
                    EmbeddingData(
                        index=int(item.get("index", idx)),
                        embedding=item.get("embedding", []),
                    )
                    for idx, item in enumerate(data)
                ],
                usage=usage,
            )
        except Exception as e:
            latency = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
            self._log_call("embedding", model, input_count, "failed", latency, None, str(e)[:500])
            raise

    async def rerank(self, body: RerankRequest) -> RerankResponse:
        t0 = datetime.now(timezone.utc)
        model = body.model or "rerank"
        input_count = len(body.documents)
        try:
            raw = await self._provider.rerank(
                body.query,
                body.documents,
                model=model,
                top_n=body.top_n,
            )
            latency = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
            usage = raw.get("usage") or {}
            token_usage = usage.get("total_tokens")
            self._log_call("rerank", model, input_count, "succeeded", latency, token_usage, None)

            return RerankResponse(
                model=raw.get("model") or model or "",
                results=[
                    RerankResult(
                        index=int(item.get("index", idx)),
                        relevance_score=float(item.get("relevance_score", 0.0)),
                        document=item.get("document"),
                    )
                    for idx, item in enumerate(raw.get("results", []))
                ],
            )
        except Exception as e:
            latency = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
            self._log_call("rerank", model, input_count, "failed", latency, None, str(e)[:500])
            raise
