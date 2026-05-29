"""Run LLM Service: python -m llm_service"""
import asyncio
import logging
import os
from pathlib import Path
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop  # noqa: E402
    _uv_loop.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop

import uvicorn  # noqa: E402

from llm_service.config import load_llm_config, dig  # noqa: E402
from llm_service.main import set_startup_config  # noqa: E402


def _configure_diagnostic_logging() -> None:
    """Write LLM worker diagnostics to a local file.

    This intentionally avoids logging prompt/message bodies. It records process,
    app, worker, task, and provider-call lifecycle events so queue concurrency
    can be reconstructed after a mining run.
    """
    log_path = Path(os.getenv("LLM_WORKER_DEBUG_LOG", "data/llm_worker_debug.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    resolved = str(log_path.resolve())
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved:
            return

    handler = logging.FileHandler(resolved, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d thread=%(threadName)s "
        "logger=%(name)s %(message)s"
    ))
    root.addHandler(handler)
    logging.getLogger(__name__).info("diagnostic_log_configured path=%s", resolved)


_configure_diagnostic_logging()

cfg = load_llm_config()
set_startup_config(cfg)

uvicorn.run(
    "llm_service.main:create_app_from_startup_config",
    host=dig(cfg, "host"),
    port=dig(cfg, "port"),
    factory=True,
)
