"""Evaluation runner.

只有 Runner 调用 Adapter（架构 5.3）。只跑 confirmed gold 的样本进入正式评估。"""

from __future__ import annotations

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import AnswerTrace, RetrievalTrace
from telecom_eval.subjects.base import AnswerSubjectAdapter, RetrievalSubjectAdapter


class EvaluationRunner:
    def __init__(self, *, run_id: str, subject_id: str):
        self.run_id = run_id
        self.subject_id = subject_id

    def run_retrieval(
        self, cases: list[EvaluationCase], adapter: RetrievalSubjectAdapter
    ) -> list[RetrievalTrace]:
        traces: list[RetrievalTrace] = []
        for case in cases:
            if not case.is_formal():
                continue
            traces.append(adapter.retrieve(case, run_id=self.run_id))
        return traces

    def run_answer(
        self, cases: list[EvaluationCase], adapter: AnswerSubjectAdapter
    ) -> list[AnswerTrace]:
        traces: list[AnswerTrace] = []
        for case in cases:
            if not case.is_formal():
                continue
            traces.append(adapter.answer(case, run_id=self.run_id))
        return traces
