"""DatasetService — 测试集新建 / 导入预览 / 导入提交 / 去重 / 快照（架构 5.1.1 / 5.1.2）。

硬规则：
- 导入预览**绝不**写入 case。
- 已 confirmed 的 case **不会**被导入静默覆盖。
- 提交时只有 gold 完整且 confirm_complete_rows=True 才会写成 confirmed，其余进 draft。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from telecom_eval.models.cases import Answerability, EvaluationCase, GoldStatus
from telecom_eval.models.datasets import (
    DatasetImportJob,
    DatasetImportPreviewRow,
    EvaluationDataset,
)

_LIST_SEP = re.compile(r"[；;|\n]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return base or "dataset"


def _normalize_question(q: str) -> str:
    return "".join((q or "").split()).lower()


def _as_list(value: Any) -> list | None:
    """把 list 原样返回；CSV 里的字符串按分隔符切；None/空 → None。非法类型返回 'INVALID'。"""
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parts = [p.strip() for p in _LIST_SEP.split(value) if p.strip()]
        return parts
    return "INVALID"  # type: ignore[return-value]


class DatasetService:
    def __init__(self, store):
        self.store = store

    # ---- dataset CRUD -------------------------------------------------
    def create_dataset(
        self,
        *,
        name: str,
        scenario_id: str,
        dataset_type: str = "mixed",
        description: str = "",
        tags: list[str] | None = None,
        owner: str | None = None,
    ) -> EvaluationDataset:
        dataset_id = f"{_slug(name)}_{uuid.uuid4().hex[:6]}"
        now = _now()
        dataset = EvaluationDataset(
            dataset_id=dataset_id,
            name=name,
            description=description,
            scenario_id=scenario_id,
            dataset_type=dataset_type,
            owner=owner,
            tags=tags or [],
            status="draft",
            case_count=0,
            confirmed_case_count=0,
            created_at=now,
            updated_at=now,
        )
        self.store.create_dataset(dataset)
        return dataset

    def get_dataset(self, dataset_id: str) -> EvaluationDataset | None:
        dataset = self.store.load_dataset(dataset_id)
        if dataset is not None:
            stats = self.store.dataset_case_stats(dataset_id)
            if stats:
                dataset = dataset.model_copy(
                    update={
                        "case_count": stats[0]["case_count"],
                        "confirmed_case_count": stats[0]["confirmed_count"],
                    }
                )
            return dataset
        # 隐式集：仅在 eval_cases 出现、未登记 eval_datasets。
        stats = self.store.dataset_case_stats(dataset_id)
        if not stats:
            return None
        now = _now()
        return EvaluationDataset(
            dataset_id=dataset_id,
            name=dataset_id,
            scenario_id="",
            dataset_type="mixed",
            status="active",
            case_count=stats[0]["case_count"],
            confirmed_case_count=stats[0]["confirmed_count"],
            created_at=now,
            updated_at=now,
        )

    def list_datasets(self, status: str | None = None) -> list[EvaluationDataset]:
        explicit = {d.dataset_id: d for d in self.store.list_datasets(status=status)}
        result: list[EvaluationDataset] = []
        for dataset_id, dataset in explicit.items():
            stats = self.store.dataset_case_stats(dataset_id)
            if stats:
                dataset = dataset.model_copy(
                    update={
                        "case_count": stats[0]["case_count"],
                        "confirmed_case_count": stats[0]["confirmed_count"],
                    }
                )
            result.append(dataset)
        # 合并隐式集（仅当不过滤 status，或 status==active 时展示）。
        if status in (None, "active"):
            for row in self.store.dataset_case_stats():
                if row["dataset_id"] in explicit:
                    continue
                now = _now()
                result.append(
                    EvaluationDataset(
                        dataset_id=row["dataset_id"],
                        name=row["dataset_id"],
                        scenario_id="",
                        dataset_type="mixed",
                        status="active",
                        case_count=row["case_count"],
                        confirmed_case_count=row["confirmed_count"],
                        created_at=now,
                        updated_at=now,
                    )
                )
        return result

    def update_dataset(self, dataset_id: str, patch: dict[str, Any]) -> EvaluationDataset:
        dataset = self.store.load_dataset(dataset_id)
        if dataset is None:
            # 隐式集（仅 eval_cases 出现）：先登记成正式 eval_datasets 行，再更新。
            implicit = self.get_dataset(dataset_id)
            if implicit is None:
                raise KeyError(dataset_id)
            self.store.create_dataset(implicit)
            dataset = implicit
        allowed = {k: v for k, v in patch.items() if k in {"name", "description", "tags", "status", "dataset_type", "owner"} and v is not None}
        updated = dataset.model_copy(update={**allowed, "updated_at": _now()})
        self.store.update_dataset(updated)
        return self.get_dataset(dataset_id)  # type: ignore[return-value]

    def add_case(self, dataset_id: str, payload: dict[str, Any]) -> EvaluationCase:
        data = dict(payload)
        data.setdefault("dataset_id", dataset_id)
        dataset = self.store.load_dataset(dataset_id)
        if dataset is not None:
            data.setdefault("scenario_id", dataset.scenario_id)
        data.setdefault("case_id", f"{dataset_id}_{uuid.uuid4().hex[:8]}")
        data.setdefault("source_type", "manual")
        case = EvaluationCase.model_validate(data)
        self.store.save_case(case)
        self.store.refresh_dataset_counts(dataset_id)
        return case

    def update_case(
        self, case_id: str, patch: dict[str, Any], *, allow_confirmed_edit: bool = False
    ) -> EvaluationCase:
        case = self.store.load_case(case_id)
        if case is None:
            raise KeyError(case_id)

        gold_fields = {"expected_answer", "expected_key_points", "expected_evidence_contains", "expected_evidence"}
        touches_gold = any(k in patch for k in gold_fields)
        # 不静默改写已确认 case 的 gold（架构 5.1.1）。
        if case.gold_status is GoldStatus.CONFIRMED and touches_gold and not allow_confirmed_edit:
            raise PermissionError(
                "该样本已 confirmed，修改 gold 会动摇历史评估基础；"
                "请确认后带 allow_confirmed_edit=true 再提交（或先降为 draft）。"
            )

        allowed = {
            k: v
            for k, v in patch.items()
            if k
            in {
                "question",
                "expected_answer",
                "expected_key_points",
                "expected_evidence",
                "task_type",
                "answerability",
                "risk_level",
                "tags",
                "gold_status",
            }
        }
        updated = case.model_copy(update=allowed)
        # 重新校验（触发 EvaluationCase 的校验器，如 key_points 非空）。
        updated = EvaluationCase.model_validate(updated.model_dump())
        self.store.save_case(updated)
        self.store.refresh_dataset_counts(updated.dataset_id)
        return updated

    def confirm_dataset_drafts(self, dataset_id: str) -> dict[str, int]:
        cases = self.store.list_cases(dataset_id=dataset_id)
        confirmed = 0
        skipped = 0
        for case in cases:
            if case.gold_status is GoldStatus.DRAFT:
                updated = case.model_copy(update={"gold_status": GoldStatus.CONFIRMED})
                self.store.save_case(EvaluationCase.model_validate(updated.model_dump()))
                confirmed += 1
            else:
                skipped += 1
        self.store.refresh_dataset_counts(dataset_id)
        return {"confirmed": confirmed, "skipped": skipped, "total": len(cases)}

    def delete_case(self, case_id: str) -> str:
        """从测试集删除样本；返回所属 dataset_id。历史 run 已按快照固化，不受影响。"""
        case = self.store.load_case(case_id)
        if case is None:
            raise KeyError(case_id)
        dataset_id = case.dataset_id
        self.store.delete_case(case_id)
        self.store.refresh_dataset_counts(dataset_id)
        return dataset_id

    def delete_case(self, case_id: str) -> bool:
        case = self.store.load_case(case_id)
        if case is None:
            return False
        dataset_id = case.dataset_id
        deleted = self.store.delete_case(case_id)
        if deleted:
            self.store.refresh_dataset_counts(dataset_id)
        return deleted

    # ---- import parsing ----------------------------------------------
    def _parse(self, filename: str, content: str) -> list[dict[str, Any]]:
        name = (filename or "").lower()
        if name.endswith(".jsonl"):
            return [json.loads(line) for line in content.splitlines() if line.strip()]
        if name.endswith(".json"):
            data = json.loads(content)
            if isinstance(data, dict):
                data = data.get("cases") or data.get("rows") or []
            return list(data)
        if name.endswith(".csv"):
            reader = csv.DictReader(io.StringIO(content))
            return [dict(row) for row in reader]
        if name.endswith(".xlsx"):
            raise ValueError("xlsx 暂未启用：请改用 .jsonl / .json / .csv（需要 openpyxl 时再扩展）。")
        # 兜底按 jsonl 尝试
        return [json.loads(line) for line in content.splitlines() if line.strip()]

    # ---- preview (never mutates) -------------------------------------
    def preview_import(self, dataset_id: str, *, filename: str, content: str) -> DatasetImportJob:
        try:
            raw_rows = self._parse(filename, content)
        except Exception as exc:  # 解析整体失败：返回一个全 rejected 的预览
            job = DatasetImportJob(
                import_id="imp_" + uuid.uuid4().hex[:12],
                dataset_id=dataset_id,
                filename=filename,
                status="preview",
                total_rows=0,
                rows=[],
                created_at=_now(),
            )
            job.rows = [
                DatasetImportPreviewRow(
                    row_number=0, status="rejected", errors=[f"文件解析失败：{exc}"]
                ).model_dump()
            ]
            job.total_rows = 1
            job.rejected_rows = 1
            self.store.save_dataset_import(job)
            return job

        dataset = self.store.load_dataset(dataset_id)
        scenario_id = dataset.scenario_id if dataset else "cloud_core"
        existing = self.store.list_cases(dataset_id=dataset_id)
        by_question = {_normalize_question(c.question): c for c in existing}
        by_case_id = {c.case_id: c for c in existing}

        preview_rows: list[DatasetImportPreviewRow] = []
        for idx, raw in enumerate(raw_rows, start=1):
            preview_rows.append(self._validate_row(idx, raw, scenario_id, by_question, by_case_id))

        confirmable = sum(1 for r in preview_rows if r.status == "confirmable")
        draft = sum(1 for r in preview_rows if r.status == "draft")
        rejected = sum(1 for r in preview_rows if r.status == "rejected")

        job = DatasetImportJob(
            import_id="imp_" + uuid.uuid4().hex[:12],
            dataset_id=dataset_id,
            filename=filename,
            status="preview",
            total_rows=len(preview_rows),
            confirmable_rows=confirmable,
            draft_rows=draft,
            rejected_rows=rejected,
            rows=[r.model_dump() for r in preview_rows],
            created_at=_now(),
        )
        self.store.save_dataset_import(job)
        return job

    def _validate_row(
        self, idx: int, raw: dict, scenario_id: str, by_question: dict, by_case_id: dict
    ) -> DatasetImportPreviewRow:
        errors: list[str] = []
        warnings: list[str] = []

        question = str(raw.get("question") or "").strip()
        if not question:
            return DatasetImportPreviewRow(row_number=idx, status="rejected", errors=["question 为空"])

        key_points = _as_list(raw.get("expected_key_points"))
        evidence = _as_list(raw.get("expected_evidence"))
        evidence_contains = _as_list(raw.get("expected_evidence_contains"))
        if key_points == "INVALID":
            errors.append("expected_key_points 类型非法（应为列表）")
        if evidence == "INVALID":
            errors.append("expected_evidence 类型非法（应为列表）")
        if evidence_contains == "INVALID":
            errors.append("expected_evidence_contains 类型非法（应为列表）")
        if errors:
            return DatasetImportPreviewRow(row_number=idx, status="rejected", errors=errors, normalized_question=_normalize_question(question))

        # evidence 字符串列表 → [{evidence_id: x}]
        norm_evidence: list[dict] = []
        for item in (evidence or []):
            if isinstance(item, dict):
                norm_evidence.append(item)
            else:
                norm_evidence.append({"evidence_id": str(item)})

        normalized = _normalize_question(question)
        dup_case = by_case_id.get(str(raw.get("case_id"))) or by_question.get(normalized)
        duplicate_of = dup_case.case_id if dup_case else None

        mapped = {
            "case_id": str(raw.get("case_id") or f"imp_{hashlib.sha1(normalized.encode('utf-8')).hexdigest()[:10]}"),
            "scenario_id": str(raw.get("scenario_id") or scenario_id),
            "source_type": "imported",
            "source_ref": raw.get("source_ref"),
            "question": question,
            "task_type": str(raw.get("task_type") or "imported"),
            "expected_answer": str(raw.get("expected_answer") or ""),
            "expected_key_points": key_points or [],
            "expected_evidence_contains": [str(s) for s in (evidence_contains or [])],
            "expected_evidence": norm_evidence,
            "expected_entities": _as_list(raw.get("expected_entities")) or [],
            "tags": _as_list(raw.get("tags")) or [],
            "difficulty": raw.get("difficulty"),
            "risk_level": str(raw.get("risk_level") or "medium"),
            "answerability": str(raw.get("answerability") or "answerable"),
        }

        # 重复 confirmed → 拒绝（不可静默覆盖）
        if dup_case is not None and dup_case.gold_status is GoldStatus.CONFIRMED:
            return DatasetImportPreviewRow(
                row_number=idx,
                status="rejected",
                normalized_question=normalized,
                mapped_case=mapped,
                errors=["与已确认 case 重复，默认不导入（不可覆盖 confirmed）"],
                duplicate_of_case_id=duplicate_of,
            )

        has_evidence = bool(mapped["expected_evidence"]) or bool(mapped["expected_evidence_contains"])
        complete = bool(mapped["expected_answer"]) and has_evidence
        if not complete:
            warnings.append("缺少完整 gold（answer + evidence/evidence_contains 至少一项），将作为草稿导入")
        if duplicate_of:
            warnings.append("与已有草稿 case 重复，提交时按去重策略处理")

        return DatasetImportPreviewRow(
            row_number=idx,
            status="confirmable" if complete else "draft",
            normalized_question=normalized,
            mapped_case=mapped,
            warnings=warnings,
            duplicate_of_case_id=duplicate_of,
        )

    # ---- commit -------------------------------------------------------
    def commit_import(
        self,
        dataset_id: str,
        *,
        import_id: str,
        duplicate_policy: str = "skip",
        confirm_complete_rows: bool = False,
    ) -> dict[str, Any]:
        job = self.store.load_dataset_import(import_id)
        if job is None:
            raise KeyError(import_id)

        inserted_confirmed = 0
        inserted_draft = 0
        skipped = 0

        for row in job.rows:
            status = row.get("status")
            mapped = row.get("mapped_case")
            if status == "rejected" or not mapped:
                skipped += 1
                continue

            duplicate_of = row.get("duplicate_of_case_id")
            target_case_id = mapped["case_id"]

            if duplicate_of:
                dup = self.store.load_case(duplicate_of)
                # 重复 confirmed 永不覆盖
                if dup is not None and dup.gold_status is GoldStatus.CONFIRMED:
                    skipped += 1
                    continue
                if duplicate_policy == "skip":
                    skipped += 1
                    continue
                if duplicate_policy == "update_draft":
                    target_case_id = duplicate_of
                elif duplicate_policy in ("copy", "import_as_draft"):
                    target_case_id = f"{mapped['case_id']}_copy_{uuid.uuid4().hex[:6]}"

            make_confirmed = status == "confirmable" and confirm_complete_rows and not (
                duplicate_of and duplicate_policy == "import_as_draft"
            )
            gold_status = GoldStatus.CONFIRMED if make_confirmed else GoldStatus.DRAFT

            case = EvaluationCase(
                case_id=target_case_id,
                dataset_id=dataset_id,
                scenario_id=mapped["scenario_id"],
                source_type="imported",
                source_ref=mapped.get("source_ref"),
                question=mapped["question"],
                task_type=mapped["task_type"],
                expected_answer=mapped["expected_answer"] or "",
                expected_key_points=mapped["expected_key_points"] or [],
                expected_evidence_contains=mapped.get("expected_evidence_contains") or [],
                expected_evidence=mapped["expected_evidence"] or [],
                expected_entities=mapped.get("expected_entities", []),
                answerability=Answerability(mapped["answerability"]),
                gold_status=gold_status,
                risk_level=mapped["risk_level"],
                difficulty=mapped.get("difficulty"),
                tags=mapped.get("tags", []),
            )
            self.store.save_case(case)
            if gold_status is GoldStatus.CONFIRMED:
                inserted_confirmed += 1
            else:
                inserted_draft += 1

        self.store.refresh_dataset_counts(dataset_id)
        committed = job.model_copy(update={"status": "committed", "completed_at": _now()})
        self.store.save_dataset_import(committed)

        return {
            "import_id": import_id,
            "inserted_confirmed": inserted_confirmed,
            "inserted_draft": inserted_draft,
            "skipped": skipped,
            "rejected": job.rejected_rows,
        }

    # ---- snapshot -----------------------------------------------------
    def create_snapshot(self, dataset_id: str, *, confirmed_only: bool = True, gold_policy: str = "confirmed_only"):
        return self.store.create_dataset_snapshot(
            dataset_id, confirmed_only=confirmed_only, gold_policy=gold_policy
        )
