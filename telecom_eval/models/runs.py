from typing import Literal

from pydantic import BaseModel, Field


class EvaluationRun(BaseModel):
    run_id: str
    subject_id: str
    subject_version: str
    dataset_id: str
    metric_suite_id: str
    scenario_profile_id: str
    run_type: Literal["retrieval", "e2e"]
    case_ids: list[str]
    created_at: str
    metadata: dict[str, str] = Field(default_factory=dict)
