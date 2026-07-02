"""Rule-based artifact builders.

第一版用确定性规则构建可复用 Artifact（claims / evidence_alignment / gold_alignment），
不在 builder 内调用 LLM（架构 2.7 / 决策 9）。语义判断缺口由 Planner 创建 JudgeTask，
经 JudgeCache 治理。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from telecom_eval.models.artifacts import AtomicClaim, EvaluationArtifact
from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import EvidenceItem

_SENTENCE_SEPARATORS = "。；;\n"


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_sentences(text: str) -> list[str]:
    normalized = text
    for sep in _SENTENCE_SEPARATORS:
        normalized = normalized.replace(sep, "。")
    return [part.strip(" 。；;") for part in normalized.split("。") if part.strip()]


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    cleaned = "".join(ch for ch in text if not ch.isspace())
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _ngram_overlap(claim_text: str, evidence_text: str, n: int = 2) -> float:
    claim_grams = _char_ngrams(claim_text, n)
    if not claim_grams:
        return 0.0
    evidence_grams = _char_ngrams(evidence_text, n)
    return len(claim_grams & evidence_grams) / len(claim_grams)


def build_atomic_claims(
    case_id: str, run_id: str, subject_id: str, trace_id: str, answer: str
) -> EvaluationArtifact:
    parts = _split_sentences(answer)
    claims = [
        AtomicClaim(claim_id=f"claim_{index:03d}", text=part).model_dump()
        for index, part in enumerate(parts, start=1)
    ]
    answer_hash = _hash_text(answer)
    return EvaluationArtifact(
        artifact_id=f"art_claims_{case_id}_{run_id}_{subject_id}",
        artifact_type="atomic_claims",
        case_id=case_id,
        run_id=run_id,
        subject_id=subject_id,
        trace_id=trace_id,
        inputs_hash=answer_hash,
        payload={"source_answer_hash": answer_hash, "claims": claims},
        lineage={"source_trace_ids": [trace_id], "created_by": "artifact_builder"},
        created_at=_now(),
    )


def build_evidence_alignment(
    case_id: str,
    run_id: str,
    subject_id: str,
    trace_id: str,
    claims_artifact: EvaluationArtifact,
    evidence_package: list[EvidenceItem],
    *,
    ngram_threshold: float = 0.6,
) -> EvaluationArtifact:
    all_evidence_text = "\n".join(item.content for item in evidence_package)
    alignments = []
    for claim in claims_artifact.payload["claims"]:
        claim_text = claim["text"]
        # 1) 精确子串
        matching = next(
            (item for item in evidence_package if claim_text and claim_text in item.content),
            None,
        )
        # 2) 中文字符 n-gram 重叠兜底（决策 9：不用 .split() 切中文）
        if matching is None and claim_text:
            best_item = None
            best_overlap = 0.0
            for item in evidence_package:
                overlap = _ngram_overlap(claim_text, item.content)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_item = item
            if best_item is not None and best_overlap >= ngram_threshold:
                matching = best_item
        alignments.append(
            {
                "claim_id": claim["claim_id"],
                "evidence_id": matching.evidence_id if matching else None,
                "support_label": "supported" if matching else "not_supported",
                "rationale": "规则匹配到证据文本" if matching else "未匹配到支持证据",
            }
        )
    evidence_hash = _hash_text(all_evidence_text)
    return EvaluationArtifact(
        artifact_id=f"art_evidence_alignment_{case_id}_{run_id}_{subject_id}",
        artifact_type="evidence_alignment",
        case_id=case_id,
        run_id=run_id,
        subject_id=subject_id,
        trace_id=trace_id,
        inputs_hash=_hash_text(claims_artifact.inputs_hash + evidence_hash),
        payload={
            "source_claims_artifact_id": claims_artifact.artifact_id,
            "source_evidence_hash": evidence_hash,
            "alignments": alignments,
        },
        lineage={"source_trace_ids": [trace_id], "created_by": "artifact_builder"},
        created_at=_now(),
    )


def build_gold_alignment(
    case_id: str,
    run_id: str,
    subject_id: str,
    trace_id: str,
    claims_artifact: EvaluationArtifact,
    case: EvaluationCase,
    *,
    ngram_threshold: float = 0.6,
) -> EvaluationArtifact:
    claims = claims_artifact.payload["claims"]
    claim_text_all = "\n".join(claim["text"] for claim in claims)
    alignments = []
    for index, key_point in enumerate(case.expected_key_points, start=1):
        matched_claims = []
        for claim in claims:
            text = claim["text"]
            if key_point in text or text in key_point:
                matched_claims.append(claim["claim_id"])
            elif _ngram_overlap(key_point, text) >= ngram_threshold:
                matched_claims.append(claim["claim_id"])
        alignments.append(
            {
                "gold_id": f"kp_{index:03d}",
                "claim_ids": matched_claims,
                "coverage_label": "covered" if matched_claims else "missed",
                "contradiction_label": "none",
                "rationale": "规则匹配到答案要点" if matched_claims else "未匹配到答案要点",
            }
        )
    return EvaluationArtifact(
        artifact_id=f"art_gold_alignment_{case_id}_{run_id}_{subject_id}",
        artifact_type="gold_alignment",
        case_id=case_id,
        run_id=run_id,
        subject_id=subject_id,
        trace_id=trace_id,
        inputs_hash=_hash_text(claim_text_all + case.expected_answer),
        payload={"source_claims_artifact_id": claims_artifact.artifact_id, "alignments": alignments},
        lineage={"source_trace_ids": [trace_id], "created_by": "artifact_builder"},
        created_at=_now(),
    )
