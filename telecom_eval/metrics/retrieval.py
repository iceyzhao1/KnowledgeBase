"""Retrieval metrics based on evidence content semantics."""

from __future__ import annotations

from collections.abc import Callable

from telecom_eval.metrics.content_judgment import build_retrieval_content_judgment
from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import EvidenceItem

SegmentResolver = Callable[[str], str | None]


def _metrics(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None = None,
) -> dict[str, float]:
    judgment = build_retrieval_content_judgment(case, evidence_package, k=k, resolver=resolver)
    return judgment["metrics"]


def hit_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    k: int,
    *,
    resolver: SegmentResolver | None = None,
) -> float:
    return _metrics(case, evidence_package, k=k, resolver=resolver)["retrieval.hit_at_k"]


def recall_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    k: int,
    *,
    resolver: SegmentResolver | None = None,
) -> float:
    return _metrics(case, evidence_package, k=k, resolver=resolver)["retrieval.recall_at_k"]


def mrr(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    k: int,
    *,
    resolver: SegmentResolver | None = None,
) -> float:
    return _metrics(case, evidence_package, k=k, resolver=resolver)["retrieval.mrr"]


def evidence_coverage(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    resolver: SegmentResolver | None = None,
) -> float:
    return _metrics(
        case,
        evidence_package,
        k=len(evidence_package),
        resolver=resolver,
    )["retrieval.evidence_coverage"]


def latency_ms(trace_latency_ms: int | float) -> float:
    return float(trace_latency_ms)
