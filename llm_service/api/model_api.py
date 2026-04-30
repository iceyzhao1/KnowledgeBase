from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from llm_service.models import (
    EmbeddingRequest,
    EmbeddingResponse,
    RerankRequest,
    RerankResponse,
)
from llm_service.providers.model_base import ModelProviderError

router = APIRouter(prefix="/api/v1/models", tags=["models"])


def _to_http_error(exc: ModelProviderError) -> HTTPException:
    if exc.error_type == "not_configured":
        return HTTPException(status_code=503, detail=exc.message)
    if exc.error_type == "rate_limited":
        return HTTPException(status_code=429, detail=exc.message)
    if exc.error_type in ("timeout", "connection_error", "server_error"):
        return HTTPException(status_code=502, detail=exc.message)
    return HTTPException(status_code=400, detail=exc.message)


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(body: EmbeddingRequest, request: Request):
    svc = request.app.state.model_service
    try:
        return await svc.embed(body)
    except ModelProviderError as exc:
        raise _to_http_error(exc) from exc


@router.post("/rerank", response_model=RerankResponse)
async def create_rerank(body: RerankRequest, request: Request):
    svc = request.app.state.model_service
    try:
        return await svc.rerank(body)
    except ModelProviderError as exc:
        raise _to_http_error(exc) from exc
