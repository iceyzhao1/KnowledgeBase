"""Deterministic fake adapters.

不依赖外部 API，用于证明 Runner 与 Trace 边界（架构 5.3 / 决策 2）。"""

from __future__ import annotations

from telecom_eval.models.cases import Answerability, EvaluationCase
from telecom_eval.models.traces import (
    AnswerInput,
    AnswerOutput,
    AnswerTrace,
    EvidenceItem,
    Refusal,
    RetrievalInput,
    RetrievalOutput,
    RetrievalTrace,
)

_EXPECT_REFUSAL = {Answerability.UNANSWERABLE, Answerability.SHOULD_REFUSE}


def _gold_evidence_item(case: EvaluationCase) -> EvidenceItem:
    gold = case.expected_evidence[0] if case.expected_evidence else {}
    return EvidenceItem(
        evidence_id=str(gold.get("evidence_id", "ev_fake")),
        content="；".join(case.expected_key_points),
        source_id=str(gold.get("source_id", "source_fake")),
        source_type="document_chunk",
        rank=1,
        provenance=dict(gold),
    )


class FakeRetrievalAdapter:
    def __init__(self, subject_id: str):
        self.subject_id = subject_id

    def retrieve(self, case: EvaluationCase, *, run_id: str) -> RetrievalTrace:
        return RetrievalTrace(
            trace_id=f"trace_ret_{run_id}_{case.case_id}",
            case_id=case.case_id,
            run_id=run_id,
            subject_id=self.subject_id,
            input=RetrievalInput(
                query=case.question, scenario_id=case.scenario_id, filters=case.filters
            ),
            output=RetrievalOutput(evidence_package=[_gold_evidence_item(case)]),
            latency_ms=10,
            errors=[],
        )


class FakeAnswerAdapter:
    def __init__(self, subject_id: str):
        self.subject_id = subject_id

    def answer(self, case: EvaluationCase, *, run_id: str) -> AnswerTrace:
        should_refuse = case.answerability in _EXPECT_REFUSAL
        evidence = _gold_evidence_item(case)
        if should_refuse:
            output = AnswerOutput(
                final_answer="",
                citations=[],
                evidence_package=[evidence],
                refusal=Refusal(refused=True, reason="知识不足，按场景规则拒答"),
            )
        else:
            output = AnswerOutput(
                final_answer="。".join(case.expected_key_points) + "。",
                citations=[{"citation_id": "cit_001", "evidence_id": evidence.evidence_id}],
                evidence_package=[evidence],
                refusal=Refusal(refused=False),
            )
        return AnswerTrace(
            trace_id=f"trace_ans_{run_id}_{case.case_id}",
            case_id=case.case_id,
            run_id=run_id,
            subject_id=self.subject_id,
            input=AnswerInput(question=case.question, scenario_id=case.scenario_id),
            output=output,
            latency_ms=20,
            errors=[],
        )
