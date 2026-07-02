"""Evaluation efficiency metrics (架构 11.2 evaluation_efficiency_suite / 决策 1).

关注评估过程本身的效率与 LLM 调用控制：延迟、成本、Judge 缓存命中率、LLM 调用次数。
全部规则可算，不调用 LLM。
"""

from __future__ import annotations

from typing import Any, Iterable


def aggregate_latency_ms(values: Iterable[float | int]) -> dict[str, float]:
    values = [float(v) for v in values]
    if not values:
        return {"count": 0, "total_ms": 0.0, "avg_ms": 0.0, "max_ms": 0.0}
    total = sum(values)
    return {
        "count": len(values),
        "total_ms": total,
        "avg_ms": total / len(values),
        "max_ms": max(values),
    }


def _get(item: Any, field: str, default: Any = 0) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def aggregate_cost(costs: Iterable[Any]) -> dict[str, float]:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    amount = 0.0
    for cost in costs:
        prompt_tokens += int(_get(cost, "prompt_tokens", 0))
        completion_tokens += int(_get(cost, "completion_tokens", 0))
        total_tokens += int(_get(cost, "total_tokens", 0))
        amount += float(_get(cost, "amount", 0.0))
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "amount": amount,
    }


def judge_cache_hit_rate(*, hits: int, misses: int) -> float:
    total = hits + misses
    if total == 0:
        return 0.0
    return hits / total


def llm_call_count(judge_results: Iterable[Any]) -> int:
    return sum(1 for result in judge_results if _get(result, "judge_type", None) == "llm")
