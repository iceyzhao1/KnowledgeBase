"""Subject adapter protocols.

架构 5.3：只有 Runner 能调用 Adapter，只有 Adapter 能调用外部被测系统。指标、planner、
诊断、报告一律不得直接调外部 API。第一版只提供 fake adapter；真实 HTTP adapter 待 API
规格给出后再加。
"""

from typing import Protocol

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import AnswerTrace, RetrievalTrace


class RetrievalSubjectAdapter(Protocol):
    subject_id: str

    def retrieve(self, case: EvaluationCase, *, run_id: str) -> RetrievalTrace:
        ...


class AnswerSubjectAdapter(Protocol):
    subject_id: str

    def answer(self, case: EvaluationCase, *, run_id: str) -> AnswerTrace:
        ...
