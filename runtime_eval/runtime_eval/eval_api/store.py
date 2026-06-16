"""Artifact store — SQLite only.

The JSON file backend was removed; SQLite (``SqliteStore``) is now the single
source of truth for all artifacts (projects / documents / suites / responses /
runs / retrieval sets / retrieval runs / gold). Reports stay on disk as
Markdown/HTML — only their directory is managed by the store.

``Store`` is kept as an alias of :class:`SqliteStore` so existing type hints
(``store: Store``) and direct construction (``Store(config)``) keep working.
"""

from __future__ import annotations

from .config import ApiConfig
from .sqlite_store import SqliteStore

# Backward-compatible alias: callers type-hint and construct ``Store`` directly.
Store = SqliteStore


def create_store(config: ApiConfig) -> SqliteStore:
    """Build the artifact store (SQLite, single backend)."""

    return SqliteStore(config)
