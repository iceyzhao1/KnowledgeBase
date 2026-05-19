"""Run LLM Service: python -m llm_service"""
import asyncio
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
# uvicorn hardcodes ProactorEventLoop on Windows, so we monkey-patch its factory.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop

    _original_factory = _uv_loop.asyncio_loop_factory

    def _selector_loop_factory(use_subprocess: bool = False):
        return asyncio.SelectorEventLoop

    _uv_loop.asyncio_loop_factory = _selector_loop_factory

import uvicorn

from llm_service.config import LLMServiceConfig


def main():
    cfg = LLMServiceConfig()
    uvicorn.run(
        "llm_service.main:create_app",
        host=cfg.host,
        port=cfg.port,
        factory=True,
    )


if __name__ == "__main__":
    main()
