"""Metric registry.

决策 4：registry 不是死代码。每个首版指标声明 ``required_inputs`` /
``required_artifacts`` / ``judge_requirements``，``MetricPlanner`` 据此规划，而不是
仅靠写死的 metric id 分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    level: str
    required_inputs: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    judge_requirements: tuple[str, ...] = ()
    compute: Callable | None = None


@dataclass(frozen=True)
class ArtifactDependency:
    """某个 Artifact 如何被构建：用哪个 builder、需要哪个 JudgeTask、依赖哪些上游 Artifact。"""

    artifact_name: str
    builder_task: str
    judge_task: str
    depends_on: tuple[str, ...] = ()


# 首版四类 Artifact 的依赖关系（决策 3 / 架构 5.6）。
ARTIFACT_DEPENDENCIES: dict[str, ArtifactDependency] = {
    "AtomicClaimsArtifact": ArtifactDependency(
        artifact_name="AtomicClaimsArtifact",
        builder_task="build_atomic_claims",
        judge_task="claim_extraction",
    ),
    "EvidenceAlignmentArtifact": ArtifactDependency(
        artifact_name="EvidenceAlignmentArtifact",
        builder_task="build_evidence_alignment",
        judge_task="evidence_alignment",
        depends_on=("AtomicClaimsArtifact",),
    ),
    "GoldAlignmentArtifact": ArtifactDependency(
        artifact_name="GoldAlignmentArtifact",
        builder_task="build_gold_alignment",
        judge_task="gold_alignment",
        depends_on=("AtomicClaimsArtifact",),
    ),
}


class MetricRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, MetricSpec] = {}

    def register(self, spec: MetricSpec) -> None:
        self._metrics[spec.metric_id] = spec

    def get(self, metric_id: str) -> MetricSpec:
        return self._metrics[metric_id]

    def has(self, metric_id: str) -> bool:
        return metric_id in self._metrics

    def list_ids(self) -> list[str]:
        return sorted(self._metrics)


def default_registry() -> MetricRegistry:
    registry = MetricRegistry()

    # retrieval_basic_suite
    for metric_id in (
        "retrieval.hit_at_k",
        "retrieval.recall_at_k",
        "retrieval.mrr",
        "retrieval.evidence_coverage",
        "retrieval.segment_resolve_rate",
        "retrieval.gold_phrase_coverage_at_k",
        "retrieval.gold_evidence_similarity_at_k",
        "retrieval.latency",
    ):
        registry.register(
            MetricSpec(
                metric_id=metric_id,
                level="retrieval",
                required_inputs=("RetrievalTrace",),
            )
        )

    # e2e_basic_suite
    registry.register(
        MetricSpec(
            metric_id="e2e.key_point_coverage",
            level="e2e",
            required_inputs=("AnswerTrace",),
            required_artifacts=("AtomicClaimsArtifact", "GoldAlignmentArtifact"),
            judge_requirements=("gold_alignment",),
        )
    )
    registry.register(
        MetricSpec(
            metric_id="e2e.faithfulness",
            level="e2e",
            required_inputs=("AnswerTrace",),
            required_artifacts=("AtomicClaimsArtifact", "EvidenceAlignmentArtifact"),
            judge_requirements=("evidence_alignment",),
        )
    )
    registry.register(
        MetricSpec(
            metric_id="e2e.citation_accuracy",
            level="e2e",
            required_inputs=("AnswerTrace",),
            required_artifacts=("AtomicClaimsArtifact", "EvidenceAlignmentArtifact"),
            judge_requirements=("evidence_alignment",),
        )
    )
    registry.register(
        MetricSpec(
            metric_id="e2e.refusal_accuracy",
            level="e2e",
            required_inputs=("AnswerTrace",),
        )
    )
    registry.register(
        MetricSpec(
            metric_id="e2e.answer_correctness",
            level="e2e",
            required_inputs=("AnswerTrace",),
            required_artifacts=("AtomicClaimsArtifact", "EvidenceAlignmentArtifact", "GoldAlignmentArtifact"),
            judge_requirements=("answer_correctness",),
        )
    )

    return registry
