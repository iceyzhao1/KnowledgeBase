"""Judge prompt builders."""

from __future__ import annotations

import json

from telecom_eval.judges.tasks import JudgeTask

_SYSTEM_BY_TYPE = {
    "claim_extraction": "你是评估系统的断言抽取器。把答案拆成原子断言，只输出 JSON。",
    "evidence_alignment": "你是评估系统的证据对齐判分器。判断每条断言是否被证据支持，只输出 JSON。",
    "gold_alignment": "你是评估系统的要点覆盖判分器。判断答案是否覆盖标准要点，只输出 JSON。",
    "answer_correctness": "你是评估系统的答案正确性判分器，只输出 JSON。",
    "retrieval_content_support": (
        "你是检索评估裁判。判断检索证据是否支持标准证据点，只输出 JSON："
        '{"support_label":"supported|partial|not_supported","score":1|0.5|0,"reason":"一句话原因"}。'
    ),
    "report_summary": "你是评估报告摘要器，只输出 JSON，且不得修改任何分数。",
}


def build_judge_prompt(task: JudgeTask) -> tuple[str, str]:
    system = _SYSTEM_BY_TYPE.get(task.task_type, "你是评估判分器，只输出 JSON。")
    user_payload = {"task_type": task.task_type, "case_id": task.case_id}
    user_payload.update(task.normalized_input or {})
    user = json.dumps(user_payload, ensure_ascii=False, sort_keys=True)
    return system, user
