"""Deterministic mock judge for tests.

决策 3：MockJudge 不调真实 LLM，但必须产出真实的 JudgeResultArtifact，并经过
JudgeCache 复用，证明「一次判分、多处复用」链路成立。
"""

from __future__ import annotations

from datetime import datetime, timezone

from telecom_eval.judges.cache import InMemoryJudgeCache, compute_cache_key
from telecom_eval.judges.tasks import JudgeTask
from telecom_eval.models.artifacts import JudgeResultArtifact


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MockJudge:
    model_version = "mock_judge_v1"

    def __init__(self) -> None:
        self.call_count = 0

    def judge(self, task: JudgeTask) -> JudgeResultArtifact:
        self.call_count += 1
        cache_key = compute_cache_key(task)
        return JudgeResultArtifact(
            artifact_id=f"art_judge_{task.task_id}",
            judge_task_id=task.task_id,
            judge_cache_key=cache_key.model_dump_json(),
            case_id=task.case_id,
            run_id=task.run_id,
            subject_id=task.subject_id,
            judge_type=task.judge_type,
            judge_model_version=task.judge_model_version,
            rubric_version=task.rubric_version,
            prompt_version=task.prompt_version,
            output_schema=task.output_schema,
            result={"status": "ok", "task_type": task.task_type, "echo": task.normalized_input},
            usage={"prompt_tokens": 0, "completion_tokens": 0, "calls": 1},
            status="ok",
            created_at=_now(),
        )


def run_judge_through_cache(
    task: JudgeTask, judge: MockJudge, cache: InMemoryJudgeCache
) -> JudgeResultArtifact:
    """先查缓存，命中即复用 JudgeResultArtifact；未命中才调用 judge 并写回缓存。"""
    cache_key = compute_cache_key(task)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    artifact = judge.judge(task)
    cache.set(cache_key, artifact)
    return artifact
