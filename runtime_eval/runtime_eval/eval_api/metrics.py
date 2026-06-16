"""Aggregate an EvalRun into report-ready metrics.

Metrics produced:
- 按问题类型区分的准确率（accuracy / pass-rate / 平均分）
- 被测 Agent 的 token 消耗（合计 / 均值）
- 查询时长（均值 / p50 / p95）
- 框架自身 LLM 成本（生成 + 裁判 token）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from ..shared.models import EvalRun, QuestionType, TestSuite, TokenUsage, Verdict


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass
class LatencyStats:
    count: int = 0
    mean_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    max_ms: float | None = None


@dataclass
class TypeMetrics:
    question_type: str
    total: int = 0
    pass_count: int = 0  # score >= threshold
    correct: int = 0
    partial: int = 0
    incorrect: int = 0
    missing: int = 0
    avg_score: float = 0.0

    @property
    def accuracy(self) -> float:
        """准确率 = 通过数 / 总数（含未答，未答计为不通过）。"""
        return self.pass_count / self.total if self.total else 0.0


@dataclass
class ReportMetrics:
    suite_id: str
    agent_name: str
    backend: str
    pass_threshold: float
    run_id: str = ""
    total_cases: int = 0
    answered_cases: int = 0
    overall_accuracy: float = 0.0
    overall_avg_score: float = 0.0
    by_type: list[TypeMetrics] = field(default_factory=list)
    latency: LatencyStats = field(default_factory=LatencyStats)
    agent_tokens: TokenUsage = field(default_factory=TokenUsage)
    agent_tokens_avg: float = 0.0
    framework_judge_tokens: TokenUsage = field(default_factory=TokenUsage)
    framework_generation_tokens: TokenUsage = field(default_factory=TokenUsage)


def compute_metrics(run: EvalRun, suite: TestSuite | None = None) -> ReportMetrics:
    threshold = run.pass_threshold
    metrics = ReportMetrics(
        suite_id=run.suite_id,
        run_id=run.run_id,
        agent_name=run.agent_name,
        backend=run.backend,
        pass_threshold=threshold,
        total_cases=len(run.results),
    )

    buckets: dict[str, TypeMetrics] = {
        t.value: TypeMetrics(question_type=t.value) for t in QuestionType.all()
    }
    scores_by_type: dict[str, list[float]] = {t.value: [] for t in QuestionType.all()}

    latencies: list[float] = []
    agent_tokens = TokenUsage()
    judge_tokens = TokenUsage()
    all_scores: list[float] = []

    for r in run.results:
        bucket = buckets[r.question_type.value]
        bucket.total += 1
        all_scores.append(r.score)
        scores_by_type[r.question_type.value].append(r.score)

        if r.score >= threshold:
            bucket.pass_count += 1
        if r.verdict == Verdict.CORRECT:
            bucket.correct += 1
        elif r.verdict == Verdict.PARTIAL:
            bucket.partial += 1
        elif r.verdict == Verdict.INCORRECT:
            bucket.incorrect += 1
        elif r.verdict == Verdict.MISSING:
            bucket.missing += 1

        if r.verdict != Verdict.MISSING:
            metrics.answered_cases += 1
        if r.latency_ms is not None:
            latencies.append(r.latency_ms)
        agent_tokens = agent_tokens + r.agent_token_usage
        judge_tokens = judge_tokens + r.judge_token_usage

    for t_value, bucket in buckets.items():
        scores = scores_by_type[t_value]
        bucket.avg_score = round(mean(scores), 4) if scores else 0.0

    pass_total = sum(b.pass_count for b in buckets.values())
    metrics.by_type = [b for b in buckets.values() if b.total > 0]
    metrics.overall_accuracy = pass_total / metrics.total_cases if metrics.total_cases else 0.0
    metrics.overall_avg_score = round(mean(all_scores), 4) if all_scores else 0.0

    metrics.latency = LatencyStats(
        count=len(latencies),
        mean_ms=round(mean(latencies), 2) if latencies else None,
        p50_ms=_percentile(latencies, 0.5),
        p95_ms=_percentile(latencies, 0.95),
        max_ms=max(latencies) if latencies else None,
    )
    metrics.agent_tokens = agent_tokens
    metrics.agent_tokens_avg = (
        round(agent_tokens.total_tokens / metrics.answered_cases, 2)
        if metrics.answered_cases
        else 0.0
    )
    metrics.framework_judge_tokens = judge_tokens
    if suite is not None:
        metrics.framework_generation_tokens = suite.generation_usage

    return metrics
