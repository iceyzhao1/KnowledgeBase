"""FastAPI application factory for the telecom_eval evaluation API (v2)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from telecom_eval.api.config import (
    build_judge_service,
    build_retrieval_adapter_factory,
    build_search_fn,
    build_segment_resolver,
    load_config,
)
from telecom_eval.api.routes import (
    cases,
    comparisons,
    dataset_imports,
    datasets,
    debug,
    health,
    reports,
    retrieval,
    runs,
)
from telecom_eval.services import (
    DatasetService,
    DebugService,
    EvaluationService,
    EvaluationQueueService,
    ReportViewService,
)
from telecom_eval.storage.sqlite_store import SQLiteEvaluationStore

API_PREFIX = "/api/v1/eval"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    config = load_config(db_path)
    store = SQLiteEvaluationStore(config.db_path)
    judge_service = build_judge_service(config, store)

    app = FastAPI(title="telecom_eval API", version="2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = config
    app.state.store = store
    app.state.judge_service = judge_service
    retrieval_factory = build_retrieval_adapter_factory(config)
    segment_resolver = build_segment_resolver(config)
    app.state.evaluation_service = EvaluationService(
        store,
        judge_service=judge_service,
        retrieval_adapter_factory=retrieval_factory,
        segment_resolver=segment_resolver,
    )
    app.state.evaluation_queue_service = EvaluationQueueService(
        store=store,
        evaluation_service=app.state.evaluation_service,
        max_workers=config.runner_max_concurrent_runs,
    )
    app.state.report_view_service = ReportViewService(store)
    app.state.debug_service = DebugService(store)
    app.state.dataset_service = DatasetService(store)
    app.state.retrieval_search = build_search_fn(config)
    app.state.evaluation_queue_service.recover_pending_runs()

    for module in (
        health,
        datasets,
        dataset_imports,
        retrieval,
        cases,
        runs,
        reports,
        comparisons,
        debug,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    return app
