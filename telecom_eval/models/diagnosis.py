from typing import Any, Literal

from pydantic import BaseModel, Field


# 完整失败类型词表（架构第 9.1 节）。注意：第一版诊断引擎只产出其中 7 类
# （决策 8：evaluation_material_issue / retrieval_miss / retrieval_ranking_issue /
# unsupported_claim / refusal_error / comparison_regression / ambiguous），
# 其余类型保留为词表，但不代表已实现。
FailureType = Literal[
    "evaluation_material_issue",
    "knowledge_gap",
    "retrieval_call_error",
    "retrieval_miss",
    "retrieval_ranking_issue",
    "evidence_noise_or_conflict",
    "generation_missing_key_point",
    "unsupported_claim",
    "citation_error",
    "refusal_error",
    "comparison_regression",
    "ambiguous",
]


class DiagnosisResult(BaseModel):
    diagnosis_id: str
    case_id: str
    run_id: str
    subject_id: str
    failure_type: FailureType
    severity: Literal["low", "medium", "high", "critical"]
    evidence: list[str] = Field(default_factory=list)
    suggested_action: str
    confidence: float
    related_metrics: list[str] = Field(default_factory=list)
    related_artifacts: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
