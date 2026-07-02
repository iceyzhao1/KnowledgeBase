"""ReportViewService — assemble run/comparison report view models from SQLite.

只消费已存 metrics / diagnoses / judge_invocations / reports，不重判分（架构 10.3）。
"""

from __future__ import annotations

from telecom_eval.api.schemas import ComparisonReportViewModel, RunReportViewModel
from telecom_eval.metrics.comparison import compare_case_scores, summarize_comparison


class ReportViewService:
    def __init__(self, store):
        self.store = store

    def build_run_report(self, run_id: str) -> RunReportViewModel:
        run = self.store.load_run_payload(run_id)
        all_metrics = self.store.list_run_metrics(run_id)
        aggregate_metrics = [m for m in all_metrics if m.case_id is None]
        case_ids = {m.case_id for m in all_metrics if m.case_id is not None}
        case_count = int(run.get("case_count") or len(case_ids))

        metric_summary: dict[str, dict] = {}
        metrics_out: list[dict] = []
        metric_cases: dict[str, list[dict]] = {}
        for metric in aggregate_metrics:
            row = {
                "metric_id": metric.metric_id,
                "level": metric.level,
                "value": metric.value,
                "status": metric.status,
            }
            metrics_out.append(row)
            metric_summary.setdefault(metric.level, {})[metric.metric_id] = metric.value
        for metric in all_metrics:
            if metric.case_id is None:
                continue
            metric_cases.setdefault(metric.metric_id, []).append(
                {
                    "case_id": metric.case_id,
                    "metric_id": metric.metric_id,
                    "level": metric.level,
                    "value": metric.value,
                    "status": metric.status,
                }
            )
        for rows in metric_cases.values():
            rows.sort(key=_case_metric_sort_key)

        diagnoses = self.store.list_run_diagnoses(run_id)
        failures = [
            {
                "case_id": d["case_id"],
                "failure_type": d["failure_type"],
                "severity": d["severity"],
            }
            for d in diagnoses
        ]

        invocations = self.store.list_judge_invocations(run_id=run_id)
        judge_usage = {
            "total_invocations": len(invocations),
            "ok_calls": sum(1 for i in invocations if i["status"] == "ok"),
            "skipped_calls": sum(1 for i in invocations if i["status"] == "skipped"),
            "total_tokens": sum(int(i.get("total_tokens", 0)) for i in invocations),
            "cases_with_llm": len({i["case_id"] for i in invocations if i["status"] == "ok" and i["case_id"]}),
        }

        report = self.store.load_report(run_id, "run")
        markdown = report["markdown"] if report else ""

        return RunReportViewModel(
            run=run,
            metrics=metrics_out,
            metric_cases=metric_cases,
            metric_summary=metric_summary,
            failures=failures,
            judge_usage=judge_usage,
            case_count=case_count,
            markdown=markdown,
        )

    def build_comparison_report(self, comparison_id: str) -> ComparisonReportViewModel | None:
        record = self.store.load_comparison(comparison_id)
        if record is None:
            return None
        payload = record["payload"]
        return ComparisonReportViewModel(
            comparison_id=comparison_id,
            baseline_run_id=record["baseline_run_id"],
            candidate_run_id=record["candidate_run_id"],
            status=record["status"],
            summary=payload.get("summary", {}),
            delta_rows=payload.get("delta_rows", []),
            regressed_cases=payload.get("regressed_cases", []),
            newly_passed=payload.get("newly_passed", []),
            newly_failed=payload.get("newly_failed", []),
            baseline_snapshot_id=payload.get("baseline_snapshot_id"),
            candidate_snapshot_id=payload.get("candidate_snapshot_id"),
            snapshot_match=payload.get("snapshot_match", False),
            snapshot_warning=payload.get("snapshot_warning"),
        )

    def create_comparison(
        self, *, baseline_run_id: str, candidate_run_id: str, metric_id: str, comparison_type: str
    ) -> ComparisonReportViewModel:
        before = self._case_scores(baseline_run_id, metric_id)
        after = self._case_scores(candidate_run_id, metric_id)
        rows = compare_case_scores(before, after)
        summary = summarize_comparison(rows)

        delta_rows = [
            {"case_id": case_id, **row} for case_id, row in sorted(rows.items())
        ]
        regressed_cases = [
            {"case_id": case_id, "failure_type": "comparison_regression", **row}
            for case_id, row in rows.items()
            if row["change_type"] in ("regressed", "newly_failed")
        ]
        newly_passed = [cid for cid, row in rows.items() if row["change_type"] == "newly_passed"]
        newly_failed = [cid for cid, row in rows.items() if row["change_type"] == "newly_failed"]

        # 公平对比前提：两个 run 必须基于同一 dataset_snapshot（架构 5.1.2）。
        baseline_snapshot = self._run_snapshot_id(baseline_run_id)
        candidate_snapshot = self._run_snapshot_id(candidate_run_id)
        snapshot_match = bool(
            baseline_snapshot and candidate_snapshot and baseline_snapshot == candidate_snapshot
        )
        snapshot_warning = (
            None
            if snapshot_match
            else "测试集不一致：两个 run 使用了不同的 dataset_snapshot，结果不可作为公平回归结论。"
        )

        comparison_id = f"cmp_{baseline_run_id}_{candidate_run_id}_{metric_id}"
        payload = {
            "metric_id": metric_id,
            "summary": summary,
            "delta_rows": delta_rows,
            "regressed_cases": regressed_cases,
            "newly_passed": newly_passed,
            "newly_failed": newly_failed,
            "baseline_snapshot_id": baseline_snapshot,
            "candidate_snapshot_id": candidate_snapshot,
            "snapshot_match": snapshot_match,
            "snapshot_warning": snapshot_warning,
        }
        self.store.save_comparison(
            comparison_id=comparison_id,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            status="completed",
            payload=payload,
        )
        return ComparisonReportViewModel(
            comparison_id=comparison_id,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            status="completed",
            summary=summary,
            delta_rows=delta_rows,
            regressed_cases=regressed_cases,
            newly_passed=newly_passed,
            newly_failed=newly_failed,
            baseline_snapshot_id=baseline_snapshot,
            candidate_snapshot_id=candidate_snapshot,
            snapshot_match=snapshot_match,
            snapshot_warning=snapshot_warning,
        )

    def _run_snapshot_id(self, run_id: str) -> str | None:
        try:
            payload = self.store.load_run_payload(run_id)
        except KeyError:
            return None
        return payload.get("dataset_snapshot_id")

    def _case_scores(self, run_id: str, metric_id: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for metric in self.store.list_run_metrics(run_id):
            if metric.case_id and metric.metric_id == metric_id:
                try:
                    scores[metric.case_id] = float(metric.value)
                except (TypeError, ValueError):
                    continue
        return scores


def _case_metric_sort_key(row: dict) -> tuple[int, float, str]:
    try:
        value = float(row.get("value"))
    except (TypeError, ValueError):
        return (1, 0.0, str(row.get("case_id") or ""))
    return (0, value, str(row.get("case_id") or ""))
