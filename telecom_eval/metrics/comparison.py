"""Comparison metrics (架构第 8 节).

样本级变化分类 + 总体胜率/退化率，用于「修改后是否真的更好」。规则可算，不需要 LLM。
"""

from __future__ import annotations


def _change_type(before: float, after: float) -> str:
    if before < 1.0 and after >= 1.0:
        return "newly_passed"
    if before >= 1.0 and after < 1.0:
        return "newly_failed"
    if after > before:
        return "improved"
    if after < before:
        return "regressed"
    return "unchanged"


def compare_case_scores(before: dict[str, float], after: dict[str, float]) -> dict[str, dict]:
    common = sorted(set(before).intersection(after))
    rows: dict[str, dict] = {}
    for case_id in common:
        before_value = before[case_id]
        after_value = after[case_id]
        rows[case_id] = {
            "before": before_value,
            "after": after_value,
            "absolute_delta": after_value - before_value,
            "relative_delta": None
            if before_value == 0
            else (after_value - before_value) / before_value,
            "change_type": _change_type(before_value, after_value),
        }
    return rows


def summarize_comparison(rows: dict[str, dict]) -> dict[str, float | int]:
    total = len(rows)
    if total == 0:
        return {"comparable_cases": 0, "win_rate": 0.0, "regression_rate": 0.0}
    wins = sum(1 for row in rows.values() if row["change_type"] in {"improved", "newly_passed"})
    regressions = sum(
        1 for row in rows.values() if row["change_type"] in {"regressed", "newly_failed"}
    )
    return {
        "comparable_cases": total,
        "win_rate": wins / total,
        "regression_rate": regressions / total,
        "newly_passed_count": sum(
            1 for row in rows.values() if row["change_type"] == "newly_passed"
        ),
        "newly_failed_count": sum(
            1 for row in rows.values() if row["change_type"] == "newly_failed"
        ),
    }
