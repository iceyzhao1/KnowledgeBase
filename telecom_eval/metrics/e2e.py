from telecom_eval.models.artifacts import EvaluationArtifact
from telecom_eval.models.cases import Answerability, EvaluationCase
from telecom_eval.models.traces import AnswerOutput, Refusal

# 决策 7：UNANSWERABLE 与 SHOULD_REFUSE 都期望被拒答。
_EXPECT_REFUSAL = {Answerability.UNANSWERABLE, Answerability.SHOULD_REFUSE}


def faithfulness(claims_artifact: EvaluationArtifact, evidence_alignment: EvaluationArtifact) -> float:
    claims = claims_artifact.payload["claims"]
    if not claims:
        return 0.0
    supported = [
        item
        for item in evidence_alignment.payload["alignments"]
        if item["support_label"] == "supported"
    ]
    return len(supported) / len(claims)


def key_point_coverage(gold_alignment: EvaluationArtifact) -> float:
    alignments = gold_alignment.payload["alignments"]
    if not alignments:
        return 0.0
    covered = [item for item in alignments if item["coverage_label"] == "covered"]
    return len(covered) / len(alignments)


def citation_accuracy(output: AnswerOutput, evidence_alignment: EvaluationArtifact) -> float:
    if not output.citations:
        return 0.0
    supported_evidence_ids = {
        item["evidence_id"]
        for item in evidence_alignment.payload["alignments"]
        if item["support_label"] == "supported" and item["evidence_id"]
    }
    correct = [
        citation
        for citation in output.citations
        if citation.get("evidence_id") in supported_evidence_ids
    ]
    return len(correct) / len(output.citations)


def refusal_accuracy(case: EvaluationCase, refusal: Refusal) -> float:
    should_refuse = case.answerability in _EXPECT_REFUSAL
    return 1.0 if should_refuse == refusal.refused else 0.0


def answer_correctness_score(result: dict) -> float:
    try:
        score = float(result.get("score"))
    except (TypeError, ValueError):
        return 0.0
    if score >= 0.75:
        return 1.0
    if score >= 0.25:
        return 0.5
    return 0.0
