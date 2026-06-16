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
    def from_conninfo(
        cls,
        conninfo: str,
        *,
        pool_min: int = 2,
        pool_max: int = 10,
        connect_timeout: int = 10,
        max_idle: float = 60.0,
        max_lifetime: float = 600.0,
    ) -> "LlmRuntimeDB":
        """Create adapter from a connection string.

        Resilience knobs (added to keep a shared PostgreSQL server from being
        exhausted by idle connections, and to fail fast instead of stalling
        30s when the server can no longer accept new connections):

        - ``connect_timeout``: cap each TCP/connect attempt (seconds). Without
          it a saturated server makes the pool hang until the 30s getconn
          timeout, surfacing as ``PoolTimeout``. Passed through to libpq.
        - ``max_idle``: close idle connections above ``pool_min`` after this
          many seconds, returning them to the shared server instead of holding
          them open forever (the root cause of the 100-connection exhaustion).
        - ``max_lifetime``: recycle every connection after this long so even a
          busy pool periodically releases and re-establishes its connections.
        - ``check``: validate a connection before handing it out; a broken or
          server-closed connection is transparently discarded and replaced.
        """
        pool = AsyncConnectionPool(
            conninfo,
            min_size=pool_min,
            max_size=pool_max,
            open=False,
            max_idle=max_idle,
            max_lifetime=max_lifetime,
            check=AsyncConnectionPool.check_connection,
            kwargs={"row_factory": dict_row, "connect_timeout": connect_timeout},
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

    async def run(self, fn):
        """Run an async function with a single pooled connection.

        Usage:
            async def my_queries(conn):
                r1 = await (await conn.execute("SELECT ...")).fetchall()
                r2 = await (await conn.execute("SELECT ...")).fetchone()
                return r1, r2
            result = await db.run(my_queries)
        """
        async with self._pool.connection() as conn:
            return await fn(conn)

    async def commit(self) -> None:
        """No-op: each operation auto-commits via context manager.

        Kept for backward compatibility with code that calls commit().
        """
        pass

    async def health_check(self) -> dict[str, Any]:
        """Run a lightweight DB health check. Returns status dict.

        Verifies: connectivity, schema tables exist, can read/write.
        """
        result: dict[str, Any] = {"connected": False, "tables_ok": False}

        # 1. Can we connect and run a simple query?
        try:
            row = await self.fetchone("SELECT 1 AS ok")
            if row and row.get("ok") == 1:
                result["connected"] = True
        except Exception as e:
            result["error"] = str(e)
            return result

        # 2. Do the core tables exist?
        expected_tables = {
            "agent_llm_tasks",
            "agent_llm_requests",
            "agent_llm_attempts",
            "agent_llm_results",
            "agent_llm_events",
            "agent_llm_prompt_templates",
        }
        try:
            rows = await self.fetchall(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'agent_llm_%%'"
            )
            found = {r["table_name"] for r in rows}
            missing = expected_tables - found
            result["tables_ok"] = len(missing) == 0
            if missing:
                result["missing_tables"] = sorted(missing)
        except Exception as e:
            result["tables_error"] = str(e)

        # 3. Quick stats for visibility
        try:
            count_row = await self.fetchone("SELECT COUNT(*) AS cnt FROM agent_llm_tasks")
            result["task_count"] = count_row["cnt"] if count_row else 0
        except Exception:
            result["task_count"] = "error"

        return result
