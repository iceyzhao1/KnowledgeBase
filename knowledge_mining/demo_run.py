"""Demo runner: clear PG tables + run mining pipeline with demo config.

Usage:
    python knowledge_mining/demo_run.py

All PG connection params come from .env (PG_HOST, PG_PORT, PG_DBNAME, etc.).
LLM service URL is read from LLM_BASE_URL env var (default: http://localhost:8900).
Embedding API key is read from EMBEDDING_API_KEY env var.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("demo_run")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "knowledge_base"

# Tables to truncate (order matters for FK constraints)
_ASSET_TABLES = [
    "asset_retrieval_embeddings",
    "asset_retrieval_units",
    "asset_raw_segment_relations",
    "asset_raw_segments",
    "asset_build_document_snapshots",
    "asset_publish_releases",
    "asset_builds",
    "asset_document_snapshot_links",
    "asset_document_snapshots",
    "asset_documents",
    "asset_source_batches",
]
_RUNTIME_TABLES = [
    "mining_run_stage_events",
    "mining_run_documents",
    "mining_runs",
]


def _clear_tables(conn, tables: list[str], schema_label: str) -> None:
    """TRUNCATE all listed tables."""
    for table in tables:
        conn.execute(f"TRUNCATE TABLE {table} CASCADE")
        logger.info("  TRUNCATED %s.%s", schema_label, table)


def clear_all_data(cfg) -> None:
    """Clear all mining data from PG."""
    import psycopg

    conninfo = cfg.conninfo
    logger.info("Clearing all mining data from PG...")

    with psycopg.connect(conninfo, autocommit=True) as conn:
        _clear_tables(conn, _ASSET_TABLES, "asset_core")
        _clear_tables(conn, _RUNTIME_TABLES, "mining_runtime")

    logger.info("All tables cleared.")


def main() -> None:
    from knowledge_mining.mining.infra.pg_config import MiningDbConfig
    from knowledge_mining.mining.jobs.run import run

    cfg = MiningDbConfig()

    # Step 1: Clear all data
    clear_all_data(cfg)

    # Step 2: Run mining pipeline
    llm_base_url = os.environ.get("LLM_BASE_URL", "http://localhost:8900")
    embedding_api_key = os.environ.get("EMBEDDING_API_KEY", "")

    logger.info("Starting demo mining run...")
    logger.info("  input_path:  %s", DATA_DIR)
    logger.info("  llm_base_url: %s", llm_base_url)
    logger.info("  embedding_api_key: %s", "***provided***" if embedding_api_key else "(none)")

    t0 = time.perf_counter()
    result = run(
        input_path=DATA_DIR,
        llm_base_url=llm_base_url,
        embedding_api_key=embedding_api_key or None,
        domain_pack="cloud_core_network",
        publish_on_partial_failure=True,
    )
    elapsed = time.perf_counter() - t0

    # Step 3: Print summary
    print("\n" + "=" * 60)
    print("DEMO MINING RUN COMPLETE")
    print("=" * 60)
    print(f"  Run ID:            {result['run_id']}")
    print(f"  Status:            {result['status']}")
    print(f"  Total documents:   {result['total_documents']}")
    print(f"  Committed:         {result['committed_count']}")
    print(f"  New:               {result['new_count']}")
    print(f"  Updated:           {result['updated_count']}")
    print(f"  Failed:            {result['failed_count']}")
    print(f"  Skipped:           {result['skipped_count']}")
    print(f"  Build ID:          {result['build_id']}")
    print(f"  Release ID:        {result['release_id']}")
    print(f"  Elapsed:           {elapsed:.1f}s")
    print("=" * 60)

    if result["status"] != "completed" or result["failed_count"] > 0:
        logger.warning("Run completed with failures. Check mining_run_stage_events for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
