"""Judge cache-key scheduling helpers.

cache_key 必须在 rubric / provider / model / answer / evidence / expected_key_points
任一变化时改变（方案 5.3）。展示样式、分组维度变化不影响 key。
"""

from __future__ import annotations

import hashlib
import json

from telecom_eval.judges.tasks import JudgeTask


def _sha(*parts: str) -> str:
    joined = "␟".join(parts)  # 用不可见分隔符避免字段拼接歧义
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def build_cache_key(task: JudgeTask, provider: str, model: str) -> str:
    ni = task.normalized_input or {}
    answer_hash = _sha(str(ni.get("answer", "")))
    evidence_hash = _sha(_canonical(ni.get("evidence", [])))
    key_points_hash = _sha(_canonical(ni.get("expected_key_points", [])))
    return _sha(
        task.task_type,
        task.case_id or "",
        answer_hash,
        evidence_hash,
        key_points_hash,
        task.rubric_version,
        provider,
        model,
    )
