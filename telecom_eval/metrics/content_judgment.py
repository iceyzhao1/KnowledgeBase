"""Content-semantic retrieval judgments.

The main retrieval metrics must answer whether the returned text supports the
gold evidence, not whether any internal evidence IDs happen to match.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import EvidenceItem

SegmentResolver = Callable[[str], str | None]
SupportJudge = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any] | None]


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", _norm(text)))


def _similarity(left: str, right: str) -> float:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_tokens = _tokens(left_norm)
    right_tokens = _tokens(right_norm)
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(token_score, sequence_score)


def _phrase_matches(phrase: str, text: str) -> bool:
    phrase_norm = _norm(phrase)
    text_norm = _norm(text)
    if not phrase_norm or not text_norm:
        return False
    if phrase_norm in text_norm:
        return True
    ascii_tokens = [
        token.lower()
        for token in re.findall(r"[a-z0-9]+", phrase_norm, flags=re.IGNORECASE)
        if len(token) >= 2
    ]
    return bool(ascii_tokens) and all(token in text_norm for token in ascii_tokens)


def _gold_points(case: EvaluationCase) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index, gold in enumerate(case.expected_evidence, start=1):
        text = str(gold.get("content") or gold.get("match_text") or "").strip()
        phrases = [
            str(value).strip()
            for value in (gold.get("match_text"), *(case.expected_evidence_contains or []))
            if str(value or "").strip()
        ]
        if text or phrases:
            points.append(
                {
                    "gold_id": f"gold_evidence_{index}",
                    "source": "expected_evidence",
                    "text": text or phrases[0],
                    "phrases": list(dict.fromkeys(phrases)),
                    "metadata": {
                        key: gold.get(key)
                        for key in ("source_ref", "line_start", "line_end", "section")
                        if gold.get(key) is not None
                    },
                }
            )
    if points:
        return points
    for index, point in enumerate(case.expected_key_points, start=1):
        text = str(point).strip()
        if text:
            points.append(
                {
                    "gold_id": f"key_point_{index}",
                    "source": "expected_key_points",
                    "text": text,
                    "phrases": [text],
                    "metadata": {},
                }
            )
    if not points and case.expected_answer:
        points.append(
            {
                "gold_id": "expected_answer",
                "source": "expected_answer",
                "text": case.expected_answer,
                "phrases": case.expected_key_points,
                "metadata": {},
            }
        )
    return points


def _candidate_text(item: EvidenceItem, resolver: SegmentResolver | None = None) -> str:
    resolved_parts: list[str] = []
    if resolver is not None:
        for value in (
            item.provenance.get("raw_segment_ids"),
            item.provenance.get("source_segment_id"),
        ):
            values = value if isinstance(value, list | tuple | set) else [value]
            for segment_id in values:
                if segment_id is None:
                    continue
                resolved = resolver(str(segment_id))
                if resolved:
                    resolved_parts.append(resolved)
    parts = [item.title or "", item.content or "", *resolved_parts]
    return "\n".join(part for part in parts if part)


def _score_candidate(gold: dict[str, Any], item: EvidenceItem, text: str) -> dict[str, Any]:
    phrases = [phrase for phrase in gold.get("phrases", []) if phrase]
    matched_phrases = [phrase for phrase in phrases if _phrase_matches(phrase, text)]
    text_similarity = _similarity(str(gold.get("text") or ""), text)
    phrase_score = len(matched_phrases) / len(phrases) if phrases else 0.0
    score = max(text_similarity, phrase_score)
    if score >= 0.72 or (phrases and phrase_score >= 1.0):
        support_label = "supported"
        final_score = 1.0
        reason = "检索内容覆盖标准证据文本或关键短语。"
    elif score >= 0.45 or phrase_score > 0:
        support_label = "partial"
        final_score = 0.5
        reason = "检索内容与标准证据部分相关，但覆盖不完整。"
    else:
        support_label = "not_supported"
        final_score = 0.0
        reason = "检索内容未覆盖该标准证据点。"
    return {
        "rank": item.rank,
        "evidence_id": item.evidence_id,
        "source_id": item.source_id,
        "title": item.title,
        "content_preview": text[:260],
        "score": final_score,
        "raw_similarity": round(text_similarity, 4),
        "phrase_coverage": round(phrase_score, 4),
        "matched_phrases": matched_phrases,
        "support_label": support_label,
        "method": "rule",
        "reason": reason,
    }


def build_retrieval_content_judgment(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None = None,
    support_judge: SupportJudge | None = None,
) -> dict[str, Any]:
    gold_points = _gold_points(case)
    ranked = sorted(evidence_package, key=lambda item: item.rank)
    ranked_top_k = ranked[:k]
    judgments: list[dict[str, Any]] = []
    first_hit_rank: int | None = None

    for gold in gold_points:
        candidates = [
            _score_candidate(gold, item, _candidate_text(item, resolver))
            for item in ranked_top_k
        ]
        best = max(candidates, key=lambda item: (item["score"], -item["rank"]), default=None)
        if best and support_judge is not None and _needs_llm_review(best):
            judged = support_judge(gold, candidates[:3])
            if judged:
                best = {**best, **judged, "method": "llm"}
        if best and best["score"] > 0 and first_hit_rank is None:
            first_hit_rank = best["rank"]
        judgments.append(
            {
                "gold_id": gold["gold_id"],
                "gold_source": gold["source"],
                "gold_text": gold["text"],
                "gold_phrases": gold["phrases"],
                "gold_metadata": gold["metadata"],
                "best_support": best
                or {
                    "rank": None,
                    "support_label": "not_supported",
                    "score": 0.0,
                    "method": "rule",
                    "reason": "没有检索候选可用于判断。",
                },
                "candidates": candidates,
            }
        )

    scores = [float(j["best_support"]["score"]) for j in judgments]
    hit = 1.0 if any(score > 0 for score in scores) else 0.0
    recall = sum(1 for score in scores if score > 0) / len(scores) if scores else 0.0
    coverage = sum(scores) / len(scores) if scores else 0.0
    mrr = 1.0 / first_hit_rank if first_hit_rank else 0.0
    metrics = {
        "retrieval.hit_at_k": hit,
        "retrieval.recall_at_k": recall,
        "retrieval.mrr": mrr,
        "retrieval.evidence_coverage": coverage,
    }
    return {
        "top_k": k,
        "candidate_count": len(evidence_package),
        "judgment_policy": "content_semantic_v1",
        "id_policy": "ignored",
        "metrics": metrics,
        "judgments": judgments,
    }


def content_judgment_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _needs_llm_review(best: dict[str, Any]) -> bool:
    if best.get("support_label") == "partial":
        return True
    return best.get("support_label") == "not_supported" and float(best.get("raw_similarity") or 0.0) >= 0.25
