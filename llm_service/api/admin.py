"""Admin endpoints — config hot-reload."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from llm_service.config import fetch_config_from_control_plane, dig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/reload-config")
async def reload_config(request: Request):
    """Re-fetch config from control plane and update runtime components.

    This allows changing provider keys, model names, timeouts, etc.
    without restarting the service. Call this after updating config via
    the main_control_service frontend.
    """
    app = request.app

    # 1. Fetch fresh config from control plane
    try:
        new_cfg = fetch_config_from_control_plane()
    except Exception as exc:
        logger.exception("Failed to fetch config from control plane")
        return {"ok": False, "error": str(exc)}

    # 2. Update provider (chat)
    provider = app.state.llm_service._provider
    if hasattr(provider, "_url"):
        provider._url = dig(new_cfg, "provider", "base_url").rstrip("/")
        provider._api_key = dig(new_cfg, "provider", "api_key")
        provider._model = dig(new_cfg, "provider", "model")
        provider._extra_headers = {**(dig(new_cfg, "provider", "headers") or {}),
                                   **(dig(new_cfg, "model", "extra_headers") or {})}
        provider._timeout = dig(new_cfg, "provider", "timeout")

    # 3. Update model provider (embedding + rerank)
    model_provider = app.state.llm_service._model_provider
    if hasattr(model_provider, "_embedding_api_key"):
        model_provider._embedding_api_key = dig(new_cfg, "embedding", "api_key")
        model_provider._embedding_url = dig(new_cfg, "embedding", "base_url").rstrip("/")
        model_provider._embedding_model = dig(new_cfg, "embedding", "model")
        model_provider._rerank_api_key = dig(new_cfg, "rerank", "api_key")
        model_provider._rerank_url = dig(new_cfg, "rerank", "base_url").rstrip("/")
        model_provider._rerank_model = dig(new_cfg, "rerank", "model")
        model_provider._timeout = dig(new_cfg, "model", "timeout")
        model_provider._extra_headers = dig(new_cfg, "model", "extra_headers") or {}

    # 4. Update service-level config dict (used by dig() calls in runtime)
    app.state.config = new_cfg
    app.state.llm_service._config = new_cfg

    # 5. Update model service defaults
    if hasattr(app.state, "model_service"):
        app.state.model_service._default_embedding_model = dig(new_cfg, "embedding", "model")
        app.state.model_service._default_rerank_model = dig(new_cfg, "rerank", "model")

    logger.info("Config reloaded from control plane successfully")
    return {
        "ok": True,
        "config": {
            "provider_model": dig(new_cfg, "provider", "model"),
            "embedding_model": dig(new_cfg, "embedding", "model"),
            "rerank_model": dig(new_cfg, "rerank", "model"),
            "worker_concurrency": dig(new_cfg, "worker", "concurrency"),
        },
    }
