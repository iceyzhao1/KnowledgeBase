"""Run LLM Service: python -m llm_service"""
import asyncio
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
# Set event loop policy before importing uvicorn so it takes effect globally.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

from llm_service.config import LLMServiceConfig  # noqa: E402

cfg = LLMServiceConfig()
uvicorn.run(
    "llm_service.main:create_app",
    host=cfg.host,
    port=cfg.port,
    factory=True,
)
