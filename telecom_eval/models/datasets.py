"""Evaluation dataset lifecycle models (架构 5.1.1 / 5.1.2).

测试集是评估系统自管理的资产：可新建、导入、校验、确认、快照。每次正式评估记录
DatasetSnapshot 以保证可复现与公平对比。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DatasetStatus = Literal["draft", "active", "archived", "deleted"]
DatasetType = Literal["retrieval", "e2e", "mixed", "regression", "adversarial", "smoke"]
ImportRowStatus = Literal["confirmable", "draft", "rejected"]


class EvaluationDataset(BaseModel):
    dataset_id: str
    name: str
    description: str = ""
    scenario_id: str
    dataset_type: DatasetType = "mixed"
    owner: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: DatasetStatus = "draft"
    case_count: int = 0
    confirmed_case_count: int = 0
    created_at: str
    updated_at: str


class DatasetSnapshot(BaseModel):
    dataset_snapshot_id: str
    dataset_id: str
    dataset_version: str
    case_ids: list[str] = Field(default_factory=list)
    case_fingerprints: dict[str, str] = Field(default_factory=dict)
    confirmed_only: bool = True
    gold_policy: str = "confirmed_only"
    created_at: str


class DatasetImportPreviewRow(BaseModel):
    row_number: int
    status: ImportRowStatus
    normalized_question: str | None = None
    mapped_case: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duplicate_of_case_id: str | None = None


class DatasetImportJob(BaseModel):
    import_id: str
    dataset_id: str
    filename: str
    status: Literal["preview", "committed", "failed"] = "preview"
    total_rows: int = 0
    confirmable_rows: int = 0
    draft_rows: int = 0
    rejected_rows: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    completed_at: str | None = None
