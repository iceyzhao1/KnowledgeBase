"""Admin endpoints — config hot-reload."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from llm_service.config import (
    dig,
    fetch_config_from_control_plane,
    resolve_active_model_config,
)

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

    # 2. Resolve active model config (handles multi-model merge)
    active_provider = resolve_active_model_config(new_cfg)

    # 3. Update provider (chat) — use resolved config
    provider = app.state.llm_service._provider
    if hasattr(provider, "_url"):
        provider._url = active_provider["base_url"].rstrip("/")
        provider._api_key = active_provider["api_key"]
        provider._model = active_provider.get("model", active_provider.get("active_model", ""))
        provider._extra_headers = active_provider.get("headers") or {}
        provider._timeout = active_provider.get("timeout", 30)

    # 4. Update model provider (embedding + rerank)
    model_provider = app.state.llm_service._model_provider
    if hasattr(model_provider, "_embedding_api_key"):
        model_provider._embedding_api_key = dig(new_cfg, "embedding", "api_key")
        model_provider._embedding_url = dig(new_cfg, "embedding", "base_url").rstrip("/")
        model_provider._embedding_model = dig(new_cfg, "embedding", "model")
        model_provider._rerank_api_key = dig(new_cfg, "rerank", "api_key")
        model_provider._rerank_url = dig(new_cfg, "rerank", "base_url").rstrip("/")
        model_provider._rerank_model = dig(new_cfg, "rerank", "model")

    # 5. Update service-level config dict (used by dig() calls in runtime)
    app.state.config = new_cfg
    app.state.llm_service._config = new_cfg

    # 6. Update model service defaults
    if hasattr(app.state, "model_service"):
        app.state.model_service._default_embedding_model = dig(new_cfg, "embedding", "model")
        app.state.model_service._default_rerank_model = dig(new_cfg, "rerank", "model")

    logger.info("Config reloaded from control plane successfully")
    return {
        "ok": True,
        "config": {
            "active_model": new_cfg.get("provider", {}).get("active_model"),
            "provider_model": active_provider.get("model"),
            "provider_base_url": active_provider.get("base_url"),
            "embedding_model": dig(new_cfg, "embedding", "model"),
            "rerank_model": dig(new_cfg, "rerank", "model"),
            "worker_concurrency": dig(new_cfg, "worker", "concurrency"),
        },
    }


@router.get("/worker-status")
async def worker_status(request: Request):
    """Diagnostic: return actual Worker concurrency and poll interval."""
    worker = getattr(request.app.state, "worker", None)
    if not worker:
        return {"ok": True, "worker_running": False}
    return {
        "ok": True,
        "worker_running": worker._running,
        "concurrency": worker._concurrency,
        "poll_interval": worker._poll_interval,
        "active_tasks": len(worker._tasks),
    }
