from typing import Literal

from pydantic import BaseModel, Field


class ComparisonRun(BaseModel):
    comparison_id: str
    baseline_run_id: str
    candidate_run_id: str
    comparison_type: Literal["before_after", "baseline_candidate", "ablation"]
    dataset_id: str
    metric_suite_id: str
    scenario_profile_id: str
    pairing_key: str = "case_id"
    created_at: str
    metadata: dict[str, str] = Field(default_factory=dict)
