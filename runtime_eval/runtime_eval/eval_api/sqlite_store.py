"""SQLite-backed artifact store — the single storage backend.

This is the one and only store implementation (the JSON file backend was
removed). ``store.Store`` is an alias of this class and ``store.create_store``
returns it; callers stay backend-agnostic.

Design: one table per artifact kind, keyed by its id, with the full pydantic
model serialised into a ``payload`` JSON column (cheapest, loss-free migration
path off the JSON files). A few hot columns (``project_id`` / ``suite_id`` /
``created_at``) are lifted out for cross-row queries (board / trends). Reports
stay on disk (Markdown/HTML); only their directory is managed here.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..shared.models import (
    Document,
    EvalRun,
    GoldRecord,
    Project,
    ResponseSet,
    RetrievalRun,
    RetrievalSet,
    RunSummary,
    TestSuite,
)
from .config import ApiConfig

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    created_at TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    project_id  TEXT,
    created_at  TEXT,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suites (
    suite_id   TEXT PRIMARY KEY,
    project_id TEXT,
    backend    TEXT,
    created_at TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS responses (
    suite_id   TEXT PRIMARY KEY,
    project_id TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    project_id TEXT,
    suite_id   TEXT,
    created_at TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_sets (
    suite_id   TEXT PRIMARY KEY,
    project_id TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_runs (
    run_id     TEXT PRIMARY KEY,
    project_id TEXT,
    suite_id   TEXT,
    created_at TEXT,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gold (
    fingerprint TEXT PRIMARY KEY,
    project_id  TEXT,
    updated_at  TEXT,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_summaries (
    run_id     TEXT PRIMARY KEY,
    project_id TEXT,
    suite_id   TEXT,
    layer      TEXT,
    kind       TEXT,
    created_at TEXT,
    status     TEXT,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS ix_runs_suite ON runs(suite_id);
CREATE INDEX IF NOT EXISTS ix_retrieval_runs_suite ON retrieval_runs(suite_id);
CREATE INDEX IF NOT EXISTS ix_gold_project ON gold(project_id);
CREATE INDEX IF NOT EXISTS ix_run_summaries_suite ON run_summaries(suite_id);
CREATE INDEX IF NOT EXISTS ix_run_summaries_project ON run_summaries(project_id);
"""


