"""Markdown reports.

报告只消费已有结果，不重新判分、不调用 Judge / Adapter（架构第 10 节）。指标表对
None / 字符串 / 数值都做防御式格式化（决策 10）。
"""

from __future__ import annotations

from typing import Any


def _format_value(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return str(value)


def _metric_table(metrics: dict[str, Any]) -> str:
    lines = ["| Metric | Value |", "|---|---:|"]
    for key, value in sorted(metrics.items()):
        lines.append(f"| {key} | {_format_value(value)} |")
    return "\n".join(lines)


def render_run_markdown(
    *, subject_id: str, dataset_id: str, metrics: dict[str, Any], failures: list[dict]
) -> str:
    lines = [
        "# Single Run Evaluation Report",
        "",
        f"- Subject: `{subject_id}`",
        f"- Dataset: `{dataset_id}`",
        "",
        "## Metrics",
        "",
        _metric_table(metrics),
        "",
        "## Failure Cases",
        "",
    ]
    if not failures:
        lines.append("No failed cases.")
    else:
        lines.extend(["| Case | Failure Type |", "|---|---|"])
        for item in failures:
            lines.append(f"| {item['case_id']} | {item['failure_type']} |")
    return "\n".join(lines)


def render_comparison_markdown(
    *,
    baseline_run_id: str,
    candidate_run_id: str,
    summary: dict[str, Any],
    regressed_cases: list[dict],
) -> str:
    lines = [
        "# Comparison Evaluation Report",
        "",
        f"- Baseline Run: `{baseline_run_id}`",
        f"- Candidate Run: `{candidate_run_id}`",
        "",
        "## Summary",
        "",
        _metric_table(summary),
        "",
        "## Regressed Cases",
        "",
    ]
    if not regressed_cases:
        lines.append("No regressed cases.")
    else:
        lines.extend(["| Case | Failure Type |", "|---|---|"])
        for item in regressed_cases:
            lines.append(f"| {item['case_id']} | {item['failure_type']} |")
    return "\n".join(lines)
