"""EvaluationService — orchestrates a run and persists everything to SQLite.

编排 v1 核心：Runner(经 Adapter) → Trace → 规则 Artifact → Metric → Diagnosis → Report，
全部落 SQLite。LLM 判分只在 allow_llm_judge 时经 JudgeService（唯一入口）触发。
默认用确定性 Fake Adapter，离线可跑；真实 HTTP adapter 待 API 规格补齐后注入。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from telecom_eval.artifacts.builders import (
    build_atomic_claims,
    build_evidence_alignment,
    build_gold_alignment,
)
from telecom_eval.api.schemas import CreateRunRequest
from telecom_eval.diagnosis.engine import diagnose_case
from telecom_eval.judges.budget import JudgeBudgetUsage
from telecom_eval.judges.tasks import JudgeTask
from telecom_eval.metrics.e2e import (
    answer_correctness_score,
    citation_accuracy,
    faithfulness,
    key_point_coverage,
    refusal_accuracy,
)
from telecom_eval.metrics.content_judgment import (
    build_retrieval_content_judgment,
    content_judgment_hash,
)
from telecom_eval.metrics.layered_retrieval import (
    gold_evidence_similarity_at_k,
    gold_phrase_coverage_at_k,
    segment_resolve_rate,
)
from telecom_eval.models.artifacts import EvaluationArtifact
from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.metrics import MetricResult
from telecom_eval.reports.report import render_run_markdown
from telecom_eval.runners.runner import EvaluationRunner
from telecom_eval.subjects.fake import FakeAnswerAdapter, FakeRetrievalAdapter

_RETRIEVAL_LEVEL = "retrieval"
_E2E_LEVEL = "e2e"
_EFFICIENCY_LEVEL = "efficiency"
_E2E_METRIC_IDS = (
    "e2e.key_point_coverage",
    "e2e.faithfulness",
    "e2e.citation_accuracy",
    "e2e.refusal_accuracy",
    "e2e.answer_correctness",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metric_level(metric_id: str) -> str:
    if metric_id.startswith("retrieval.latency"):
        return _EFFICIENCY_LEVEL
    if metric_id.startswith("retrieval."):
        return _RETRIEVAL_LEVEL
    if metric_id.startswith("e2e."):
        return _E2E_LEVEL
    return _EFFICIENCY_LEVEL


def _coerce_support_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score >= 0.75:
        return 1.0
    if score >= 0.25:
        return 0.5
    return 0.0


def _support_label_from_score(score: float) -> str:
    if score >= 1.0:
        return "supported"
    if score >= 0.5:
        return "partial"
    return "not_supported"


class EvaluationService:
    def __init__(
        self,
        store,
        *,
        judge_service=None,
        judge_service_factory=None,
        retrieval_adapter=None,
        answer_adapter=None,
        retrieval_adapter_factory=None,
        answer_adapter_factory=None,
        segment_resolver=None,
    ):
        self.store = store
        self.judge_service = judge_service
        self._judge_service_factory = judge_service_factory
        self._retrieval_adapter = retrieval_adapter
        self._answer_adapter = answer_adapter
        # 工厂按 subject_id 产生 adapter（真实 HTTP adapter 需要每个 run 的 subject_id）。
        self._retrieval_adapter_factory = retrieval_adapter_factory
        self._answer_adapter_factory = answer_adapter_factory
        self._segment_resolver = segment_resolver

    # ------------------------------------------------------------------
    def create_run(self, request: CreateRunRequest, *, execute_immediately: bool = True) -> dict:
        run_id = "run_" + uuid.uuid4().hex[:12]
        created_at = _now()

        # 可复现：用已有 snapshot，或自动建一个 confirmed-only snapshot（架构 5.1.2）。
        if request.dataset_snapshot_id:
            snapshot = self.store.load_dataset_snapshot(request.dataset_snapshot_id)
            if snapshot is None:
                raise KeyError(f"dataset_snapshot not found: {request.dataset_snapshot_id}")
        else:
            snapshot = self.store.create_dataset_snapshot(
                request.dataset_id, confirmed_only=request.confirmed_only
            )

        snapshot_case_ids = set(snapshot.case_ids)
        cases = [
            c
            for c in self.store.list_cases(dataset_id=request.dataset_id)
            if c.case_id in snapshot_case_ids
        ]
        run_payload = {
            "run_id": run_id,
            "subject_id": request.subject_id,
            "subject_search_path": request.subject_search_path,
            "answer_model_id": request.answer_model_id,
            "judge_model_id": request.judge_model_id,
            "same_answer_and_judge_model": bool(
                request.answer_model_id and request.judge_model_id and request.answer_model_id == request.judge_model_id
            ),
            "dataset_id": request.dataset_id,
            "dataset_snapshot_id": snapshot.dataset_snapshot_id,
            "dataset_version": snapshot.dataset_version,
            "eval_type": request.eval_type,
            "status": "queued",
            "allow_llm_judge": request.allow_llm_judge,
            "request": request.model_dump(),
            "metric_suite_ids": request.metric_suite_ids,
            "scenario_id": cases[0].scenario_id if cases else None,
            "top_k": request.top_k,
            "confirmed_only": request.confirmed_only,
            "case_count": len(cases),
            "case_ids": [c.case_id for c in cases],
            "created_at": created_at,
        }
        self.store.save_run(run_payload)
        if not execute_immediately:
            return self.get_run_summary(run_id)
        return self.execute_queued_run(run_id)

    def execute_queued_run(self, run_id: str) -> dict:
        payload = self.store.load_run_payload(run_id)
        request = CreateRunRequest.model_validate(payload.get("request") or payload)
        snapshot_id = payload.get("dataset_snapshot_id")
        if not snapshot_id:
            raise KeyError(f"run has no dataset snapshot: {run_id}")
        snapshot = self.store.load_dataset_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"dataset_snapshot not found: {snapshot_id}")
        snapshot_case_ids = set(snapshot.case_ids)
        cases = [
            c
            for c in self.store.list_cases(dataset_id=request.dataset_id)
            if c.case_id in snapshot_case_ids
        ]
        self.store.update_run_status(run_id, "running", started_at=_now())
        try:
            self._execute(run_id, request, cases)
        except Exception:
            self.store.update_run_status(run_id, "failed", completed_at=_now())
            raise
        self.store.update_run_status(run_id, "completed", completed_at=_now())
        return self.get_run_summary(run_id)

    def get_run_summary(self, run_id: str) -> dict:
        payload = self.store.load_run_payload(run_id)
        return {
            "run_id": payload["run_id"],
            "subject_id": payload.get("subject_id", ""),
            "dataset_id": payload.get("dataset_id", ""),
            "dataset_snapshot_id": payload.get("dataset_snapshot_id"),
            "eval_type": payload.get("eval_type", "mixed"),
            "status": payload.get("status", "unknown"),
            "allow_llm_judge": bool(payload.get("allow_llm_judge")),
            "created_at": payload.get("created_at", ""),
            "started_at": payload.get("started_at"),
            "completed_at": payload.get("completed_at"),
        }

    # ------------------------------------------------------------------
    def _retrieval(self, subject_id: str, request: CreateRunRequest | None = None):
        if self._retrieval_adapter is not None:
            return self._retrieval_adapter
        if self._retrieval_adapter_factory is not None:
            return self._retrieval_adapter_factory(subject_id, request)
        return FakeRetrievalAdapter(subject_id=subject_id)

    def _answer(self, subject_id: str, request: CreateRunRequest | None = None):
        if self._answer_adapter is not None:
            return self._answer_adapter
        if self._answer_adapter_factory is not None:
            return self._answer_adapter_factory(subject_id, request)
        return FakeAnswerAdapter(subject_id=subject_id)

    def _judge(self, request: CreateRunRequest):
        if self._judge_service_factory is not None and request.judge_model_id:
            return self._judge_service_factory(request.judge_model_id)
        return self.judge_service

    def _has_real_answer_adapter(self) -> bool:
        return self._answer_adapter is not None or self._answer_adapter_factory is not None

    def _execute(self, run_id: str, request: CreateRunRequest, cases: list[EvaluationCase]) -> None:
        subject_id = request.subject_id
        top_k = request.top_k
        do_retrieval = request.eval_type in ("retrieval", "mixed")
        do_e2e = request.eval_type in ("e2e", "mixed")
        runner = EvaluationRunner(run_id=run_id, subject_id=subject_id)
        retrieval_adapter = self._retrieval(subject_id, request)
        answer_adapter = self._answer(subject_id, request) if do_e2e and self._has_real_answer_adapter() else None
        judge_service = self._judge(request)

        budget = request.judge_budget.model_copy()
        if request.allow_llm_judge:
            budget.allow_llm_judge = True
        usage = JudgeBudgetUsage()

        per_metric_values: dict[str, list[float]] = {}

        for case in cases:
            if not case.is_formal():
                continue
            metric_values: dict[str, float] = {}
            artifact_values: dict[str, Any] = {}
            retrieved_evidence_package = []

            if do_retrieval:
                rtrace = retrieval_adapter.retrieve(case, run_id=run_id)
                self.store.save_trace(rtrace)
                if rtrace.errors:
                    artifact_values["retrieval_errors"] = list(rtrace.errors)
                ep = rtrace.output.evidence_package
                retrieved_evidence_package = list(ep)
                content_judgment = build_retrieval_content_judgment(
                    case,
                    ep,
                    k=top_k,
                    resolver=self._segment_resolver,
                        support_judge=self._retrieval_support_judge(
                            run_id=run_id,
                            case=case,
                            subject_id=subject_id,
                            judge_service=judge_service,
                            budget=budget,
                            usage=usage,
                    ),
                )
                judgment_artifact = EvaluationArtifact(
                    artifact_id=f"art_retrieval_content_judgment_{run_id}_{case.case_id}",
                    artifact_type="retrieval_content_judgment",
                    case_id=case.case_id,
                    run_id=run_id,
                    subject_id=subject_id,
                    trace_id=rtrace.trace_id,
                    inputs_hash=content_judgment_hash(
                        {
                            "case_id": case.case_id,
                            "question": case.question,
                            "expected_evidence": case.expected_evidence,
                            "expected_key_points": case.expected_key_points,
                            "evidence_package": [item.model_dump() for item in ep],
                            "top_k": top_k,
                        }
                    ),
                    payload=content_judgment,
                    lineage={"source": "retrieval_trace", "trace_id": rtrace.trace_id},
                    created_at=_now(),
                )
                self.store.save_artifact(
                    judgment_artifact,
                    artifact_type="retrieval_content_judgment",
                    run_id=run_id,
                    case_id=case.case_id,
                    cache_key=judgment_artifact.inputs_hash,
                )
                retrieval_metrics = {
                    "retrieval.hit_at_k": content_judgment["metrics"]["retrieval.hit_at_k"],
                    "retrieval.recall_at_k": content_judgment["metrics"]["retrieval.recall_at_k"],
                    "retrieval.mrr": content_judgment["metrics"]["retrieval.mrr"],
                    "retrieval.evidence_coverage": content_judgment["metrics"]["retrieval.evidence_coverage"],
                    "retrieval.latency": float(rtrace.latency_ms),
                    "retrieval.segment_resolve_rate": segment_resolve_rate(
                        ep, resolver=self._segment_resolver
                    ),
                    "retrieval.gold_phrase_coverage_at_k": gold_phrase_coverage_at_k(
                        case, ep, k=top_k, resolver=self._segment_resolver
                    ),
                    "retrieval.gold_evidence_similarity_at_k": gold_evidence_similarity_at_k(
                        case, ep, k=top_k, resolver=self._segment_resolver
                    ),
                }
                for metric_id, value in retrieval_metrics.items():
                    self._save_metric(run_id, case.case_id, subject_id, metric_id, value)
                    metric_values[metric_id] = value
                    per_metric_values.setdefault(metric_id, []).append(value)

            if do_e2e and answer_adapter is None:
                self._save_e2e_inconclusive_without_answer_adapter(run_id, case.case_id, subject_id)

            if do_e2e and answer_adapter is not None:
                atrace = self._answer_case(
                    answer_adapter,
                    case,
                    run_id=run_id,
                    evidence_package=retrieved_evidence_package,
                )
                self.store.save_trace(atrace)
                claims = build_atomic_claims(
                    case.case_id, run_id, subject_id, atrace.trace_id, atrace.output.final_answer
                )
                ev_align = build_evidence_alignment(
                    case.case_id, run_id, subject_id, atrace.trace_id, claims, atrace.output.evidence_package
                )
                gold_align = build_gold_alignment(
                    case.case_id, run_id, subject_id, atrace.trace_id, claims, case
                )
                ev_align = self._enhance_evidence_alignment(
                    run_id=run_id,
                    case=case,
                    subject_id=subject_id,
                    trace_id=atrace.trace_id,
                    claims=claims,
                    evidence_package=atrace.output.evidence_package,
                    alignment=ev_align,
                    budget=budget,
                    usage=usage,
                    judge_service=judge_service,
                )
                gold_align = self._enhance_gold_alignment(
                    run_id=run_id,
                    case=case,
                    subject_id=subject_id,
                    trace_id=atrace.trace_id,
                    claims=claims,
                    alignment=gold_align,
                    budget=budget,
                    usage=usage,
                    judge_service=judge_service,
                )
                self.store.save_artifact(
                    claims, artifact_type="atomic_claims", run_id=run_id, case_id=case.case_id, cache_key=claims.inputs_hash
                )
                self.store.save_artifact(
                    ev_align, artifact_type="evidence_alignment", run_id=run_id, case_id=case.case_id, cache_key=ev_align.inputs_hash
                )
                self.store.save_artifact(
                    gold_align, artifact_type="gold_alignment", run_id=run_id, case_id=case.case_id, cache_key=gold_align.inputs_hash
                )
                e2e_metrics = {
                    "e2e.key_point_coverage": key_point_coverage(gold_align),
                    "e2e.faithfulness": faithfulness(claims, ev_align),
                    "e2e.citation_accuracy": citation_accuracy(atrace.output, ev_align),
                    "e2e.refusal_accuracy": refusal_accuracy(case, atrace.output.refusal),
                }
                for metric_id, value in e2e_metrics.items():
                    self._save_metric(run_id, case.case_id, subject_id, metric_id, value)
                    metric_values[metric_id] = value
                    per_metric_values.setdefault(metric_id, []).append(value)

                correctness = self._judge_answer_correctness(
                    run_id=run_id,
                    case=case,
                    subject_id=subject_id,
                    answer=atrace.output.final_answer,
                    evidence=[e.content for e in atrace.output.evidence_package],
                    evidence_alignment=ev_align.payload.get("alignments", []),
                    gold_alignment=gold_align.payload.get("alignments", []),
                    budget=budget,
                    usage=usage,
                    judge_service=judge_service,
                )
                if correctness is not None:
                    value, details, status = correctness
                    self._save_metric(
                        run_id,
                        case.case_id,
                        subject_id,
                        "e2e.answer_correctness",
                        value,
                        status=status,
                        details=details,
                    )
                    if status == "ok":
                        metric_values["e2e.answer_correctness"] = float(value)
                        per_metric_values.setdefault("e2e.answer_correctness", []).append(float(value))

                artifact_values["unsupported_claims"] = [
                    a["claim_id"]
                    for a in ev_align.payload["alignments"]
                    if a["support_label"] != "supported"
                ]

            diagnosis = diagnose_case(
                case_id=case.case_id,
                run_id=run_id,
                subject_id=subject_id,
                metric_values=metric_values,
                artifact_values=artifact_values,
                comparison_values={},
            )
            if diagnosis.failure_type != "ambiguous":
                self.store.save_diagnosis(diagnosis)

        self._finalize(run_id, subject_id, per_metric_values, request)

    def _save_metric(
        self,
        run_id: str,
        case_id: str | None,
        subject_id: str,
        metric_id: str,
        value: Any,
        *,
        status: str = "ok",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.store.save_metric_result(
            MetricResult(
                metric_id=metric_id,
                case_id=case_id,
                run_id=run_id,
                subject_id=subject_id,
                level=_metric_level(metric_id),
                value=value,
                status=status,
                details=details or {},
            )
        )

    def _save_e2e_inconclusive_without_answer_adapter(self, run_id: str, case_id: str, subject_id: str) -> None:
        details = {
            "reason": "real_answer_adapter_missing",
            "message": "未接入真实回答接口，不能用离线 FakeAnswerAdapter 计算端到端回答指标。",
        }
        for metric_id in _E2E_METRIC_IDS:
            self._save_metric(
                run_id,
                case_id,
                subject_id,
                metric_id,
                None,
                status="inconclusive",
                details=details,
            )

    def _answer_case(self, answer_adapter, case: EvaluationCase, *, run_id: str, evidence_package):
        try:
            return answer_adapter.answer(case, run_id=run_id, evidence_package=evidence_package)
        except TypeError as exc:
            if "evidence_package" not in str(exc):
                raise
            return answer_adapter.answer(case, run_id=run_id)

    def _retrieval_support_judge(
        self,
        *,
        run_id: str,
        case: EvaluationCase,
        subject_id: str,
        judge_service,
        budget,
        usage: JudgeBudgetUsage,
    ):
        if not budget.allow_llm_judge or judge_service is None:
            return None

        def judge(gold: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
            task = JudgeTask(
                task_id=f"jt_{run_id}_{case.case_id}_{gold['gold_id']}_retrieval_content",
                task_type="retrieval_content_support",
                case_id=case.case_id,
                run_id=run_id,
                subject_id=subject_id,
                normalized_input={
                    "question": case.question,
                    "gold_evidence_point": {
                        "gold_id": gold.get("gold_id"),
                        "text": gold.get("text"),
                        "phrases": gold.get("phrases", []),
                    },
                    "retrieved_candidates": [
                        {
                            "rank": candidate.get("rank"),
                            "content_preview": candidate.get("content_preview"),
                            "rule_score": candidate.get("score"),
                            "rule_reason": candidate.get("reason"),
                        }
                        for candidate in candidates
                    ],
                },
                input_hash="",
                rubric_version="retrieval_content_v1",
                prompt_version="retrieval_content_prompt_v1",
                judge_type="llm",
                judge_model_version="unbound",
                output_schema="retrieval_content_support_v1",
            )
            artifact = judge_service.run(task, budget=budget, usage=usage)
            if artifact.status != "ok":
                return {
                    "support_label": "partial",
                    "score": 0.5,
                    "llm_status": artifact.status,
                    "reason": artifact.reason or "大模型复核未完成，保留规则部分支持判断。",
                }
            result = artifact.result or {}
            score = _coerce_support_score(result.get("score"))
            return {
                "support_label": str(result.get("support_label") or _support_label_from_score(score)),
                "score": score,
                "llm_status": artifact.status,
                "reason": str(result.get("reason") or "大模型复核完成。"),
            }

        return judge

    def _enhance_evidence_alignment(
        self,
        *,
        run_id: str,
        case: EvaluationCase,
        subject_id: str,
        trace_id: str,
        claims: EvaluationArtifact,
        evidence_package,
        alignment: EvaluationArtifact,
        budget,
        usage: JudgeBudgetUsage,
        judge_service,
    ) -> EvaluationArtifact:
        if not budget.allow_llm_judge or judge_service is None:
            return alignment
        claim_by_id = {claim["claim_id"]: claim for claim in claims.payload.get("claims", [])}
        evidence_brief = [
            {"evidence_id": item.evidence_id, "content": item.content[:600], "rank": item.rank}
            for item in evidence_package
        ]
        updated = []
        for item in alignment.payload.get("alignments", []):
            current = dict(item)
            if current.get("support_label") == "supported":
                updated.append(current)
                continue
            claim = claim_by_id.get(str(current.get("claim_id")), {})
            task = JudgeTask(
                task_id=f"jt_{run_id}_{case.case_id}_{current.get('claim_id')}_evidence_alignment",
                task_type="evidence_alignment",
                case_id=case.case_id,
                run_id=run_id,
                subject_id=subject_id,
                normalized_input={
                    "question": case.question,
                    "claim": claim,
                    "retrieved_evidence": evidence_brief,
                    "rule_alignment": current,
                },
                input_hash="",
                rubric_version="evidence_alignment_v1",
                prompt_version="evidence_alignment_prompt_v1",
                judge_type="llm",
                judge_model_version="unbound",
                output_schema="evidence_alignment_v1",
            )
            artifact = judge_service.run(task, budget=budget, usage=usage)
            self.store.save_artifact(
                artifact,
                artifact_type="evidence_alignment_judgment",
                run_id=run_id,
                case_id=case.case_id,
                cache_key=artifact.judge_cache_key,
            )
            current["llm_status"] = artifact.status
            if artifact.status == "ok":
                result = artifact.result or {}
                current["method"] = "llm"
                current["support_label"] = str(result.get("support_label") or current.get("support_label"))
                current["evidence_id"] = result.get("best_evidence_id") or current.get("evidence_id")
                current["score"] = result.get("score")
                current["rationale"] = str(result.get("reason") or current.get("rationale") or "")
            updated.append(current)
        alignment.payload["alignments"] = updated
        alignment.payload["llm_enhanced"] = True
        return alignment

    def _enhance_gold_alignment(
        self,
        *,
        run_id: str,
        case: EvaluationCase,
        subject_id: str,
        trace_id: str,
        claims: EvaluationArtifact,
        alignment: EvaluationArtifact,
        budget,
        usage: JudgeBudgetUsage,
        judge_service,
    ) -> EvaluationArtifact:
        if not budget.allow_llm_judge or judge_service is None:
            return alignment
        updated = []
        for item in alignment.payload.get("alignments", []):
            current = dict(item)
            if current.get("coverage_label") == "covered":
                updated.append(current)
                continue
            task = JudgeTask(
                task_id=f"jt_{run_id}_{case.case_id}_{current.get('gold_id')}_gold_alignment",
                task_type="gold_alignment",
                case_id=case.case_id,
                run_id=run_id,
                subject_id=subject_id,
                normalized_input={
                    "question": case.question,
                    "expected_answer": case.expected_answer,
                    "expected_key_points": case.expected_key_points,
                    "claims": claims.payload.get("claims", []),
                    "rule_alignment": current,
                },
                input_hash="",
                rubric_version="gold_alignment_v1",
                prompt_version="gold_alignment_prompt_v1",
                judge_type="llm",
                judge_model_version="unbound",
                output_schema="gold_alignment_v1",
            )
            artifact = judge_service.run(task, budget=budget, usage=usage)
            self.store.save_artifact(
                artifact,
                artifact_type="gold_alignment_judgment",
                run_id=run_id,
                case_id=case.case_id,
                cache_key=artifact.judge_cache_key,
            )
            current["llm_status"] = artifact.status
            if artifact.status == "ok":
                result = artifact.result or {}
                current["method"] = "llm"
                current["coverage_label"] = str(result.get("coverage_label") or current.get("coverage_label"))
                current["claim_ids"] = result.get("matched_claim_ids") or current.get("claim_ids", [])
                current["score"] = result.get("score")
                current["rationale"] = str(result.get("reason") or current.get("rationale") or "")
            updated.append(current)
        alignment.payload["alignments"] = updated
        alignment.payload["llm_enhanced"] = True
        return alignment

    def _judge_answer_correctness(
        self,
        *,
        run_id: str,
        case: EvaluationCase,
        subject_id: str,
        answer: str,
        evidence: list[str],
        evidence_alignment: list[dict[str, Any]],
        gold_alignment: list[dict[str, Any]],
        budget,
        usage: JudgeBudgetUsage,
        judge_service,
    ) -> tuple[float | None, dict[str, Any], str] | None:
        if not budget.allow_llm_judge or judge_service is None:
            return None
        task = JudgeTask(
            task_id=f"jt_{run_id}_{case.case_id}_answer_correctness",
            task_type="answer_correctness",
            case_id=case.case_id,
            run_id=run_id,
            subject_id=subject_id,
            normalized_input={
                "question": case.question,
                "answer": answer,
                "expected_answer": case.expected_answer,
                "expected_key_points": case.expected_key_points,
                "expected_evidence": case.expected_evidence,
                "evidence": evidence,
                "evidence_alignment": evidence_alignment,
                "gold_alignment": gold_alignment,
            },
            input_hash="",
            rubric_version="answer_correctness_v1",
            prompt_version="answer_correctness_prompt_v1",
            judge_type="llm",
            judge_model_version="unbound",
            output_schema="answer_correctness_v1",
        )
        artifact = judge_service.run(task, budget=budget, usage=usage)
        self.store.save_artifact(
            artifact,
            artifact_type="answer_correctness_judgment",
            run_id=run_id,
            case_id=case.case_id,
            cache_key=artifact.judge_cache_key,
        )
        details = {
            "judge_status": artifact.status,
            "reason": artifact.reason,
            "result": artifact.result,
        }
        if artifact.status != "ok":
            return None, details, "inconclusive"
        return answer_correctness_score(artifact.result or {}), details, "ok"

    def _finalize(
        self,
        run_id: str,
        subject_id: str,
        per_metric_values: dict[str, list[float]],
        request: CreateRunRequest,
    ) -> None:
        # 聚合：每个指标取均值，存为 run 级 MetricResult（case_id=None）。
        aggregates: dict[str, float] = {}
        for metric_id, values in per_metric_values.items():
            avg = sum(values) / len(values) if values else 0.0
            aggregates[metric_id] = avg
            self._save_metric(run_id, None, subject_id, metric_id, avg)

        self._save_inconclusive_aggregate_metrics(run_id, subject_id, aggregates)

        # 效率：缓存命中率 / LLM 调用次数（从审计表算）。
        invocations = self.store.list_judge_invocations(run_id=run_id)
        llm_calls = sum(1 for inv in invocations if inv["status"] == "ok")
        aggregates["efficiency.llm_call_count"] = float(llm_calls)
        self._save_metric(run_id, None, subject_id, "efficiency.llm_call_count", float(llm_calls))

        # 报告（消费已存诊断，不重判分）。
        diagnoses = self.store.list_run_diagnoses(run_id)
        failures = [
            {"case_id": d["case_id"], "failure_type": d["failure_type"], "severity": d["severity"]}
            for d in diagnoses
        ]
        markdown = render_run_markdown(
            subject_id=subject_id,
            dataset_id=request.dataset_id,
            metrics=aggregates,
            failures=failures,
        )
        self.store.save_report(
            report_id=f"report_{run_id}",
            run_id=run_id,
            report_type="run",
            payload={"aggregates": aggregates, "failures": failures},
            markdown=markdown,
        )

    def _save_inconclusive_aggregate_metrics(
        self,
        run_id: str,
        subject_id: str,
        aggregates: dict[str, float],
    ) -> None:
        by_metric: dict[str, list[MetricResult]] = {}
        for metric in self.store.list_run_metrics(run_id):
            if metric.case_id is None or metric.metric_id in aggregates:
                continue
            by_metric.setdefault(metric.metric_id, []).append(metric)

        for metric_id, metrics in by_metric.items():
            statuses = {metric.status for metric in metrics}
            if statuses and statuses <= {"inconclusive", "missing_inputs", "not_applicable"}:
                status = "inconclusive" if "inconclusive" in statuses else sorted(statuses)[0]
                self._save_metric(
                    run_id,
                    None,
                    subject_id,
                    metric_id,
                    None,
                    status=status,
                    details={
                        "reason": "all_case_metrics_inconclusive",
                        "case_count": len(metrics),
                    },
                )
