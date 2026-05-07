from pathlib import Path

import aiosqlite

_SCHEMA_DIR = (
    Path(__file__).resolve().parent.parent
    / "databases"
    / "agent_llm_runtime"
    / "schemas"
)


async def init_db(db_path: str) -> aiosqlite.Connection:
    """Open (or create) the SQLite database and ensure schema is applied.

    Uses isolation_level=None (autocommit) so that every SQL statement
    commits immediately.  This prevents stale-read snapshots caused by
    implicit transactions lingering across shared aiosqlite connections
    (e.g. the API connection used by both submit and dashboard handlers).
    All existing commit() calls become harmless no-ops.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path, isolation_level=None, timeout=30.0)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = OFF")
    await conn.execute("PRAGMA busy_timeout = 30000")
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")

    # Run schema files in order
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sqlite.sql")):
        schema_sql = sql_file.read_text(encoding="utf-8")
        await conn.executescript(schema_sql)

    await conn.execute("PRAGMA foreign_keys = ON")
    return conn
