"""Judge budget governance.

LLM 调用必须受控：默认禁用，开启后还要受最大调用次数、token 上限、参与 LLM 的样本数上限约束。
预算拒绝时，JudgeService 写入 status=skipped 的 JudgeResultArtifact，依赖它的指标变 inconclusive。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class JudgeBudget(BaseModel):
    allow_llm_judge: bool = False
    max_llm_calls: int = 0
    max_prompt_tokens: int = 0
    max_completion_tokens: int = 0
    max_total_tokens: int = 0
    max_cases_with_llm: int = 0


class JudgeBudgetUsage(BaseModel):
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cases_with_llm: set[str] = Field(default_factory=set)


def can_call_llm(
    budget: JudgeBudget, usage: JudgeBudgetUsage, case_id: str | None
) -> tuple[bool, str | None]:
    if not budget.allow_llm_judge:
        return False, "llm_judge_disabled"
    if budget.max_llm_calls and usage.llm_calls >= budget.max_llm_calls:
        return False, "max_llm_calls_exceeded"
    if budget.max_prompt_tokens and usage.prompt_tokens >= budget.max_prompt_tokens:
        return False, "max_prompt_tokens_exceeded"
    if budget.max_completion_tokens and usage.completion_tokens >= budget.max_completion_tokens:
        return False, "max_completion_tokens_exceeded"
    if budget.max_total_tokens and usage.total_tokens >= budget.max_total_tokens:
        return False, "max_total_tokens_exceeded"
    if case_id and budget.max_cases_with_llm:
        cases = set(usage.cases_with_llm)
        cases.add(case_id)
        if len(cases) > budget.max_cases_with_llm:
            return False, "max_cases_with_llm_exceeded"
    return True, None
