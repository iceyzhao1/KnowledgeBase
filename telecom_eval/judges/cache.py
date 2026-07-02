"""Judge cache.

JudgeCacheKey 携带完整 lineage（架构 5.9）。只要 key 不变，JudgeResultArtifact 可被多个
指标/诊断/报告复用；任何影响判断依据的变化都会改变 key 从而失效旧结果。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from telecom_eval.judges.tasks import JudgeTask


class JudgeCacheKey(BaseModel, frozen=True):
    task_type: str
    case_id: str
    subject_id: str
    input_hash: str
    answer_hash: str | None = None
    evidence_hash: str | None = None
    gold_hash: str | None = None
    rubric_version: str
    prompt_version: str
    judge_model_version: str
    judge_type: str
    output_schema: str


def compute_cache_key(task: JudgeTask) -> JudgeCacheKey:
    normalized = task.normalized_input or {}
    return JudgeCacheKey(
        task_type=task.task_type,
        case_id=task.case_id,
        subject_id=task.subject_id,
        input_hash=task.input_hash,
        answer_hash=normalized.get("answer_hash"),
        evidence_hash=normalized.get("evidence_hash"),
        gold_hash=normalized.get("gold_hash"),
        rubric_version=task.rubric_version,
        prompt_version=task.prompt_version,
        judge_model_version=task.judge_model_version,
        judge_type=task.judge_type,
        output_schema=task.output_schema,
    )


class InMemoryJudgeCache:
    def __init__(self) -> None:
        self._items: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0

    def _key(self, key: JudgeCacheKey) -> str:
        return key.model_dump_json()

    def get(self, key: JudgeCacheKey) -> Any | None:
        serialized = self._key(key)
        if serialized in self._items:
            self.hits += 1
            return self._items[serialized]
        self.misses += 1
        return None

    def set(self, key: JudgeCacheKey, value: Any) -> None:
        self._items[self._key(key)] = value
