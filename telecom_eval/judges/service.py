"""JudgeService — the single gate for real LLM judging.

流程（方案 5.1）：收 JudgeTask → 建 cache_key → 查 SQLite judge_cache → 查预算 →
仅在允许时调用 provider → 存 JudgeResultArtifact + judge_cache + judge_invocation 审计 →
返回 JudgeResultArtifact（或受控的 skipped 结果）。除本服务外，任何层不得直接调用 provider。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from telecom_eval.judges.budget import JudgeBudget, JudgeBudgetUsage, can_call_llm
from telecom_eval.judges.prompts import build_judge_prompt
from telecom_eval.judges.providers.base import JudgeProvider, ProviderResult, ProviderUsage
from telecom_eval.judges.scheduler import _sha, build_cache_key
from telecom_eval.judges.tasks import JudgeTask
from telecom_eval.models.artifacts import JudgeResultArtifact

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {"raw_text": text}


class JudgeService:
    def __init__(self, *, store, provider: JudgeProvider, model: str | None = None):
        self.store = store
        self.provider = provider
        self.model = model or getattr(provider, "name", "unknown")

    def run(
        self,
        task: JudgeTask,
        *,
        budget: JudgeBudget,
        usage: JudgeBudgetUsage | None = None,
    ) -> JudgeResultArtifact:
        usage = usage if usage is not None else JudgeBudgetUsage()
        cache_key = build_cache_key(task, self.provider.name, self.model)

        cached = self.store.get_judge_cache(cache_key)
        if cached is not None:
            return JudgeResultArtifact.model_validate(cached["payload"])

        allowed, reason = can_call_llm(budget, usage, task.case_id)
        if not allowed:
            artifact = self._build_artifact(
                task, cache_key, status="skipped", result={}, usage=ProviderUsage(), reason=reason
            )
            self._audit(task, cache_key, status="skipped", usage=ProviderUsage(), error=reason)
            return artifact

        system, user = build_judge_prompt(task)
        try:
            provider_result = self.provider.invoke(system=system, user=user)
        except Exception as exc:  # provider 失败：记审计、返回 error 工件，但不污染缓存
            artifact = self._build_artifact(
                task, cache_key, status="error", result={"error": str(exc)}, usage=ProviderUsage()
            )
            logger.exception(
                "judge provider failed provider=%s model=%s run_id=%s case_id=%s task_id=%s",
                self.provider.name,
                self.model,
                task.run_id,
                task.case_id,
                task.task_id,
            )
            self._audit(task, cache_key, status="error", usage=ProviderUsage(), error=str(exc))
            return artifact

        artifact = self._build_artifact(
            task,
            cache_key,
            status="ok",
            result=_parse_text(provider_result.text),
            usage=provider_result.usage,
        )
        self.store.put_judge_cache(cache_key, artifact)

        usage.llm_calls += 1
        usage.prompt_tokens += provider_result.usage.prompt_tokens
        usage.completion_tokens += provider_result.usage.completion_tokens
        usage.total_tokens += provider_result.usage.total_tokens
        if task.case_id:
            usage.cases_with_llm.add(task.case_id)

        self._audit(task, cache_key, status="ok", usage=provider_result.usage, latency_ms=provider_result.latency_ms)
        return artifact

    # ------------------------------------------------------------------
    def _build_artifact(
        self,
        task: JudgeTask,
        cache_key: str,
        *,
        status: str,
        result: dict,
        usage: ProviderUsage,
        reason: str | None = None,
    ) -> JudgeResultArtifact:
        return JudgeResultArtifact(
            artifact_id=f"art_judge_{cache_key[:16]}_{task.task_id}",
            judge_task_id=task.task_id,
            judge_cache_key=cache_key,
            case_id=task.case_id,
            run_id=task.run_id,
            subject_id=task.subject_id,
            judge_type=task.judge_type,
            judge_model_version=self.model,
            rubric_version=task.rubric_version,
            prompt_version=task.prompt_version,
            output_schema=task.output_schema,
            result=result,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            },
            status=status,
            reason=reason,
            created_at=_now(),
        )

    def _audit(
        self,
        task: JudgeTask,
        cache_key: str,
        *,
        status: str,
        usage: ProviderUsage,
        error: str | None = None,
        latency_ms: int = 0,
    ) -> None:
        system, user = build_judge_prompt(task)
        self.store.save_judge_invocation(
            {
                "invocation_id": uuid.uuid4().hex,
                "task_id": task.task_id,
                "run_id": task.run_id,
                "case_id": task.case_id,
                "cache_key": cache_key,
                "judge_provider": self.provider.name,
                "judge_model": self.model,
                "prompt_hash": _sha(system, user),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "error": error,
                "created_at": _now(),
            }
        )
