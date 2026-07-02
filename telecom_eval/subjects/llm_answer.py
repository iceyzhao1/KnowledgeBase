"""Evidence-grounded LLM answer adapter.

This adapter is the e2e "answering system" when no external answer API exists. It calls a
configured model with only the user question and retrieved evidence package. It must never
receive gold answers or gold key points.
"""

from __future__ import annotations

import json
import time
from typing import Any

from telecom_eval.judges.providers.base import JudgeProvider
from telecom_eval.models.cases import Answerability, EvaluationCase
from telecom_eval.models.traces import (
    AnswerInput,
    AnswerOutput,
    AnswerTrace,
    Cost,
    EvidenceItem,
    Refusal,
)

_EXPECT_REFUSAL = {Answerability.UNANSWERABLE, Answerability.SHOULD_REFUSE}


def _parse_answer_text(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {"answer": ""}
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"answer": stripped}


class EvidenceGroundedLLMAnswerAdapter:
    """Generate answers from retrieved evidence using an LLM provider."""

    def __init__(self, *, subject_id: str, provider: JudgeProvider, model: str):
        self.subject_id = subject_id
        self.provider = provider
        self.model = model

    def answer(
        self,
        case: EvaluationCase,
        *,
        run_id: str,
        evidence_package: list[EvidenceItem] | None = None,
    ) -> AnswerTrace:
        evidence_package = list(evidence_package or [])
        system, user = self._prompt(case, evidence_package)
        started = time.perf_counter()
        try:
            result = self.provider.invoke(system=system, user=user)
            latency_ms = result.latency_ms or int((time.perf_counter() - started) * 1000)
            parsed = _parse_answer_text(result.text)
            return self._trace_from_result(
                case=case,
                run_id=run_id,
                parsed=parsed,
                evidence_package=evidence_package,
                latency_ms=latency_ms,
                usage=result.usage,
                errors=[],
                raw=result.raw,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return AnswerTrace(
                trace_id=f"trace_ans_{run_id}_{case.case_id}",
                case_id=case.case_id,
                run_id=run_id,
                subject_id=self.subject_id,
                input=AnswerInput(question=case.question, scenario_id=case.scenario_id, filters=case.filters),
                output=AnswerOutput(
                    final_answer="",
                    citations=[],
                    evidence_package=evidence_package,
                    refusal=Refusal(refused=True, reason="回答模型调用失败"),
                ),
                latency_ms=latency_ms,
                errors=[f"answer llm call failed: {exc}"],
                optional_debug_trace={"answer_model": self.model, "provider": self.provider.name},
            )

    def _prompt(self, case: EvaluationCase, evidence_package: list[EvidenceItem]) -> tuple[str, str]:
        system = (
            "你是电信知识库问答系统。只能根据提供的证据包回答用户问题。"
            "如果证据不足以回答，必须拒答并说明资料不足。"
            "只输出 JSON："
            '{"answer":"最终回答","citations":[{"evidence_id":"证据ID"}],'
            '"refusal":{"refused":false,"reason":null}}。'
        )
        evidence = [
            {
                "evidence_id": item.evidence_id,
                "rank": item.rank,
                "title": item.title,
                "content": item.content,
                "source_id": item.source_id,
            }
            for item in evidence_package
        ]
        user = json.dumps(
            {
                "question": case.question,
                "scenario_id": case.scenario_id,
                "answerability_hint": case.answerability.value,
                "must_refuse_if_unanswerable": case.answerability in _EXPECT_REFUSAL,
                "evidence_package": evidence,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return system, user

    def _trace_from_result(
        self,
        *,
        case: EvaluationCase,
        run_id: str,
        parsed: dict[str, Any],
        evidence_package: list[EvidenceItem],
        latency_ms: int,
        usage,
        errors: list[str],
        raw: dict[str, Any],
    ) -> AnswerTrace:
        refusal_raw = parsed.get("refusal") if isinstance(parsed.get("refusal"), dict) else {}
        refused = bool(refusal_raw.get("refused", False))
        answer = str(parsed.get("answer") or parsed.get("final_answer") or "")
        citations = parsed.get("citations") if isinstance(parsed.get("citations"), list) else []
        normalized_citations = []
        evidence_ids = {item.evidence_id for item in evidence_package}
        for index, citation in enumerate(citations, start=1):
            if not isinstance(citation, dict):
                continue
            evidence_id = str(citation.get("evidence_id") or "")
            if evidence_id and evidence_id in evidence_ids:
                normalized_citations.append(
                    {
                        "citation_id": str(citation.get("citation_id") or f"cit_{index:03d}"),
                        "evidence_id": evidence_id,
                    }
                )
        return AnswerTrace(
            trace_id=f"trace_ans_{run_id}_{case.case_id}",
            case_id=case.case_id,
            run_id=run_id,
            subject_id=self.subject_id,
            input=AnswerInput(question=case.question, scenario_id=case.scenario_id, filters=case.filters),
            output=AnswerOutput(
                final_answer=answer,
                citations=normalized_citations,
                evidence_package=evidence_package,
                refusal=Refusal(refused=refused, reason=refusal_raw.get("reason")),
            ),
            latency_ms=latency_ms,
            cost=Cost(
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            ),
            errors=errors,
            optional_debug_trace={
                "answer_model": self.model,
                "provider": self.provider.name,
                "raw": raw,
            },
        )
