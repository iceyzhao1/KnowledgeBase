from typing import Any, Literal

from pydantic import BaseModel, Field


MetricStatus = Literal["ok", "not_applicable", "missing_inputs", "inconclusive", "error"]


class MetricResult(BaseModel):
    metric_id: str
    case_id: str | None = None
    run_id: str
    subject_id: str
    level: Literal["retrieval", "e2e", "comparison", "efficiency"]
    value: Any
    status: MetricStatus
    details: dict[str, Any] = Field(default_factory=dict)
    used_inputs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
