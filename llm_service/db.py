"""PostgreSQL database adapter for llm_service.

Uses psycopg[pool] AsyncConnectionPool for connection management.
Replaces the previous aiosqlite implementation.
"""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class LlmRuntimeDB:
    """PostgreSQL adapter for agent_llm_runtime tables."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_conninfo(cls, conninfo: str, *, pool_min: int = 2, pool_max: int = 10) -> "LlmRuntimeDB":
        """Create adapter from a connection string."""
        pool = AsyncConnectionPool(
            conninfo,
            min_size=pool_min,
            max_size=pool_max,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        return cls(pool)

    async def open(self) -> None:
        """Open the connection pool."""
        await self._pool.open()

    async def close(self) -> None:
        """Close the connection pool."""
        await self._pool.close()

    @property
    def pool(self) -> AsyncConnectionPool:
        return self._pool

    async def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a single statement (auto-committed)."""
        async with self._pool.connection() as conn:
            await conn.execute(sql, params)

    async def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Execute and return one row as dict."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute and return all rows as list of dicts."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def commit(self) -> None:
        """No-op: each operation auto-commits via context manager.

        Kept for backward compatibility with code that calls commit().
        """
        pass
