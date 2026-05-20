"""Run LLM Service: python -m llm_service"""
import asyncio
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
# uvicorn hardcodes ProactorEventLoop on Windows, so we monkey-patch its factory.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop  # noqa: E402

    _uv_loop.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop

import uvicorn  # noqa: E402

from llm_service.config import LLMServiceConfig  # noqa: E402

cfg = LLMServiceConfig()
uvicorn.run(
    "llm_service.main:create_app",
    host=cfg.host,
    port=cfg.port,
    factory=True,
)
