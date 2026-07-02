from typing import Any

from pydantic import BaseModel, Field, model_validator

from telecom_eval.keys import build_match_keys


class Cost(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    amount: float = 0.0
    currency: str | None = None


class EvidenceItem(BaseModel):
    evidence_id: str
    observed_item_id: str | None = None
    content: str
    title: str | None = None
    source_id: str
    source_type: str
    rank: int
    role: str | None = None
    kind: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    match_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _populate_match_keys(self) -> "EvidenceItem":
        # 决策 5：未显式给出 match_keys 时，从稳定 provenance 自动派生。
        if not self.match_keys:
            self.match_keys = build_match_keys(
                evidence_id=self.evidence_id,
                observed_item_id=self.observed_item_id,
                provenance=self.provenance,
            )
        return self


class RetrievalInput(BaseModel):
    query: str
    scenario_id: str
    filters: dict[str, Any] = Field(default_factory=dict)
    top_k: int | None = None


class RetrievalWarning(BaseModel):
    """决策 6：检索侧 contextPack.issues（如 low_confidence）落到这里，
    它是检索告警、可辅助诊断，但不是 AnswerTrace.refusal。"""

    type: str
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class RetrievalOutput(BaseModel):
    normalized_query: str | None = None
    evidence_package: list[EvidenceItem]
    warnings: list[RetrievalWarning] = Field(default_factory=list)


class RetrievalTrace(BaseModel):
    trace_id: str
    case_id: str
    run_id: str
    subject_id: str
    input: RetrievalInput
    output: RetrievalOutput
    latency_ms: int
    cost: Cost = Field(default_factory=Cost)
    errors: list[str] = Field(default_factory=list)
    optional_debug_trace: dict[str, Any] = Field(default_factory=dict)


class AnswerInput(BaseModel):
    question: str
    scenario_id: str
    user_context: dict[str, Any] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)


class Refusal(BaseModel):
    refused: bool
    reason: str | None = None


class AnswerOutput(BaseModel):
    final_answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_package: list[EvidenceItem] = Field(default_factory=list)
    refusal: Refusal
    confidence: float | None = None


class AnswerTrace(BaseModel):
    trace_id: str
    case_id: str
    run_id: str
    subject_id: str
    input: AnswerInput
    output: AnswerOutput
    latency_ms: int
    cost: Cost = Field(default_factory=Cost)
    errors: list[str] = Field(default_factory=list)
    optional_debug_trace: dict[str, Any] = Field(default_factory=dict)
