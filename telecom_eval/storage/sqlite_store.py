"""SQLite-backed evaluation store (v2 source of truth).

结构化列用于过滤，完整模型放进 ``payload_json``（``ensure_ascii=False`` 保留中文）。
一个 store 实例持有一条连接，``check_same_thread=False`` 以便 FastAPI 线程池访问。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from telecom_eval.models.artifacts import JudgeResultArtifact
from telecom_eval.models.cases import EvaluationCase, GoldStatus
from telecom_eval.models.diagnosis import DiagnosisResult
from telecom_eval.models.metrics import MetricResult
from telecom_eval.models.traces import AnswerTrace, RetrievalTrace
from telecom_eval.storage.migrations import apply_migrations


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(obj: Any) -> dict:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    raise TypeError(f"unsupported object for storage: {type(obj)!r}")


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


class SQLiteEvaluationStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        apply_migrations(self._conn)

    def close(self) -> None:
        self._conn.close()

    # ---- introspection -------------------------------------------------
    def list_table_names(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]

    # ---- cases ---------------------------------------------------------
    def save_case(self, case: EvaluationCase) -> None:
        now = _now()
        self._conn.execute(
            """
            INSERT INTO eval_cases
              (case_id, dataset_id, scenario_id, task_type, answerability,
               gold_status, risk_level, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
              dataset_id=excluded.dataset_id, scenario_id=excluded.scenario_id,
              task_type=excluded.task_type, answerability=excluded.answerability,
              gold_status=excluded.gold_status, risk_level=excluded.risk_level,
              payload_json=excluded.payload_json, updated_at=excluded.updated_at
            """,
            (
                case.case_id,
                case.dataset_id,
                case.scenario_id,
                case.task_type,
                str(case.answerability),
                str(case.gold_status),
                case.risk_level,
                case.model_dump_json(),
                now,
                now,
            ),
        )
        self._conn.commit()

    def delete_case(self, case_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM eval_cases WHERE case_id = ?", (case_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_cases(
        self, dataset_id: str | None = None, confirmed_only: bool = False
    ) -> list[EvaluationCase]:
        query = "SELECT payload_json FROM eval_cases"
        clauses: list[str] = []
        params: list[Any] = []
        if dataset_id is not None:
            clauses.append("dataset_id = ?")
            params.append(dataset_id)
        if confirmed_only:
            clauses.append("gold_status = ?")
            params.append(str(GoldStatus.CONFIRMED))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY case_id"
        rows = self._conn.execute(query, params).fetchall()
        return [EvaluationCase.model_validate_json(row["payload_json"]) for row in rows]

    def load_case(self, case_id: str) -> EvaluationCase | None:
        row = self._conn.execute(
            "SELECT payload_json FROM eval_cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return EvaluationCase.model_validate_json(row["payload_json"]) if row else None

    def delete_case(self, case_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM eval_cases WHERE case_id = ?", (case_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def dataset_case_stats(self, dataset_id: str | None = None) -> list[dict]:
        """从 eval_cases 聚合每个 dataset 的样本/确认数（含未登记 eval_datasets 的隐式集）。"""
        query = (
            "SELECT dataset_id, COUNT(*) AS case_count, "
            "SUM(CASE WHEN gold_status = ? THEN 1 ELSE 0 END) AS confirmed_count "
            "FROM eval_cases"
        )
        params: list[Any] = [str(GoldStatus.CONFIRMED)]
        if dataset_id is not None:
            query += " WHERE dataset_id = ?"
            params.append(dataset_id)
        query += " GROUP BY dataset_id ORDER BY dataset_id"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ---- datasets (explicit) ------------------------------------------
    def create_dataset(self, dataset) -> None:
        self._upsert_dataset(dataset)

    def update_dataset(self, dataset) -> None:
        self._upsert_dataset(dataset)

    def _upsert_dataset(self, dataset) -> None:
        from telecom_eval.models.datasets import EvaluationDataset

        d = dataset if isinstance(dataset, EvaluationDataset) else EvaluationDataset.model_validate(dataset)
        self._conn.execute(
            """
            INSERT INTO eval_datasets
              (dataset_id, name, scenario_id, dataset_type, status, case_count,
               confirmed_case_count, tags_json, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
              name=excluded.name, scenario_id=excluded.scenario_id,
              dataset_type=excluded.dataset_type, status=excluded.status,
              case_count=excluded.case_count, confirmed_case_count=excluded.confirmed_case_count,
              tags_json=excluded.tags_json, payload_json=excluded.payload_json,
              updated_at=excluded.updated_at
            """,
            (
                d.dataset_id,
                d.name,
                d.scenario_id,
                d.dataset_type,
                d.status,
                d.case_count,
                d.confirmed_case_count,
                _json(d.tags),
                d.model_dump_json(),
                d.created_at,
                d.updated_at,
            ),
        )
        self._conn.commit()

    def load_dataset(self, dataset_id: str):
        from telecom_eval.models.datasets import EvaluationDataset

        row = self._conn.execute(
            "SELECT payload_json FROM eval_datasets WHERE dataset_id = ? AND status <> ?",
            (dataset_id, "deleted"),
        ).fetchone()
        return EvaluationDataset.model_validate_json(row["payload_json"]) if row else None

    def list_datasets(self, status: str | None = None) -> list:
        from telecom_eval.models.datasets import EvaluationDataset

        query = "SELECT payload_json FROM eval_datasets"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        else:
            query += " WHERE status <> ?"
            params.append("deleted")
        query += " ORDER BY updated_at DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [EvaluationDataset.model_validate_json(r["payload_json"]) for r in rows]

    def refresh_dataset_counts(self, dataset_id: str) -> None:
        stats = self.dataset_case_stats(dataset_id)
        case_count = stats[0]["case_count"] if stats else 0
        confirmed = stats[0]["confirmed_count"] if stats else 0
        dataset = self.load_dataset(dataset_id)
        if dataset is None:
            return
        updated = dataset.model_copy(
            update={"case_count": case_count, "confirmed_case_count": confirmed, "updated_at": _now()}
        )
        self._upsert_dataset(updated)

    def delete_dataset(self, dataset_id: str) -> bool:
        run_rows = self._conn.execute(
            "SELECT run_id FROM eval_runs WHERE dataset_id = ?", (dataset_id,)
        ).fetchall()
        run_ids = [row["run_id"] for row in run_rows]
        row = self._conn.execute(
            "SELECT payload_json FROM eval_datasets WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        deleted_at = _now()
        with self._conn:
            for run_id in run_ids:
                self._soft_delete_run_without_commit(run_id, deleted_at=deleted_at)
            if row:
                payload = json.loads(row["payload_json"])
                payload["status"] = "deleted"
                payload["deleted_at"] = deleted_at
                self._conn.execute(
                    """
                    UPDATE eval_datasets
                    SET status = ?, payload_json = ?, updated_at = ?
                    WHERE dataset_id = ?
                    """,
                    ("deleted", _json(payload), deleted_at, dataset_id),
                )
        return bool(row) or bool(run_ids)

    # ---- dataset imports ----------------------------------------------
    def save_dataset_import(self, import_job) -> None:
        from telecom_eval.models.datasets import DatasetImportJob

        job = import_job if isinstance(import_job, DatasetImportJob) else DatasetImportJob.model_validate(import_job)
        self._conn.execute(
            """
            INSERT INTO eval_dataset_imports
              (import_id, dataset_id, filename, status, total_rows, valid_rows,
               draft_rows, rejected_rows, payload_json, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(import_id) DO UPDATE SET
              status=excluded.status, total_rows=excluded.total_rows,
              valid_rows=excluded.valid_rows, draft_rows=excluded.draft_rows,
              rejected_rows=excluded.rejected_rows, payload_json=excluded.payload_json,
              completed_at=excluded.completed_at
            """,
            (
                job.import_id,
                job.dataset_id,
                job.filename,
                job.status,
                job.total_rows,
                job.confirmable_rows,
                job.draft_rows,
                job.rejected_rows,
                job.model_dump_json(),
                job.created_at,
                job.completed_at,
            ),
        )
        self._conn.commit()

    def load_dataset_import(self, import_id: str):
        from telecom_eval.models.datasets import DatasetImportJob

        row = self._conn.execute(
            "SELECT payload_json FROM eval_dataset_imports WHERE import_id = ?", (import_id,)
        ).fetchone()
        return DatasetImportJob.model_validate_json(row["payload_json"]) if row else None

    # ---- dataset snapshots --------------------------------------------
    def create_dataset_snapshot(
        self, dataset_id: str, *, confirmed_only: bool = True, gold_policy: str = "confirmed_only"
    ):
        import hashlib
        import uuid

        from telecom_eval.models.datasets import DatasetSnapshot

        cases = self.list_cases(dataset_id=dataset_id, confirmed_only=confirmed_only)
        cases = sorted(cases, key=lambda c: c.case_id)
        fingerprints: dict[str, str] = {}
        for case in cases:
            basis = _json(
                {
                    "q": case.question,
                    "a": case.expected_answer,
                    "kp": case.expected_key_points,
                    "ev": case.expected_evidence,
                }
            )
            fingerprints[case.case_id] = "sha256:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()
        version_basis = _json([[cid, fingerprints[cid]] for cid in sorted(fingerprints)])
        dataset_version = "v-" + hashlib.sha256(version_basis.encode("utf-8")).hexdigest()[:16]
        snapshot = DatasetSnapshot(
            dataset_snapshot_id="snap_" + uuid.uuid4().hex[:12],
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            case_ids=[c.case_id for c in cases],
            case_fingerprints=fingerprints,
            confirmed_only=confirmed_only,
            gold_policy=gold_policy,
            created_at=_now(),
        )
        self._conn.execute(
            """
            INSERT INTO eval_dataset_snapshots
              (dataset_snapshot_id, dataset_id, dataset_version, case_ids_json,
               case_fingerprints_json, confirmed_only, gold_policy, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.dataset_snapshot_id,
                snapshot.dataset_id,
                snapshot.dataset_version,
                _json(snapshot.case_ids),
                _json(snapshot.case_fingerprints),
                1 if confirmed_only else 0,
                gold_policy,
                snapshot.model_dump_json(),
                snapshot.created_at,
            ),
        )
        self._conn.commit()
        return snapshot

    def load_dataset_snapshot(self, dataset_snapshot_id: str):
        from telecom_eval.models.datasets import DatasetSnapshot

        row = self._conn.execute(
            "SELECT payload_json FROM eval_dataset_snapshots WHERE dataset_snapshot_id = ?",
            (dataset_snapshot_id,),
        ).fetchone()
        return DatasetSnapshot.model_validate_json(row["payload_json"]) if row else None

    # ---- runs ----------------------------------------------------------
    def save_run(self, run: Any) -> None:
        data = _dump(run)
        run_id = data["run_id"]
        eval_type = data.get("eval_type") or data.get("run_type") or "mixed"
        status = data.get("status", "created")
        allow_llm = 1 if data.get("allow_llm_judge") else 0
        created_at = data.get("created_at") or _now()
        self._conn.execute(
            """
            INSERT INTO eval_runs
              (run_id, subject_id, dataset_id, dataset_snapshot_id, eval_type, status,
               allow_llm_judge, created_at, started_at, completed_at, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              subject_id=excluded.subject_id, dataset_id=excluded.dataset_id,
              dataset_snapshot_id=excluded.dataset_snapshot_id,
              eval_type=excluded.eval_type, status=excluded.status,
              allow_llm_judge=excluded.allow_llm_judge,
              started_at=excluded.started_at, completed_at=excluded.completed_at,
              payload_json=excluded.payload_json
            """,
            (
                run_id,
                data.get("subject_id", ""),
                data.get("dataset_id", ""),
                data.get("dataset_snapshot_id"),
                eval_type,
                status,
                allow_llm,
                created_at,
                data.get("started_at"),
                data.get("completed_at"),
                _json(data),
            ),
        )
        self._conn.commit()

    def update_run_status(self, run_id: str, status: str, **timestamps: str | None) -> None:
        sets = ["status = ?"]
        params: list[Any] = [status]
        for column in ("started_at", "completed_at"):
            if column in timestamps:
                sets.append(f"{column} = ?")
                params.append(timestamps[column])
        params.append(run_id)
        self._conn.execute(
            f"UPDATE eval_runs SET {', '.join(sets)} WHERE run_id = ?", params
        )
        # 同步 payload_json 里的 status
        row = self._conn.execute(
            "SELECT payload_json FROM eval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row:
            payload = json.loads(row["payload_json"])
            payload["status"] = status
            for column in ("started_at", "completed_at"):
                if column in timestamps:
                    payload[column] = timestamps[column]
            self._conn.execute(
                "UPDATE eval_runs SET payload_json = ? WHERE run_id = ?",
                (_json(payload), run_id),
            )
        self._conn.commit()

    def list_runs(
        self, dataset_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        query = (
            "SELECT run_id, subject_id, dataset_id, eval_type, status, allow_llm_judge,"
            " created_at, started_at, completed_at FROM eval_runs"
        )
        params: list[Any] = []
        if dataset_id is not None:
            query += " WHERE dataset_id = ? AND status <> ?"
            params.extend([dataset_id, "deleted"])
        else:
            query += " WHERE status <> ?"
            params.append("deleted")
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def load_run_payload(self, run_id: str) -> dict:
        row = self._conn.execute(
            "SELECT payload_json FROM eval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"run not found: {run_id}")
        return json.loads(row["payload_json"])

    def delete_run(self, run_id: str) -> bool:
        with self._conn:
            return self._soft_delete_run_without_commit(run_id)

    def _soft_delete_run_without_commit(self, run_id: str, *, deleted_at: str | None = None) -> bool:
        row = self._conn.execute(
            "SELECT payload_json FROM eval_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return False
        deleted_at = deleted_at or _now()
        payload = json.loads(row["payload_json"])
        payload["status"] = "deleted"
        payload["deleted_at"] = deleted_at
        self._conn.execute(
            """
            UPDATE eval_runs
            SET status = ?, payload_json = ?, completed_at = COALESCE(completed_at, ?)
            WHERE run_id = ?
            """,
            ("deleted", _json(payload), deleted_at, run_id),
        )
        return True

    # ---- traces --------------------------------------------------------
    def save_trace(self, trace: RetrievalTrace | AnswerTrace) -> None:
        trace_type = "answer" if isinstance(trace, AnswerTrace) else "retrieval"
        self._conn.execute(
            """
            INSERT INTO eval_traces
              (trace_id, run_id, case_id, subject_id, trace_type, latency_ms,
               has_error, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
              payload_json=excluded.payload_json, latency_ms=excluded.latency_ms,
              has_error=excluded.has_error
            """,
            (
                trace.trace_id,
                trace.run_id,
                trace.case_id,
                trace.subject_id,
                trace_type,
                trace.latency_ms,
                1 if trace.errors else 0,
                trace.model_dump_json(),
                _now(),
            ),
        )
        self._conn.commit()

    def get_trace(self, trace_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT trace_id, run_id, case_id, trace_type, payload_json "
            "FROM eval_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def list_case_traces(self, run_id: str, case_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT trace_id, trace_type, latency_ms, has_error, payload_json "
            "FROM eval_traces WHERE run_id = ? AND case_id = ? ORDER BY trace_type",
            (run_id, case_id),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    # ---- metrics -------------------------------------------------------
    def save_metric_result(self, metric: MetricResult) -> None:
        metric_result_id = f"{metric.run_id}:{metric.case_id or '_agg'}:{metric.metric_id}"
        self._conn.execute(
            """
            INSERT INTO eval_metrics
              (metric_result_id, run_id, case_id, subject_id, metric_id, level,
               status, value_json, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(metric_result_id) DO UPDATE SET
              status=excluded.status, value_json=excluded.value_json,
              payload_json=excluded.payload_json
            """,
            (
                metric_result_id,
                metric.run_id,
                metric.case_id,
                metric.subject_id,
                metric.metric_id,
                metric.level,
                metric.status,
                _json(metric.value),
                metric.model_dump_json(),
                _now(),
            ),
        )
        self._conn.commit()

    def list_run_metrics(self, run_id: str, case_id: str | None = None) -> list[MetricResult]:
        if case_id is None:
            rows = self._conn.execute(
                "SELECT payload_json FROM eval_metrics WHERE run_id = ? ORDER BY metric_id",
                (run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT payload_json FROM eval_metrics WHERE run_id = ? AND case_id = ? "
                "ORDER BY metric_id",
                (run_id, case_id),
            ).fetchall()
        return [MetricResult.model_validate_json(row["payload_json"]) for row in rows]

    # ---- artifacts -----------------------------------------------------
    def save_artifact(
        self,
        artifact: BaseModel,
        *,
        artifact_type: str,
        run_id: str,
        case_id: str | None,
        cache_key: str | None,
    ) -> None:
        data = _dump(artifact)
        artifact_id = data.get("artifact_id") or f"{run_id}:{case_id}:{artifact_type}"
        self._conn.execute(
            """
            INSERT INTO eval_artifacts
              (artifact_id, run_id, case_id, artifact_type, cache_key, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
              payload_json=excluded.payload_json, cache_key=excluded.cache_key
            """,
            (artifact_id, run_id, case_id, artifact_type, cache_key, _json(data), _now()),
        )
        self._conn.commit()

    def get_artifact(self, artifact_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT artifact_id, run_id, case_id, artifact_type, cache_key, payload_json "
            "FROM eval_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def list_case_artifacts(self, run_id: str, case_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT artifact_id, artifact_type, cache_key, payload_json "
            "FROM eval_artifacts WHERE run_id = ? AND case_id = ? ORDER BY artifact_type",
            (run_id, case_id),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    # ---- diagnoses -----------------------------------------------------
    def save_diagnosis(self, diagnosis: DiagnosisResult) -> None:
        self._conn.execute(
            """
            INSERT INTO eval_diagnoses
              (diagnosis_id, run_id, case_id, failure_type, severity, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(diagnosis_id) DO UPDATE SET
              failure_type=excluded.failure_type, severity=excluded.severity,
              payload_json=excluded.payload_json
            """,
            (
                diagnosis.diagnosis_id,
                diagnosis.run_id,
                diagnosis.case_id,
                diagnosis.failure_type,
                diagnosis.severity,
                diagnosis.model_dump_json(),
                _now(),
            ),
        )
        self._conn.commit()

    def list_case_diagnoses(self, run_id: str, case_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT diagnosis_id, failure_type, severity, payload_json "
            "FROM eval_diagnoses WHERE run_id = ? AND case_id = ?",
            (run_id, case_id),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def list_run_diagnoses(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT diagnosis_id, case_id, failure_type, severity, payload_json "
            "FROM eval_diagnoses WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    # ---- reports -------------------------------------------------------
    def save_report(
        self, *, report_id: str, run_id: str, report_type: str, payload: dict, markdown: str
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO eval_reports
              (report_id, run_id, report_type, payload_json, markdown, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO UPDATE SET
              payload_json=excluded.payload_json, markdown=excluded.markdown
            """,
            (report_id, run_id, report_type, _json(payload), markdown, _now()),
        )
        self._conn.commit()

    def load_report(self, run_id: str, report_type: str = "run") -> dict | None:
        row = self._conn.execute(
            "SELECT report_id, run_id, report_type, payload_json, markdown "
            "FROM eval_reports WHERE run_id = ? AND report_type = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id, report_type),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    # ---- comparisons ---------------------------------------------------
    def save_comparison(
        self,
        *,
        comparison_id: str,
        baseline_run_id: str,
        candidate_run_id: str,
        status: str,
        payload: dict,
        completed_at: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO eval_comparisons
              (comparison_id, baseline_run_id, candidate_run_id, status,
               payload_json, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comparison_id) DO UPDATE SET
              status=excluded.status, payload_json=excluded.payload_json,
              completed_at=excluded.completed_at
            """,
            (
                comparison_id,
                baseline_run_id,
                candidate_run_id,
                status,
                _json(payload),
                _now(),
                completed_at,
            ),
        )
        self._conn.commit()

    def load_comparison(self, comparison_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT comparison_id, baseline_run_id, candidate_run_id, status, payload_json, "
            "completed_at FROM eval_comparisons WHERE comparison_id = ?",
            (comparison_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    # ---- judge cache & audit ------------------------------------------
    def get_judge_cache(self, cache_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT cache_key, judge_provider, judge_model, rubric_version, artifact_id, "
            "payload_json FROM judge_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def put_judge_cache(self, cache_key: str, artifact: JudgeResultArtifact) -> None:
        self._conn.execute(
            """
            INSERT INTO judge_cache
              (cache_key, judge_provider, judge_model, rubric_version, artifact_id,
               payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
              payload_json=excluded.payload_json, artifact_id=excluded.artifact_id
            """,
            (
                cache_key,
                artifact.judge_type,
                artifact.judge_model_version,
                artifact.rubric_version,
                artifact.artifact_id,
                artifact.model_dump_json(),
                _now(),
            ),
        )
        self._conn.commit()

    def save_judge_invocation(self, invocation: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO judge_invocations
              (invocation_id, task_id, run_id, case_id, cache_key, judge_provider,
               judge_model, prompt_hash, prompt_tokens, completion_tokens, total_tokens,
               latency_ms, status, error, attempt, max_attempts, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation["invocation_id"],
                invocation["task_id"],
                invocation["run_id"],
                invocation.get("case_id"),
                invocation["cache_key"],
                invocation["judge_provider"],
                invocation["judge_model"],
                invocation["prompt_hash"],
                invocation.get("prompt_tokens", 0),
                invocation.get("completion_tokens", 0),
                invocation.get("total_tokens", 0),
                invocation.get("latency_ms", 0),
                invocation["status"],
                invocation.get("error"),
                invocation.get("attempt", 1),
                invocation.get("max_attempts", 1),
                invocation.get("created_at") or _now(),
            ),
        )
        self._conn.commit()

    def list_judge_invocations(
        self, run_id: str | None = None, case_id: str | None = None, limit: int = 200
    ) -> list[dict]:
        query = "SELECT * FROM judge_invocations"
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if case_id is not None:
            clauses.append("case_id = ?")
            params.append(case_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
