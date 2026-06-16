"""eval-llm domain logic: turn requests into provider calls + structured output.

These functions are transport-agnostic (no FastAPI types) so they can be unit
tested directly and reused by the HTTP layer in ``app.py``.
"""

from __future__ import annotations

from ..shared.jsonutil import extract_json
from ..shared.models import QuestionType, TokenUsage
from .prompts import (
    build_answer_match_judge_prompt,
    build_answer_prompt,
    build_generate_prompt,
    build_judge_prompt,
    build_overall_summary_prompt,
    build_retrieval_judge_prompt,
)
from .providers.base import LLMProvider


def _coerce_types(types: list[str] | None) -> list[QuestionType]:
    if not types:
        return QuestionType.all()
    out: list[QuestionType] = []
    for t in types:
        try:
            out.append(QuestionType(str(t).strip().lower()))
        except ValueError:
            continue
    return out or QuestionType.all()


def preview_generate_prompt(
    *,
    document_text: str,
    doc_ref: str,
    types: list[str] | None = None,
    per_type: int = 3,
    persona: str | None = None,
) -> dict:
    """渲染出题提示词但不调用模型。返回 {system, user, doc_ref, doc_chars}。"""

    qtypes = _coerce_types(types)
    system, user = build_generate_prompt(
        document_text=document_text,
        doc_ref=doc_ref,
        types=qtypes,
        per_type=per_type,
        persona=persona,
    )
    return {
        "doc_ref": doc_ref,
        "doc_chars": len(document_text or ""),
        "types": [t.value for t in qtypes],
        "per_type": per_type,
        "system": system,
        "user": user,
    }


