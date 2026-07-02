from typing import Any, Literal

from pydantic import BaseModel, Field


class EvaluationArtifact(BaseModel):
    artifact_id: str
    artifact_type: str
    case_id: str
    run_id: str
    subject_id: str
    trace_id: str | None = None
    inputs_hash: str
    payload: dict[str, Any]
    lineage: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AtomicClaim(BaseModel):
    claim_id: str
    text: str
    claim_type: str = "fact"
    importance: Literal["required", "optional", "background"] = "required"
    source_span: dict[str, int] | None = None


class EvidenceAlignment(BaseModel):
    claim_id: str
    evidence_id: str | None = None
    support_label: Literal["supported", "contradicted", "not_supported"]
    rationale: str = ""


class GoldAlignment(BaseModel):
    gold_id: str
    claim_ids: list[str] = Field(default_factory=list)
    coverage_label: Literal["covered", "partially_covered", "missed"]
    contradiction_label: Literal["none", "contradicted"] = "none"
    rationale: str = ""


class JudgeResultArtifact(BaseModel):
    """决策 3：Judge 结果必须落成真实可复用 Artifact，而不只是占位名字。

    MockJudge 产出它、JudgeCache 复用它，供多个指标/诊断/报告共享，避免重复判分。"""

    artifact_id: str
    artifact_type: str = "judge_result"
    judge_task_id: str
    judge_cache_key: str
    case_id: str
    run_id: str
    subject_id: str
    judge_type: Literal["llm", "human", "rule"]
    judge_model_version: str
    rubric_version: str
    prompt_version: str
    output_schema: str
    result: dict[str, Any]
    usage: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "error", "inconclusive", "needs_review", "skipped"] = "ok"
    reason: str | None = None  # 决策：预算拒绝时记录跳过原因（如 llm_judge_disabled）
    errors: list[str] = Field(default_factory=list)
    created_at: str
