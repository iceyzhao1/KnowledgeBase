from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llm_service.config import load_llm_config, dig, resolve_active_model_config
from llm_service.db import LlmRuntimeDB
from llm_service.pg_config import load_db_config
from llm_service.pg_schema import ensure_schema
from llm_service.providers.bigmodel_models import BigModelProvider
from llm_service.providers.model_base import ModelProviderProtocol
from llm_service.providers.base import ProviderProtocol
from llm_service.providers.openai_compatible import OpenAICompatibleProvider
from llm_service.runtime.model_service import ModelService
from llm_service.runtime.service import LLMService
from llm_service.runtime.worker import LeaseRecovery, Worker

logger = logging.getLogger(__name__)

# Module-level config set by __main__ before uvicorn forks workers.
_STARTUP_CONFIG: dict | None = None


def set_startup_config(cfg: dict) -> None:
    """Store config from __main__ so the uvicorn factory can reuse it."""
    global _STARTUP_CONFIG
    _STARTUP_CONFIG = cfg


def create_app_from_startup_config() -> FastAPI:
    """Uvicorn factory — reuses config loaded by __main__ (no double-fetch)."""
    if _STARTUP_CONFIG is None:
        raise RuntimeError("set_startup_config() must be called before create_app_from_startup_config()")
    return create_app(config=_STARTUP_CONFIG)


def create_app(
    config: dict | None = None,
    provider_factory: Callable[[], ProviderProtocol] | None = None,
    model_provider_factory: Callable[[], ModelProviderProtocol] | None = None,
    *,
    start_worker: bool = True,
) -> FastAPI:
    cfg = config or load_llm_config()
    _factory = provider_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # PostgreSQL — all params from control plane database.yaml
        pg_cfg = load_db_config()
        logger.info("Ensuring database schema for %s @ %s:%s", pg_cfg.dbname, pg_cfg.host, pg_cfg.port)
        ensure_schema(pg_cfg)

        db = LlmRuntimeDB.from_conninfo(
            pg_cfg.conninfo,
            pool_min=pg_cfg.pool_min,
            pool_max=pg_cfg.pool_max,
        )
        await db.open()

        health = await db.health_check()
        if not health.get("connected"):
            await db.close()
            raise RuntimeError(f"Database health check failed: {health.get('error', 'cannot connect')}")
        if not health.get("tables_ok"):
            logger.warning("Database tables check: %s", health)
        logger.info(
            "Database health check passed (connected=%s, tables=%s, tasks=%s)",
            health["connected"], health["tables_ok"], health.get("task_count", "?"),
        )

        # Providers — all params from config dict, no defaults
        # Resolve multi-model: if provider.models exists, merge active_model overrides
        active_provider = resolve_active_model_config(cfg)
        provider = _factory() if _factory else OpenAICompatibleProvider(
            base_url=active_provider["base_url"],
            api_key=active_provider["api_key"],
            model=active_provider.get("model", active_provider.get("active_model", "")),
            headers=active_provider.get("headers") or {},
            timeout=active_provider.get("timeout", 30),
            bypass_proxy=active_provider.get("bypass_proxy", False),
        )
        model_provider = (
            model_provider_factory()
            if model_provider_factory
            else BigModelProvider(
                embedding_api_key=dig(cfg, "embedding", "api_key"),
                embedding_url=dig(cfg, "embedding", "base_url"),
                embedding_model=dig(cfg, "embedding", "model"),
                rerank_api_key=dig(cfg, "rerank", "api_key"),
                rerank_url=dig(cfg, "rerank", "base_url"),
                rerank_model=dig(cfg, "rerank", "model"),
                timeout=dig(cfg, "embedding", "timeout"),
                bypass_proxy=dig(cfg, "embedding", "bypass_proxy"),
                extra_headers=dig(cfg, "embedding", "headers") or {},
            )
        )

        # Template registry with configurable cache TTL
        from llm_service.runtime.template_registry import TemplateRegistry
        templates = TemplateRegistry(db=db, cache_ttl=dig(cfg, "template", "cache_ttl"))

        # Embedding dimensions: optional in YAML, None means don't pass to provider
        _embedding_dimensions = cfg.get("embedding", {}).get("dimensions")

        svc = LLMService(db=db, provider=provider, config=cfg, model_provider=model_provider, templates=templates)
        model_svc = ModelService(
            model_provider, db=db,
            default_embedding_model=dig(cfg, "embedding", "model"),
            default_rerank_model=dig(cfg, "rerank", "model"),
            default_embedding_dimensions=_embedding_dimensions,
        )
        app.state.llm_service = svc
        app.state.model_service = model_svc
        app.state.db = db

        worker = None
        recovery = None
        try:
            if start_worker:
                worker = Worker(
                    db=db,
                    task_manager=svc._mgr,
                    event_bus=svc._bus,
                    provider=provider,
                    model_provider=model_provider,
                    templates=templates,
                    concurrency=dig(cfg, "worker", "concurrency"),
                    poll_interval=dig(cfg, "worker", "poll_interval"),
                    llm_service=svc,
                )
                await worker.start()

                recovery = LeaseRecovery(
                    db=db,
                    task_manager=svc._mgr,
                    event_bus=svc._bus,
                    interval=dig(cfg, "task", "lease_recovery_interval"),
                )
                await recovery.start()
        except Exception:
            if recovery:
                await recovery.stop()
            if worker:
                await worker.stop()
            await db.close()
            raise

        yield

        if recovery:
            await recovery.stop()
        if worker:
            await worker.stop()
            try:
                await db.execute(
                    "UPDATE agent_llm_tasks SET status = 'queued', lease_expires_at = NULL "
                    "WHERE status = 'running'"
                )
                logger.info("Re-queued in-flight tasks on shutdown")
            except Exception:
                logger.exception("Failed to re-queue in-flight tasks")
        if hasattr(provider, 'close'):
            await provider.close()
        if hasattr(model_provider, 'close'):
            await model_provider.close()
        await db.close()

    app = FastAPI(title="LLM Service", version="0.1.0", lifespan=lifespan)
    app.state.config = cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from llm_service.api.admin import router as admin_router
    from llm_service.api.health import router as health_router
    from llm_service.api.model_api import router as model_api_router
    from llm_service.api.results import router as results_router
    from llm_service.api.stats import router as stats_router
    from llm_service.api.tasks import router as tasks_router
    from llm_service.api.templates import router as templates_router

    app.include_router(admin_router)
    app.include_router(health_router)
    app.include_router(model_api_router)
    app.include_router(tasks_router)
    app.include_router(results_router)
    app.include_router(templates_router)
    app.include_router(stats_router)

    return app