def generate_cases(
    provider: LLMProvider,
    *,
    document_text: str,
    doc_ref: str,
    types: list[str] | None = None,
    per_type: int = 3,
    persona: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> dict:
    """Generate raw test cases for one document.

    Returns ``{"cases": [...], "usage": {...}}`` where each case is a plain dict
    matching the contract (question / question_type / expected_answer /
    key_points / source / difficulty). eval-api assigns stable ids and builds the
    TestSuite — this service stays stateless.
    """

    qtypes = _coerce_types(types)
    system, user = build_generate_prompt(
        document_text=document_text,
        doc_ref=doc_ref,
        types=qtypes,
        per_type=per_type,
        persona=persona,
    )
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        payload = extract_json(result.text)
    except ValueError:
        payload = {}

    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    cases: list[dict] = []
    for raw in raw_cases or []:
        if not isinstance(raw, dict):
            continue
        qtype = str(raw.get("question_type", "")).strip().lower()
        if qtype not in {t.value for t in QuestionType.all()}:
            continue
        question = str(raw.get("question", "")).strip()
        expected = str(raw.get("expected_answer", "")).strip()
        if not question or not expected:
            continue
        src = raw.get("source") or {}
        cases.append(
            {
                "question": question,
                "question_type": qtype,
                "expected_answer": expected,
                "key_points": [str(p) for p in (raw.get("key_points") or [])],
                "expected_evidence": [
                    str(e).strip()
                    for e in (raw.get("expected_evidence") or [])
                    if str(e).strip()
                ],
                "expected_entities": [
                    str(e).strip()
                    for e in (raw.get("expected_entities") or [])
                    if str(e).strip()
                ],
                "source": {
                    "doc": str(src.get("doc") or doc_ref),
                    "section": src.get("section"),
                    "quote": src.get("quote"),
                },
                "difficulty": str(raw.get("difficulty", "normal")).strip() or "normal",
            }
        )

    return {"cases": cases, "usage": result.usage.model_dump()}


def answer_case(
    provider: LLMProvider,
    *,
    question: str,
    evidence: list[str] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> dict:
    """L2 作答：让某个模型只读固定证据包整合一版最终答案。

    纯 completion（无工具、不再检索）。返回 ``{answer, usage}``，answer 是答案正文。
    """

    evidence = evidence or []
    system, user = build_answer_prompt(question=question, evidence=evidence)
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=False,
    )
    return {"answer": result.text.strip(), "usage": result.usage.model_dump()}


def judge_case(
    provider: LLMProvider,
    *,
    question: str,
    question_type: str,
    expected_answer: str,
    key_points: list[str] | None = None,
    agent_answer: str,
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> dict:
    """Judge one answer. Returns verdict/score/rationale/points + usage."""

    key_points = key_points or []
    system, user = build_judge_prompt(
        question=question,
        question_type=question_type,
        expected_answer=expected_answer,
        key_points=key_points,
        agent_answer=agent_answer,
    )
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        payload = extract_json(result.text)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    score = float(payload.get("score", 0.0) or 0.0)
    score = max(0.0, min(1.0, score))
    verdict = str(payload.get("verdict", "")).strip().lower()
    if verdict not in {"correct", "partial", "incorrect"}:
        verdict = "correct" if score >= 0.8 else "partial" if score > 0 else "incorrect"

    return {
        "verdict": verdict,
        "score": score,
        "rationale": str(payload.get("rationale", "")),
        "covered_points": [str(p) for p in (payload.get("covered_points") or [])],
        "missed_points": [str(p) for p in (payload.get("missed_points") or [])],
        "usage": result.usage.model_dump(),
    }


def judge_answer_match(
    provider: LLMProvider,
    *,
    question: str,
    expected_answer: str,
    key_points: list[str] | None = None,
    answer: str,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict:
    """L2 对照判：把模型答案 A 跟黄金最终答案 G 比，一次出覆盖/准确/矛盾 + 综合分。

    返回（全部锚定黄金答案 G）：
      coverage  覆盖度 = 命中要点 / (命中+遗漏)      —— 防漏
      precision 准确度 = G 支持论断 / (支持+多余+矛盾) —— 防掺水（矛盾也算不准）
      f1        综合分 = 2·P·R/(P+R)                  —— 排行榜主排序
      has_hard_error / contradiction 清单            —— 硬伤高亮
    以及 covered/missed/correct/extra/contradictions 五份清单 + rationale + usage。
    """

    key_points = key_points or []
    system, user = build_answer_match_judge_prompt(
        question=question,
        expected_answer=expected_answer,
        key_points=key_points,
        answer=answer,
    )
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        payload = extract_json(result.text)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    def _strlist(key: str) -> list[str]:
        return [str(x) for x in (payload.get(key) or []) if str(x).strip()]

    covered = _strlist("covered_points")
    missed = _strlist("missed_points")
    correct = _strlist("correct_claims")
    extra = _strlist("extra_claims")
    contradictions = _strlist("contradictions")

    cov_denom = len(covered) + len(missed)
    coverage = len(covered) / cov_denom if cov_denom else 0.0
    prec_denom = len(correct) + len(extra) + len(contradictions)
    precision = len(correct) / prec_denom if prec_denom else 0.0
    f1 = (
        2 * coverage * precision / (coverage + precision)
        if (coverage + precision) > 0
        else 0.0
    )

    return {
        "coverage": round(coverage, 4),
        "precision": round(precision, 4),
        "f1": round(f1, 4),
        "covered_points": covered,
        "missed_points": missed,
        "correct_claims": correct,
        "extra_claims": extra,
        "contradictions": contradictions,
        "has_hard_error": bool(contradictions),
        "rationale": str(payload.get("rationale", "")),
        "usage": result.usage.model_dump(),
    }


def judge_retrieval(
    provider: LLMProvider,
    *,
    question: str,
    expected_answer: str,
    gold_facts: list[str],
    items: list[str],
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict:
    """Judge one case's retrieved evidence against its gold facts.

    Returns ``{item_grades, gold_covered_at, rationale, usage}`` where
    ``item_grades`` is a 0–3 grade per retrieved item in rank order and
    ``gold_covered_at`` is the 1-based rank of the first item covering each gold
    fact (0 if uncovered). Aggregate IR metrics are computed downstream from
    these raw labels, so multiple K can be reported off one judge pass.
    """

    gold_facts = gold_facts or []
    items = items or []
    system, user = build_retrieval_judge_prompt(
        question=question,
        expected_answer=expected_answer,
        gold_facts=gold_facts,
        items=items,
    )
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        payload = extract_json(result.text)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # Map model output (1-based index references) back onto rank/position order.
    grades = [0] * len(items)
    for entry in payload.get("items") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index"))
            grade = int(entry.get("grade", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(items):
            grades[idx - 1] = max(0, min(3, grade))

    covered = [0] * len(gold_facts)
    for entry in payload.get("facts") or []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index"))
            by = int(entry.get("covered_by", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= len(gold_facts):
            covered[idx - 1] = by if 1 <= by <= len(items) else 0

    return {
        "item_grades": grades,
        "gold_covered_at": covered,
        "rationale": str(payload.get("rationale", "")),
        "usage": result.usage.model_dump(),
    }


def summarize_overall(
    provider: LLMProvider,
    *,
    suite_meta: dict,
    l1: dict | None,
    l2: dict | None,
    l4: dict | None,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> dict:
    """综合总评：把三层关键指标交给模型，整合出一段中文整体评价。

    纯 completion（无工具、非 JSON）。返回 ``{summary, usage}``。
    """

    system, user = build_overall_summary_prompt(
        suite_meta=suite_meta, l1=l1, l2=l2, l4=l4
    )
    result = provider.chat(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=False,
    )
    return {"summary": result.text.strip(), "usage": result.usage.model_dump()}
