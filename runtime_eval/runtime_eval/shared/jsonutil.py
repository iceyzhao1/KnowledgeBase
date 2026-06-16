"""Robust JSON extraction from LLM output (handles ```json fences / prose)."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> Any:
    """Best-effort parse of a JSON object/array embedded in model output."""

    if text is None:
        raise ValueError("empty LLM output")
    stripped = text.strip()

    # 1) fenced block ```json ... ```
    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            stripped = candidate

    # 2) direct parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 3) first balanced { ... } or [ ... ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = stripped.find(opener)
        end = stripped.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"无法从模型输出解析 JSON：{text[:200]!r}")
