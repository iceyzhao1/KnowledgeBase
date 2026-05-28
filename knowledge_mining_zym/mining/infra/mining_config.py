"""Mining pipeline configuration — all values from .env.

Uses pydantic-settings to load environment variables, same pattern as pg_config.py.
Mining calls llm_service for both chat (template-based) and embedding.
Only the embedding model name and dimensions need to be configured here;
the actual API key, base URL for embedding are handled by llm_service.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

_REPO_ROOT = Path(__file__).resolve().parents[3]  # knowledge_mining/mining/infra/ -> CoreMasterKB/


class MiningConfig(BaseSettings):
    """Mining pipeline configuration, loaded from environment variables.

    Env vars:
        LLM_SERVICE_URL:        llm_service address (default: http://localhost:8900)
        EMBEDDING_MODEL:        model name sent to llm_service embedding endpoint
        EMBEDDING_DIMENSIONS:   embedding vector dimensions
        MINING_LLM_BYPASS_PROXY: bypass system proxy for LLM calls
        MINING_LLM_POLL_TIMEOUT_SECONDS: max wait per LLM batch poll
        MINING_LLM_POLL_INTERVAL_SECONDS: seconds between LLM status scans
        MINING_LLM_STATUS_ERROR_LIMIT: consecutive status errors before abandoning a task
        DOMAIN_PACK:            default domain pack ID
        MAX_WORKERS:            max concurrent workers for streaming pipeline
    """

    # LLM Service
    llm_service_url: str = "http://localhost:8900"
    mining_llm_bypass_proxy: bool = False
    mining_llm_poll_timeout_seconds: float = 180.0
    mining_llm_poll_interval_seconds: float = 1.0
    mining_llm_status_error_limit: int = 5

    # Embedding (via llm_service)
    embedding_model: str = "embedding-3"
    embedding_dimensions: int | None = None

    # Pipeline defaults
    domain_pack: str = "cloud_core_network"
    max_workers: int = 4

    model_config = {
        "env_prefix": "",
        "env_file": str(_REPO_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }
