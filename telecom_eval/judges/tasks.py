from typing import Any, Literal

from pydantic import BaseModel, Field


class JudgeTask(BaseModel):
    task_id: str
    task_type: Literal[
        "claim_extraction",
        "evidence_alignment",
        "gold_alignment",
        "answer_correctness",
        "retrieval_content_support",
        "report_summary",
    ]
    case_id: str
    run_id: str
    subject_id: str
    input_refs: dict[str, str] = Field(default_factory=dict)
    normalized_input: dict[str, Any]
    input_hash: str
    rubric_version: str
    prompt_version: str
    judge_type: Literal["llm", "human", "rule"]
    judge_model_version: str
    output_schema: str
    cache_policy: Literal["reuse_if_key_matches", "no_reuse"] = "reuse_if_key_matches"
