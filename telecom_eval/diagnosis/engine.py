"""Diagnosis Engine.

不重新判分，只解释已有指标和 Artifact（架构第 9 节）。第一版用固定启发式规则链，按
上游→下游归因，且只产出 7 类失败类型（决策 8）：

  evaluation_material_issue -> retrieval_miss -> retrieval_ranking_issue
  -> unsupported_claim -> refusal_error -> comparison_regression -> ambiguous

其余架构级失败类型本版不实现。
"""

from __future__ import annotations

from telecom_eval.models.diagnosis import DiagnosisResult, FailureType

_RANKING_MRR_THRESHOLD = 0.5


def _result(
    *,
    case_id: str,
    run_id: str,
    subject_id: str,
    failure_type: FailureType,
    severity: str,
    evidence: list[str],
    suggested_action: str,
    confidence: float,
    related_metrics: list[str],
    related_artifacts: list[str] | None = None,
) -> DiagnosisResult:
    return DiagnosisResult(
        diagnosis_id=f"diag_{run_id}_{case_id}",
        case_id=case_id,
        run_id=run_id,
        subject_id=subject_id,
        failure_type=failure_type,
        severity=severity,
        evidence=evidence,
        suggested_action=suggested_action,
        confidence=confidence,
        related_metrics=related_metrics,
        related_artifacts=related_artifacts or [],
    )


def diagnose_case(
    *,
    case_id: str,
    run_id: str,
    subject_id: str,
    metric_values: dict[str, float],
    artifact_values: dict,
    comparison_values: dict,
) -> DiagnosisResult:
    common = {"case_id": case_id, "run_id": run_id, "subject_id": subject_id}

    # 1) 评估材料问题（最上游）
    if artifact_values.get("material_invalid"):
        return _result(
            **common,
            failure_type="evaluation_material_issue",
            severity="high",
            evidence=["material_invalid=True"],
            suggested_action="检查评估材料：gold/expected_evidence/confirmed 状态或 Judge 失败。",
            confidence=0.7,
            related_metrics=list(metric_values),
        )

    if artifact_values.get("retrieval_errors"):
        errors = [str(e) for e in artifact_values["retrieval_errors"]]
        return _result(
            **common,
            failure_type="retrieval_call_error",
            severity="high",
            evidence=errors[:3],
            suggested_action="检索接口调用失败：检查后端进程网络权限、范式接口地址、端口和代理配置。",
            confidence=0.95,
            related_metrics=list(metric_values),
        )

    hit = metric_values.get("retrieval.hit_at_k")
    mrr = metric_values.get("retrieval.mrr")

    # 2) 检索完全没召回
    if hit == 0.0:
        return _result(
            **common,
            failure_type="retrieval_miss",
            severity="medium",
            evidence=["retrieval.hit_at_k=0"],
            suggested_action="检查召回、索引覆盖和 query 改写。",
            confidence=0.8,
            related_metrics=["retrieval.hit_at_k"],
        )

    # 3) 召回到但排名过低
    if hit and hit > 0 and mrr is not None and 0 < mrr < _RANKING_MRR_THRESHOLD:
        return _result(
            **common,
            failure_type="retrieval_ranking_issue",
            severity="medium",
            evidence=[f"retrieval.mrr={mrr}"],
            suggested_action="正确证据已召回但排名过低，检查重排与排序策略。",
            confidence=0.7,
            related_metrics=["retrieval.hit_at_k", "retrieval.mrr"],
        )

    # 4) 答案存在未被证据支持的 claim
    if metric_values.get("e2e.faithfulness", 1.0) < 1.0 and artifact_values.get("unsupported_claims"):
        claims = artifact_values["unsupported_claims"]
        return _result(
            **common,
            failure_type="unsupported_claim",
            severity="high",
            evidence=[f"unsupported_claims={','.join(claims)}"],
            suggested_action="检查生成 grounding 和证据约束。",
            confidence=0.75,
            related_metrics=["e2e.faithfulness"],
            related_artifacts=claims,
        )

    # 5) 拒答判定错误
    if metric_values.get("e2e.refusal_accuracy") == 0.0:
        return _result(
            **common,
            failure_type="refusal_error",
            severity="high",
            evidence=["e2e.refusal_accuracy=0"],
            suggested_action="检查拒答策略与 answerability 判定。",
            confidence=0.75,
            related_metrics=["e2e.refusal_accuracy"],
        )

    # 6) 版本对比退化（下游）
    if comparison_values.get("change_type") in {"regressed", "newly_failed"}:
        return _result(
            **common,
            failure_type="comparison_regression",
            severity="high",
            evidence=[f"change_type={comparison_values['change_type']}"],
            suggested_action="检查修改后版本导致退化的检索证据包或答案变化。",
            confidence=0.8,
            related_metrics=list(metric_values),
        )

    # 7) 信息不足，无法稳定归因
    return _result(
        **common,
        failure_type="ambiguous",
        severity="low",
        evidence=[],
        suggested_action="补充 Trace、Artifact 或人工复核信息后重新诊断。",
        confidence=0.3,
        related_metrics=list(metric_values),
    )
