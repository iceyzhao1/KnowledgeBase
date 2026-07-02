"""SQLite schema for the v2 evaluation store.

结构化列用于过滤/排序，完整 Pydantic 模型存进 ``payload_json``（UTF-8，中文原文）。
"""

from __future__ import annotations

import sqlite3

SCHEMA_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS eval_datasets (
      dataset_id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      scenario_id TEXT NOT NULL,
      dataset_type TEXT NOT NULL,
      status TEXT NOT NULL,
      case_count INTEGER NOT NULL DEFAULT 0,
      confirmed_case_count INTEGER NOT NULL DEFAULT 0,
      tags_json TEXT NOT NULL DEFAULT '[]',
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_datasets_status ON eval_datasets(status, scenario_id)",
    """
    CREATE TABLE IF NOT EXISTS eval_dataset_imports (
      import_id TEXT PRIMARY KEY,
      dataset_id TEXT NOT NULL,
      filename TEXT NOT NULL,
      status TEXT NOT NULL,
      total_rows INTEGER NOT NULL DEFAULT 0,
      valid_rows INTEGER NOT NULL DEFAULT 0,
      draft_rows INTEGER NOT NULL DEFAULT 0,
      rejected_rows INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      completed_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_dataset_imports_dataset ON eval_dataset_imports(dataset_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS eval_dataset_snapshots (
      dataset_snapshot_id TEXT PRIMARY KEY,
      dataset_id TEXT NOT NULL,
      dataset_version TEXT NOT NULL,
      case_ids_json TEXT NOT NULL,
      case_fingerprints_json TEXT NOT NULL,
      confirmed_only INTEGER NOT NULL DEFAULT 1,
      gold_policy TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_dataset_snapshots_dataset ON eval_dataset_snapshots(dataset_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS eval_cases (
      case_id TEXT PRIMARY KEY,
      dataset_id TEXT NOT NULL,
      scenario_id TEXT NOT NULL,
      task_type TEXT NOT NULL,
      answerability TEXT NOT NULL,
      gold_status TEXT NOT NULL,
      risk_level TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_cases_dataset ON eval_cases(dataset_id, gold_status, scenario_id)",
    """
    CREATE TABLE IF NOT EXISTS eval_runs (
      run_id TEXT PRIMARY KEY,
      subject_id TEXT NOT NULL,
      dataset_id TEXT NOT NULL,
      dataset_snapshot_id TEXT,
      eval_type TEXT NOT NULL,
      status TEXT NOT NULL,
      allow_llm_judge INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      started_at TEXT,
      completed_at TEXT,
      payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_runs_dataset ON eval_runs(dataset_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS eval_traces (
      trace_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      case_id TEXT NOT NULL,
      subject_id TEXT NOT NULL,
      trace_type TEXT NOT NULL,
      latency_ms INTEGER NOT NULL DEFAULT 0,
      has_error INTEGER NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_traces_run_case ON eval_traces(run_id, case_id, trace_type)",
    """
    CREATE TABLE IF NOT EXISTS eval_artifacts (
      artifact_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      case_id TEXT,
      artifact_type TEXT NOT NULL,
      cache_key TEXT,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_artifacts_run_case ON eval_artifacts(run_id, case_id, artifact_type)",
    "CREATE INDEX IF NOT EXISTS idx_eval_artifacts_cache_key ON eval_artifacts(cache_key)",
    """
    CREATE TABLE IF NOT EXISTS eval_metrics (
      metric_result_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      case_id TEXT,
      subject_id TEXT NOT NULL,
      metric_id TEXT NOT NULL,
      level TEXT NOT NULL,
      status TEXT NOT NULL,
      value_json TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_metrics_run_case ON eval_metrics(run_id, case_id, metric_id)",
    """
    CREATE TABLE IF NOT EXISTS eval_diagnoses (
      diagnosis_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      case_id TEXT NOT NULL,
      failure_type TEXT NOT NULL,
      severity TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_diagnoses_run_case ON eval_diagnoses(run_id, case_id, failure_type)",
    """
    CREATE TABLE IF NOT EXISTS eval_reports (
      report_id TEXT PRIMARY KEY,
      run_id TEXT NOT NULL,
      report_type TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      markdown TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_eval_reports_run ON eval_reports(run_id, report_type)",
    """
    CREATE TABLE IF NOT EXISTS eval_comparisons (
      comparison_id TEXT PRIMARY KEY,
      baseline_run_id TEXT NOT NULL,
      candidate_run_id TEXT NOT NULL,
      status TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS judge_cache (
      cache_key TEXT PRIMARY KEY,
      judge_provider TEXT NOT NULL,
      judge_model TEXT NOT NULL,
      rubric_version TEXT NOT NULL,
      artifact_id TEXT NOT NULL,
      payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS judge_invocations (
      invocation_id TEXT PRIMARY KEY,
      task_id TEXT NOT NULL,
      run_id TEXT NOT NULL,
      case_id TEXT,
      cache_key TEXT NOT NULL,
      judge_provider TEXT NOT NULL,
      judge_model TEXT NOT NULL,
      prompt_hash TEXT NOT NULL,
      prompt_tokens INTEGER NOT NULL DEFAULT 0,
      completion_tokens INTEGER NOT NULL DEFAULT 0,
      total_tokens INTEGER NOT NULL DEFAULT 0,
      latency_ms INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL,
      error TEXT,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_judge_invocations_run_case ON judge_invocations(run_id, case_id, status)",
]


def apply_migrations(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    for statement in SCHEMA_STATEMENTS:
        cursor.execute(statement)
    # 兼容旧库：补 eval_runs.dataset_snapshot_id 列（已存在则忽略）。
    try:
        cursor.execute("ALTER TABLE eval_runs ADD COLUMN dataset_snapshot_id TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