class SqliteStore:
    """SQLite implementation of the Store interface (see module docstring)."""

    def __init__(self, config: ApiConfig):
        self.config = config
        self.root = config.workspace_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = config.database_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # check_same_thread=False: FastAPI runs sync handlers on a threadpool; we
        # serialise access ourselves via _lock so a single connection is safe.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # --- low-level helpers ---
    def _upsert(self, table: str, key_col: str, key: str, cols: dict[str, object]) -> None:
        all_cols = [key_col, *cols.keys()]
        placeholders = ", ".join("?" for _ in all_cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols)
        sql = (
            f"INSERT INTO {table} ({', '.join(all_cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({key_col}) DO UPDATE SET {updates}"
        )
        with self._lock:
            self._conn.execute(sql, [key, *cols.values()])
            self._conn.commit()

    def _fetch(self, table: str, key_col: str, key: str) -> str | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT payload FROM {table} WHERE {key_col} = ?", (key,)
            )
            row = cur.fetchone()
        return row["payload"] if row else None

    def _fetch_col(self, table: str, key_col: str, key: str, col: str) -> str | None:
        with self._lock:
            cur = self._conn.execute(
                f"SELECT {col} FROM {table} WHERE {key_col} = ?", (key,)
            )
            row = cur.fetchone()
        return row[col] if row else None

    # --- paths (reports stay on disk) ---
    def project_dir(self, project_id: str) -> Path:
        return self.root / project_id

    def reports_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "reports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # --- id -> project_id resolution ---
    def project_of_suite(self, suite_id: str) -> str | None:
        return self._fetch_col("suites", "suite_id", suite_id, "project_id")

    def project_of_run(self, run_id: str) -> str | None:
        return self._fetch_col("runs", "run_id", run_id, "project_id")

    def project_of_retrieval_run(self, run_id: str) -> str | None:
        return self._fetch_col("retrieval_runs", "run_id", run_id, "project_id")

    # --- Project ---
    def list_projects(self) -> list[Project]:
        with self._lock:
            cur = self._conn.execute("SELECT payload FROM projects ORDER BY project_id")
            rows = cur.fetchall()
        return [Project.model_validate_json(r["payload"]) for r in rows]

    def save_project(self, project: Project) -> Project:
        self._upsert(
            "projects",
            "project_id",
            project.project_id,
            {"created_at": project.created_at, "payload": project.model_dump_json()},
        )
        return project

    def get_project(self, project_id: str) -> Project | None:
        payload = self._fetch("projects", "project_id", project_id)
        return Project.model_validate_json(payload) if payload else None

    def delete_project(self, project_id: str) -> bool:
        """删项目并级联清掉其下全部数据（所有表都带 project_id 列）。"""
        tables = [
            "documents", "suites", "responses", "runs",
            "retrieval_sets", "retrieval_runs", "gold",
            "run_summaries", "projects",
        ]
        with self._lock:
            existed = self._conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is not None
            for t in tables:
                self._conn.execute(
                    f"DELETE FROM {t} WHERE project_id = ?", (project_id,)
                )
            self._conn.commit()
        return existed

    # --- Document ---
    def save_document(self, doc: Document) -> Path:
        self._upsert(
            "documents",
            "document_id",
            doc.document_id,
            {
                "project_id": doc.project_id,
                "created_at": doc.created_at,
                "payload": doc.model_dump_json(),
            },
        )
        return self.db_path

    def get_document(self, project_id: str, document_id: str) -> Document | None:
        payload = self._fetch("documents", "document_id", document_id)
        if not payload:
            return None
        doc = Document.model_validate_json(payload)
        return doc if doc.project_id == project_id else None

    def list_documents(self, project_id: str) -> list[Document]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT payload FROM documents WHERE project_id = ? ORDER BY document_id",
                (project_id,),
            )
            rows = cur.fetchall()
        return [Document.model_validate_json(r["payload"]) for r in rows]

    # --- TestSuite ---
    def save_suite(self, suite: TestSuite) -> Path:
        pid = suite.project_id or "default"
        self._upsert(
            "suites",
            "suite_id",
            suite.suite_id,
            {
                "project_id": pid,
                "backend": suite.backend,
                "created_at": suite.created_at,
                "payload": suite.model_dump_json(),
            },
        )
        return self.db_path

    def get_suite(self, suite_id: str) -> TestSuite | None:
        payload = self._fetch("suites", "suite_id", suite_id)
        return TestSuite.model_validate_json(payload) if payload else None

    def list_suites(self, project_id: str | None = None) -> list[TestSuite]:
        """列出测试集（按创建时间倒序，最新在前）。前端测试集下拉/看板用。"""
        with self._lock:
            if project_id is None:
                cur = self._conn.execute(
                    "SELECT payload FROM suites ORDER BY created_at DESC, suite_id"
                )
            else:
                cur = self._conn.execute(
                    "SELECT payload FROM suites WHERE project_id = ? "
                    "ORDER BY created_at DESC, suite_id",
                    (project_id,),
                )
            rows = cur.fetchall()
        return [TestSuite.model_validate_json(r["payload"]) for r in rows]

    # --- ResponseSet ---
    def save_responses(self, responses: ResponseSet) -> Path:
        pid = self.project_of_suite(responses.suite_id) or "default"
        self._upsert(
            "responses",
            "suite_id",
            responses.suite_id,
            {"project_id": pid, "payload": responses.model_dump_json()},
        )
        return self.db_path

    def get_responses(self, suite_id: str) -> ResponseSet:
        payload = self._fetch("responses", "suite_id", suite_id)
        if not payload:
            return ResponseSet(suite_id=suite_id)
        return ResponseSet.model_validate_json(payload)

    # --- EvalRun ---
    def save_run(self, run: EvalRun) -> Path:
        pid = run.project_id or self.project_of_suite(run.suite_id) or "default"
        self._upsert(
            "runs",
            "run_id",
            run.run_id,
            {
                "project_id": pid,
                "suite_id": run.suite_id,
                "created_at": run.created_at,
                "payload": run.model_dump_json(),
            },
        )
        return self.db_path

    def get_run(self, run_id: str) -> EvalRun | None:
        payload = self._fetch("runs", "run_id", run_id)
        return EvalRun.model_validate_json(payload) if payload else None

    # --- RetrievalSet ---
    def save_retrieval_set(self, retrieval: RetrievalSet) -> Path:
        pid = self.project_of_suite(retrieval.suite_id) or "default"
        self._upsert(
            "retrieval_sets",
            "suite_id",
            retrieval.suite_id,
            {"project_id": pid, "payload": retrieval.model_dump_json()},
        )
        return self.db_path

    def get_retrieval_set(self, suite_id: str) -> RetrievalSet:
        payload = self._fetch("retrieval_sets", "suite_id", suite_id)
        if not payload:
            return RetrievalSet(suite_id=suite_id)
        return RetrievalSet.model_validate_json(payload)

    # --- RetrievalRun ---
    def working_retrieval_run_id(self, suite_id: str) -> str:
        """Deterministic run id for the per-question (incremental) retrieval flow.

        Matches the JSON store so the per-question「评估」endpoint upserts case
        results into one stable run id (progress survives restarts).
        """

        return f"rrun_{suite_id}"

    def save_retrieval_run(self, run: RetrievalRun) -> Path:
        pid = run.project_id or self.project_of_suite(run.suite_id) or "default"
        self._upsert(
            "retrieval_runs",
            "run_id",
            run.run_id,
            {
                "project_id": pid,
                "suite_id": run.suite_id,
                "created_at": run.created_at,
                "payload": run.model_dump_json(),
            },
        )
        return self.db_path

    def get_retrieval_run(self, run_id: str) -> RetrievalRun | None:
        payload = self._fetch("retrieval_runs", "run_id", run_id)
        return RetrievalRun.model_validate_json(payload) if payload else None

    # --- GoldRecord (黄金库 A4，按指纹复用) ---
    def save_gold(self, gold: GoldRecord) -> Path:
        self._upsert(
            "gold",
            "fingerprint",
            gold.fingerprint,
            {
                "project_id": gold.project_id,
                "updated_at": gold.updated_at,
                "payload": gold.model_dump_json(),
            },
        )
        return self.db_path

    def get_gold(self, fingerprint: str) -> GoldRecord | None:
        payload = self._fetch("gold", "fingerprint", fingerprint)
        return GoldRecord.model_validate_json(payload) if payload else None

    def match_gold(self, question: str) -> GoldRecord | None:
        return self.get_gold(GoldRecord.make_fingerprint(question))

    def delete_gold(self, fingerprint: str) -> bool:
        """删一条黄金；返回是否真的删到（不存在则 False）。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM gold WHERE fingerprint = ?", (fingerprint,)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def list_gold(self, project_id: str | None = None) -> list[GoldRecord]:
        with self._lock:
            if project_id is None:
                cur = self._conn.execute(
                    "SELECT payload FROM gold ORDER BY fingerprint"
                )
            else:
                cur = self._conn.execute(
                    "SELECT payload FROM gold WHERE project_id = ? ORDER BY fingerprint",
                    (project_id,),
                )
            rows = cur.fetchall()
        return [GoldRecord.model_validate_json(r["payload"]) for r in rows]

    # --- RunSummary (评测档案：每次 run 的指标落库) ---
    def save_run_summary(self, summary: RunSummary) -> RunSummary:
        """落一条 run 指标快照。逐题流程用稳定 run_id 反复 upsert（覆盖式更新）。"""
        pid = summary.project_id or self.project_of_suite(summary.suite_id) or "default"
        summary.project_id = pid
        self._upsert(
            "run_summaries",
            "run_id",
            summary.run_id,
            {
                "project_id": pid,
                "suite_id": summary.suite_id,
                "layer": summary.layer,
                "kind": summary.kind,
                "created_at": summary.created_at,
                "status": summary.status,
                "payload": summary.model_dump_json(),
            },
        )
        return summary

    def get_run_summary(self, run_id: str) -> RunSummary | None:
        payload = self._fetch("run_summaries", "run_id", run_id)
        return RunSummary.model_validate_json(payload) if payload else None

    def list_run_summaries(
        self, *, project_id: str | None = None, suite_id: str | None = None
    ) -> list[RunSummary]:
        """列出 run 指标快照（最新在前）。project_id / suite_id 二选一过滤或全量。"""
        with self._lock:
            if suite_id is not None:
                cur = self._conn.execute(
                    "SELECT payload FROM run_summaries WHERE suite_id = ? "
                    "ORDER BY created_at DESC, run_id",
                    (suite_id,),
                )
            elif project_id is not None:
                cur = self._conn.execute(
                    "SELECT payload FROM run_summaries WHERE project_id = ? "
                    "ORDER BY created_at DESC, run_id",
                    (project_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT payload FROM run_summaries ORDER BY created_at DESC, run_id"
                )
            rows = cur.fetchall()
        return [RunSummary.model_validate_json(r["payload"]) for r in rows]

    def latest_run_summary(self, suite_id: str, layer: str | None = None) -> RunSummary | None:
        """某测试集最近一次 run 快照（可按 layer 过滤），看板/状态聚合用。"""
        with self._lock:
            if layer is None:
                cur = self._conn.execute(
                    "SELECT payload FROM run_summaries WHERE suite_id = ? "
                    "ORDER BY created_at DESC, run_id LIMIT 1",
                    (suite_id,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT payload FROM run_summaries WHERE suite_id = ? AND layer = ? "
                    "ORDER BY created_at DESC, run_id LIMIT 1",
                    (suite_id, layer),
                )
            row = cur.fetchone()
        return RunSummary.model_validate_json(row["payload"]) if row else None
