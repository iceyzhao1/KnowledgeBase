"""Run Mining API: python -m knowledge_mining.mining.api"""
import asyncio
import os
import sys

# Windows: psycopg async requires SelectorEventLoop, not ProactorEventLoop.
# uvicorn hardcodes ProactorEventLoop on Windows, so we monkey-patch its factory.
if sys.platform == "win32":
    import uvicorn.loops.asyncio as _uv_loop

    _original_factory = _uv_loop.asyncio_loop_factory

    def _selector_loop_factory(use_subprocess: bool = False):
        return asyncio.SelectorEventLoop

    _uv_loop.asyncio_loop_factory = _selector_loop_factory

from knowledge_mining.mining.api.app import app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("MINING_API_PORT", "8901"))
    uvicorn.run(app, host="0.0.0.0", port=port)
