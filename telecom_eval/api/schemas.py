"""API request/response schemas (v2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from telecom_eval.judges.budget import JudgeBudget

DEFAULT_METRIC_SUITES = [
    "retrieval_basic_suite",
    "e2e_basic_suite",
    "evaluation_efficiency_suite",
]


class CreateRunRequest(BaseModel):
    dataset_id: str
    dataset_snapshot_id: str | None = None
    subject_id: str
    subject_search_path: str | None = None
    eval_type: Literal["retrieval", "e2e", "mixed"] = "mixed"
    metric_suite_ids: list[str] = Field(default_factory=lambda: list(DEFAULT_METRIC_SUITES))
    confirmed_only: bool = True
    top_k: int = 10
    allow_llm_judge: bool = False
    judge_budget: JudgeBudget = Field(default_factory=JudgeBudget)


class CreateComparisonRequest(BaseModel):
    baseline_run_id: str
    candidate_run_id: str
    metric_id: str = "retrieval.recall_at_k"
    comparison_type: Literal["before_after", "baseline_candidate", "ablation"] = "before_after"


class CreateDatasetRequest(BaseModel):
    name: str
    scenario_id: str
    dataset_type: Literal["retrieval", "e2e", "mixed", "regression", "adversarial", "smoke"] = "mixed"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None


class UpdateDatasetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    status: Literal["draft", "active", "archived", "deleted"] | None = None
    dataset_type: str | None = None
    owner: str | None = None


class AddCaseRequest(BaseModel):
    case: dict[str, Any]


class DatasetImportPreviewRequest(BaseModel):
    filename: str
    content: str


class CommitDatasetImportRequest(BaseModel):
    import_id: str
    duplicate_policy: Literal["skip", "import_as_draft", "copy", "update_draft"] = "skip"
    confirm_complete_rows: bool = False


class CreateSnapshotRequest(BaseModel):
    confirmed_only: bool = True
    gold_policy: str = "confirmed_only"


class RetrievalSearchRequest(BaseModel):
    query: str
    domain: str | None = None
    top_k: int = 10


class UpdateCaseRequest(BaseModel):
    """编辑样本（标准答案 / 黄金短语 / 关联原文证据等）。"""

    question: str | None = None
    expected_answer: str | None = None
    expected_key_points: list[str] | None = None
    expected_evidence: list[dict[str, Any]] | None = None
    task_type: str | None = None
    answerability: str | None = None
    risk_level: str | None = None
    tags: list[str] | None = None
    gold_status: str | None = None
    allow_confirmed_edit: bool = False


class EvaluationRunSummary(BaseModel):
    run_id: str
    subject_id: str
    dataset_id: str
    eval_type: str
    status: str
    allow_llm_judge: bool = False
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class RunReportViewModel(BaseModel):
    run: dict[str, Any]
    metrics: list[dict[str, Any]]
    metric_cases: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    metric_summary: dict[str, Any]
    failures: list[dict[str, Any]]
    judge_usage: dict[str, Any]
    case_count: int
    markdown: str = ""


class CaseDebugViewModel(BaseModel):
    case: dict[str, Any]
    retrieval_trace: dict[str, Any] | None = None
    answer_trace: dict[str, Any] | None = None
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis: dict[str, Any] | None = None
    evidence_alignment: list[dict[str, Any]] = Field(default_factory=list)
    retrieval_content_judgment: dict[str, Any] | None = None
    expected_evidence: list[dict[str, Any]] = Field(default_factory=list)
    judge_invocations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class ComparisonReportViewModel(BaseModel):
    comparison_id: str
    baseline_run_id: str
    candidate_run_id: str
    status: str
    summary: dict[str, Any]
    delta_rows: list[dict[str, Any]]
    regressed_cases: list[dict[str, Any]]
    newly_passed: list[str]
    newly_failed: list[str]
    baseline_snapshot_id: str | None = None
    candidate_snapshot_id: str | None = None
    snapshot_match: bool = False
    snapshot_warning: str | None = None
