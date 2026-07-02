"""Metric Planner.

评估前置规划器（架构 5.7）。它读取 registry 的 required_inputs / required_artifacts /
judge_requirements（决策 4），优先用 Trace、复用已有 Artifact，缺失才创建构建任务与 typed
JudgeTask（决策 3）。它不调 LLM、不调被测系统、不生成报告。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from telecom_eval.judges.tasks import JudgeTask
from telecom_eval.metrics.registry import (
    ARTIFACT_DEPENDENCIES,
    MetricRegistry,
    default_registry,
)


class MetricExecutionPlan(BaseModel):
    plan_id: str
    run_id: str
    case_id: str
    subject_id: str
    metric_tasks: list[dict] = Field(default_factory=list)
    artifact_tasks: list[dict] = Field(default_factory=list)
    judge_tasks: list[JudgeTask] = Field(default_factory=list)
    skipped_metrics: list[dict] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)


class MetricPlanner:
    def __init__(self, registry: MetricRegistry | None = None):
        self.registry = registry or default_registry()

    def plan(
        self,
        *,
        run_id: str,
        case_id: str,
        subject_id: str,
        metric_ids: list[str],
        available_inputs: set[str],
        available_artifacts: set[str],
    ) -> MetricExecutionPlan:
        # 决策 10：复制后再增删派生 Artifact 名，调用方集合不被改写。
        available = set(available_artifacts)
        plan = MetricExecutionPlan(
            plan_id=f"plan_{run_id}_{case_id}_{subject_id}",
            run_id=run_id,
            case_id=case_id,
            subject_id=subject_id,
        )

        for metric_id in metric_ids:
            if not self.registry.has(metric_id):
                plan.skipped_metrics.append({"metric_id": metric_id, "reason": "unknown metric"})
                continue

            spec = self.registry.get(metric_id)
            missing = [inp for inp in spec.required_inputs if inp not in available_inputs]
            if missing:
                plan.skipped_metrics.append(
                    {"metric_id": metric_id, "reason": f"missing {','.join(missing)}"}
                )
                plan.missing_inputs.extend(missing)
                continue

            blocked = False
            for artifact_name in spec.required_artifacts:
                if not self._ensure_artifact(
                    artifact_name,
                    metric_id=metric_id,
                    plan=plan,
                    available=available,
                    run_id=run_id,
                    case_id=case_id,
                    subject_id=subject_id,
                ):
                    blocked = True
            if blocked:
                plan.skipped_metrics.append(
                    {"metric_id": metric_id, "reason": "unsatisfiable artifact dependency"}
                )
                continue

            plan.metric_tasks.append({"metric_id": metric_id})

        plan.missing_inputs = sorted(set(plan.missing_inputs))
        return plan

    def _ensure_artifact(
        self,
        artifact_name: str,
        *,
        metric_id: str,
        plan: MetricExecutionPlan,
        available: set[str],
        run_id: str,
        case_id: str,
        subject_id: str,
    ) -> bool:
        if artifact_name in available:
            return True

        dependency = ARTIFACT_DEPENDENCIES.get(artifact_name)
        if dependency is None:
            plan.missing_inputs.append(artifact_name)
            return False

        for upstream in dependency.depends_on:
            if not self._ensure_artifact(
                upstream,
                metric_id=metric_id,
                plan=plan,
                available=available,
                run_id=run_id,
                case_id=case_id,
                subject_id=subject_id,
            ):
                return False

        plan.artifact_tasks.append(
            {
                "task_type": dependency.builder_task,
                "artifact": artifact_name,
                "reason": f"required_by:{metric_id}",
            }
        )
        plan.judge_tasks.append(
            JudgeTask(
                task_id=f"jt_{run_id}_{case_id}_{dependency.judge_task}",
                task_type=dependency.judge_task,
                case_id=case_id,
                run_id=run_id,
                subject_id=subject_id,
                normalized_input={},
                input_hash="",
                rubric_version="v1",
                prompt_version="v1",
                judge_type="llm",
                judge_model_version="unbound",
                output_schema="v1",
            )
        )
        available.add(artifact_name)
        return True
