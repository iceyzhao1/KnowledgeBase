"""DebugService — assemble the CaseDebugViewModel, the UI debug backbone.

把某个 case 的 case/trace/metric/artifact/diagnosis/judge 调用/raw JSON 全拉出来，
让任何低分都能从 UI 一路下钻到证据、对齐、判分调用（方案 8.4）。只读，不重判分。
"""

from __future__ import annotations

from telecom_eval.api.schemas import CaseDebugViewModel


class DebugService:
    def __init__(self, store):
        self.store = store

    def build_case_debug(self, run_id: str, case_id: str) -> CaseDebugViewModel:
        case = self.store.load_case(case_id)
        case_dict = case.model_dump() if case else {}

        traces = self.store.list_case_traces(run_id, case_id)
        retrieval_trace = next((t["payload"] for t in traces if t["trace_type"] == "retrieval"), None)
        answer_trace = next((t["payload"] for t in traces if t["trace_type"] == "answer"), None)

        metrics = [m.model_dump() for m in self.store.list_run_metrics(run_id, case_id)]
        artifacts = self.store.list_case_artifacts(run_id, case_id)
        diagnoses = self.store.list_case_diagnoses(run_id, case_id)
        diagnosis = diagnoses[0]["payload"] if diagnoses else None

        evidence_alignment: list[dict] = []
        retrieval_content_judgment: dict | None = None
        for artifact in artifacts:
            if artifact["artifact_type"] == "evidence_alignment":
                evidence_alignment = artifact["payload"].get("payload", {}).get("alignments", [])
            if artifact["artifact_type"] == "retrieval_content_judgment":
                retrieval_content_judgment = artifact["payload"].get("payload", {})

        judge_invocations = self.store.list_judge_invocations(run_id=run_id, case_id=case_id)

        warnings: list[dict] = []
        errors: list[str] = []
        if retrieval_trace:
            warnings = retrieval_trace.get("output", {}).get("warnings", []) or []
            errors.extend(retrieval_trace.get("errors", []) or [])
        if answer_trace:
            errors.extend(answer_trace.get("errors", []) or [])

        return CaseDebugViewModel(
            case=case_dict,
            retrieval_trace=retrieval_trace,
            answer_trace=answer_trace,
            metrics=metrics,
            artifacts=artifacts,
            diagnosis=diagnosis,
            evidence_alignment=evidence_alignment,
            retrieval_content_judgment=retrieval_content_judgment,
            expected_evidence=case_dict.get("expected_evidence", []),
            judge_invocations=judge_invocations,
            warnings=warnings,
            errors=errors,
            raw={
                "traces": traces,
                "artifacts": artifacts,
                "metrics": metrics,
                "diagnoses": diagnoses,
                "judge_invocations": judge_invocations,
            },
        )
