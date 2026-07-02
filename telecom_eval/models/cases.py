from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Answerability(StrEnum):
    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    SHOULD_REFUSE = "should_refuse"


class GoldStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    DEPRECATED = "deprecated"


class RetrievalTarget(BaseModel):
    target_type: Literal["retrieval_unit", "evidence_span", "entity", "relation", "graph_path"]
    target_id: str
    source_gold_id: str | None = None
    index_version: str | None = None
    match_policy: Literal["exact", "overlap", "semantic_equivalent"] = "exact"
    raw_segment_ids: list[str] = Field(default_factory=list)
    document_key: str | None = None
    segment_index: int | None = None


class EvaluationCase(BaseModel):
    case_id: str
    dataset_id: str
    scenario_id: str
    source_type: Literal[
        "manual",
        "production_log",
        "generated_from_corpus",
        "regression",
        "adversarial",
        "imported",
    ]
    source_ref: str | None = None
    question: str
    filters: dict[str, Any] = Field(default_factory=dict)
    task_type: str
    expected_answer: str
    expected_key_points: list[str] = Field(default_factory=list)
    expected_evidence_contains: list[str] = Field(default_factory=list)
    expected_evidence: list[dict[str, Any]] = Field(default_factory=list)
    expected_entities: list[dict[str, Any]] = Field(default_factory=list)
    expected_relations: list[dict[str, Any]] = Field(default_factory=list)
    answerability: Answerability
    expected_retrieval_items: list[RetrievalTarget] = Field(default_factory=list)
    target_index_version: str | None = None
    gold_status: GoldStatus
    risk_level: Literal["low", "medium", "high", "critical"]
    difficulty: Literal["easy", "medium", "hard"] | None = None
    tags: list[str] = Field(default_factory=list)

    def is_formal(self) -> bool:
        return self.gold_status is GoldStatus.CONFIRMED
