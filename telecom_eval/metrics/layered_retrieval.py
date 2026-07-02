"""Layered retrieval metrics.

These metrics separate three concerns:
- technical retrieval health;
- content coverage after resolving segment IDs to raw segment text;
- text similarity diagnostics for explaining retrieval quality.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import EvidenceItem

SegmentResolver = Callable[[str], str | None]


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _ranked(evidence_package: list[EvidenceItem], k: int | None = None) -> list[EvidenceItem]:
    ranked = sorted(evidence_package, key=lambda item: item.rank)
    return ranked[:k] if k is not None else ranked


def _segment_ids(item: EvidenceItem) -> list[str]:
    ids: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for child in value:
                add(child)
            return
        text = str(value)
        if text and text not in ids:
            ids.append(text)

    add(item.provenance.get("raw_segment_ids"))
    add(item.provenance.get("source_segment_id"))
    citation = item.provenance.get("citation")
    if isinstance(citation, dict):
        add(citation.get("raw_segment_ids"))
    add(item.evidence_id)
    return ids


def resolve_segment_text(item: EvidenceItem, resolver: SegmentResolver | None = None) -> str | None:
    """Resolve an evidence item to raw segment text.

    In production, ``resolver`` should read ``asset_raw_segments`` by segment ID. For local smoke
    runs and tests, it can be a dict-backed function. Direct ``item.content`` is only a fallback
    for evidence without a segment ID.
    """
    segment_ids = _segment_ids(item)
    if resolver is not None:
        for segment_id in segment_ids:
            resolved = resolver(segment_id)
            if resolved:
                return resolved
    if not segment_ids and item.content:
        return item.content
    return None


def segment_resolve_rate(
    evidence_package: list[EvidenceItem], *, resolver: SegmentResolver | None, k: int | None = None
) -> float:
    ranked = _ranked(evidence_package, k)
    if not ranked:
        return 0.0
    resolved = sum(1 for item in ranked if resolve_segment_text(item, resolver))
    return resolved / len(ranked)


def _gold_phrases(case: EvaluationCase) -> list[str]:
    phrases: list[str] = []
    for source in (case.expected_evidence_contains, case.expected_key_points):
        for phrase in source:
            normalized = _norm(phrase)
            if normalized and normalized not in phrases:
                phrases.append(normalized)
    for gold in case.expected_evidence:
        normalized = _norm(gold.get("match_text"))
        if normalized and normalized not in phrases:
            phrases.append(normalized)
    return phrases


def _gold_evidence_texts(case: EvaluationCase) -> list[str]:
    texts: list[str] = []
    for gold in case.expected_evidence:
        text = _norm(gold.get("content") or gold.get("match_text"))
        if text:
            texts.append(text)
    if not texts and case.expected_answer:
        texts.append(_norm(case.expected_answer))
    return texts


def _coverage(phrases: list[str], text: str) -> float:
    if not phrases:
        return 0.0
    normalized = _norm(text)
    if not normalized:
        return 0.0
    return sum(1 for phrase in phrases if _phrase_matches(phrase, normalized)) / len(phrases)


def _phrase_matches(phrase: str, normalized_text: str) -> bool:
    if phrase in normalized_text:
        return True
    ascii_tokens = [
        token.lower()
        for token in re.findall(r"[a-z0-9]+", phrase, flags=re.IGNORECASE)
        if len(token) >= 2
    ]
    if ascii_tokens and all(token in normalized_text for token in ascii_tokens):
        return True
    return False


def _similarity(left: str, right: str) -> float:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_tokens = set(re.findall(r"\w+", left_norm))
    right_tokens = set(re.findall(r"\w+", right_norm))
    token_score = 0.0
    if left_tokens and right_tokens:
        token_score = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(token_score, sequence_score)


def _resolved_texts(
    evidence_package: list[EvidenceItem], *, k: int | None, resolver: SegmentResolver | None
) -> list[tuple[EvidenceItem, str]]:
    texts: list[tuple[EvidenceItem, str]] = []
    for item in _ranked(evidence_package, k):
        text = resolve_segment_text(item, resolver)
        if text:
            texts.append((item, text))
    return texts


def gold_phrase_coverage_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None,
) -> float:
    phrases = _gold_phrases(case)
    if not phrases:
        return 0.0
    combined = "\n".join(text for _, text in _resolved_texts(evidence_package, k=k, resolver=resolver))
    return _coverage(phrases, combined)


def gold_evidence_similarity_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None,
) -> float:
    gold_texts = _gold_evidence_texts(case)
    resolved = _resolved_texts(evidence_package, k=k, resolver=resolver)
    if not gold_texts or not resolved:
        return 0.0
    scores = []
    for gold_text in gold_texts:
        scores.append(max(_similarity(gold_text, text) for _, text in resolved))
    return sum(scores) / len(scores)


def _content_match_score(case: EvaluationCase, text: str) -> float:
    phrase_score = _coverage(_gold_phrases(case), text)
    gold_texts = _gold_evidence_texts(case)
    similarity_score = max((_similarity(gold, text) for gold in gold_texts), default=0.0)
    key_point_score = _coverage([_norm(p) for p in case.expected_key_points], text)
    return 0.5 * phrase_score + 0.3 * similarity_score + 0.2 * key_point_score


def content_hit_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None,
    threshold: float = 0.45,
) -> float:
    return 1.0 if any(
        _content_match_score(case, text) >= threshold
        for _, text in _resolved_texts(evidence_package, k=k, resolver=resolver)
    ) else 0.0


def content_recall_at_k(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None,
    threshold: float = 0.45,
) -> float:
    gold_texts = _gold_evidence_texts(case)
    resolved = _resolved_texts(evidence_package, k=k, resolver=resolver)
    if not gold_texts:
        return 0.0
    hits = 0
    for gold_text in gold_texts:
        best = max(
            (0.5 * _coverage(_gold_phrases(case), text) + 0.5 * _similarity(gold_text, text) for _, text in resolved),
            default=0.0,
        )
        if best >= threshold:
            hits += 1
    return hits / len(gold_texts)


def content_mrr(
    case: EvaluationCase,
    evidence_package: list[EvidenceItem],
    *,
    k: int,
    resolver: SegmentResolver | None,
    threshold: float = 0.45,
) -> float:
    for index, item in enumerate(_ranked(evidence_package, k), start=1):
        text = resolve_segment_text(item, resolver)
        if text and _content_match_score(case, text) >= threshold:
            return 1.0 / index
    return 0.0

