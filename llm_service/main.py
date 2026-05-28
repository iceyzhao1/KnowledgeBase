from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llm_service.config import load_llm_config, dig
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


def create_app_with_config() -> FastAPI:
    """Factory for uvicorn — loads config once and passes to create_app."""
    cfg = load_llm_config()
    return create_app(config=cfg)


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
        provider = _factory() if _factory else OpenAICompatibleProvider(
            base_url=dig(cfg, "provider", "base_url"),
            api_key=dig(cfg, "provider", "api_key"),
            model=dig(cfg, "provider", "model"),
            headers={**(dig(cfg, "provider", "headers") or {}), **(dig(cfg, "model", "extra_headers") or {})},
            timeout=dig(cfg, "provider", "timeout"),
            bypass_proxy=dig(cfg, "provider", "bypass_proxy"),
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
                timeout=dig(cfg, "model", "timeout"),
                bypass_proxy=dig(cfg, "model", "bypass_proxy"),
                extra_headers=dig(cfg, "model", "extra_headers") or {},
            )
        )

        # Template registry with configurable cache TTL
        from llm_service.runtime.template_registry import TemplateRegistry
        templates = TemplateRegistry(db=db, cache_ttl=dig(cfg, "template", "cache_ttl"))

        svc = LLMService(db=db, provider=provider, config=cfg, model_provider=model_provider, templates=templates)
        model_svc = ModelService(
            model_provider, db=db,
            default_embedding_model=dig(cfg, "embedding", "model"),
            default_rerank_model=dig(cfg, "rerank", "model"),
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

    from llm_service.api.health import router as health_router
    from llm_service.api.model_api import router as model_api_router
    from llm_service.api.results import router as results_router
    from llm_service.api.stats import router as stats_router
    from llm_service.api.tasks import router as tasks_router
    from llm_service.api.templates import router as templates_router

    app.include_router(health_router)
    app.include_router(model_api_router)
    app.include_router(tasks_router)
    app.include_router(results_router)
    app.include_router(templates_router)
    app.include_router(stats_router)

    return app
