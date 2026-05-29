"""Run LLM Service: python -m llm_service"""
import asyncio
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop  # noqa: E402
    _uv_loop.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop

import uvicorn  # noqa: E402

from llm_service.config import load_llm_config, dig  # noqa: E402
from llm_service.main import set_startup_config  # noqa: E402

cfg = load_llm_config()
set_startup_config(cfg)

uvicorn.run(
    "llm_service.main:create_app_from_startup_config",
    host=dig(cfg, "host"),
    port=dig(cfg, "port"),
    factory=True,
)
