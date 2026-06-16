"""Import an externally-authored test suite (YAML/JSON) into a TestSuite.

The framework normally generates cases via eval-llm, but operators may already
have curated golden sets. This adapter maps a few common field names onto the
framework's :class:`TestCase` model so those suites can be judged as-is.

Accepted top-level shapes::

    {questions: [...]}    # the curated-corpus convention
    {cases: [...]}        # the framework's own export
    [ ... ]               # a bare list of case dicts

Per-case field mapping (first present wins)::

    id              <- id | case_id            (else auto qN)
    question        <- question | query
    question_type   <- question_type | type    (coerced; see QuestionType.coerce)
    expected_answer <- expected_answer | answer
    key_points      <- key_points
                       (else expected_evidence_contains + expected_entities)
    expected_evidence <- expected_evidence | expected_evidence_contains  (retrieval gold)
    expected_entities <- expected_entities                              (retrieval gold)
    source.section  <- source_section | source.section
    source.doc      <- source.doc | <doc_ref arg>
    difficulty      <- difficulty
    notes           <- notes
"""

from __future__ import annotations

import json
from typing import Any

from ..shared.models import QuestionType, RetrievedItem, SourceRef, TestCase

try:  # PyYAML is optional; JSON imports work without it.
    import yaml
except ImportError:  # pragma: no cover - exercised only when yaml absent
    yaml = None


class ImportError_(ValueError):
    """Raised when an uploaded suite file cannot be parsed/mapped."""


def _load_raw(name: str, data: bytes) -> Any:
    text = data.decode("utf-8", errors="replace")
    is_json = name.lower().endswith(".json") or text.lstrip().startswith(("{", "["))
    if is_json:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportError_(f"JSON 解析失败：{exc}") from exc
    if yaml is None:
        raise ImportError_(
            "解析 YAML 需要 PyYAML，请 `pip install pyyaml`，或改上传 .json。"
        )
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:  # type: ignore[union-attr]
        raise ImportError_(f"YAML 解析失败：{exc}") from exc


def _items(doc: Any) -> list[dict]:
    if isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        items = doc.get("questions") or doc.get("cases") or []
    else:
        items = []
    if not items:
        raise ImportError_("文件中未找到用例（期望顶层 questions / cases 列表）。")
    if not all(isinstance(it, dict) for it in items):
        raise ImportError_("用例列表中存在非对象元素。")
    return items


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _key_points(raw: dict) -> list[str]:
    explicit = raw.get("key_points")
    if explicit:
        return [str(x) for x in explicit]
    points: list[str] = []
    for field in ("expected_evidence_contains", "expected_entities"):
        for x in raw.get(field, []) or []:
            s = str(x).strip()
            if s and s not in points:
                points.append(s)
    return points


def _str_list(raw: dict, *keys: str) -> list[str]:
    """First present key whose value is a non-empty list -> list[str] (deduped)."""
    for k in keys:
        val = raw.get(k)
        if val:
            out: list[str] = []
            for x in val:
                s = str(x).strip()
                if s and s not in out:
                    out.append(s)
            if out:
                return out
    return []


def _source(raw: dict, doc_ref: str) -> SourceRef:
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    section = _first(raw, "source_section") or src.get("section")
    quote = src.get("quote")
    if not quote:
        ev = raw.get("expected_evidence_contains") or []
        quote = str(ev[0]) if ev else None
    return SourceRef(doc=src.get("doc") or doc_ref, section=section, quote=quote)


def parse_suite_file(*, name: str, data: bytes, doc_ref: str) -> list[TestCase]:
    """Parse an uploaded YAML/JSON suite into ``TestCase`` objects.

    Raises ``ImportError_`` with an actionable message on any problem.
    """

    doc = _load_raw(name, data)
    items = _items(doc)
    cases: list[TestCase] = []
    seen: set[str] = set()
    for idx, raw in enumerate(items, start=1):
        question = _first(raw, "question", "query")
        if not question:
            raise ImportError_(f"第 {idx} 条缺少 question。")
        qt_raw = _first(raw, "question_type", "type")
        if not qt_raw:
            raise ImportError_(f"第 {idx} 条缺少 question_type。")
        try:
            qtype = QuestionType.coerce(str(qt_raw))
        except ValueError as exc:
            raise ImportError_(f"第 {idx} 条 {exc}") from exc
        expected = _first(raw, "expected_answer", "answer")
        if not expected:
            raise ImportError_(f"第 {idx} 条缺少 expected_answer。")

        cid = str(_first(raw, "id", "case_id") or f"q{idx:03d}")
        if cid in seen:
            raise ImportError_(f"重复的用例 id：{cid}")
        seen.add(cid)

        cases.append(
            TestCase(
                id=cid,
                question=str(question),
                question_type=qtype,
                expected_answer=str(expected),
                key_points=_key_points(raw),
                expected_evidence=_str_list(
                    raw, "expected_evidence", "expected_evidence_contains"
                ),
                expected_entities=_str_list(raw, "expected_entities"),
                source=_source(raw, doc_ref),
                difficulty=str(_first(raw, "difficulty") or "normal"),
                notes=_first(raw, "notes"),
            )
        )
    return cases


def _coerce_items(value: Any) -> list[RetrievedItem]:
    """A per-case value -> ranked RetrievedItem list.

    Each entry may be a plain string (text only) or a dict with
    ``rank``/``text``/``source``. Rank defaults to 1-based position.
    """

    if not isinstance(value, list):
        return []
    out: list[RetrievedItem] = []
    for i, entry in enumerate(value, start=1):
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                out.append(RetrievedItem(rank=i, text=text))
        elif isinstance(entry, dict):
            text = str(entry.get("text") or entry.get("content") or "").strip()
            if not text:
                continue
            try:
                rank = int(entry.get("rank", i))
            except (TypeError, ValueError):
                rank = i
            source = entry.get("source")
            out.append(
                RetrievedItem(rank=rank, text=text, source=str(source) if source else None)
            )
    return out


def parse_retrieval_file(*, name: str, data: bytes) -> tuple[str, dict[str, list[RetrievedItem]]]:
    """Parse an uploaded retrieved-evidence file into (agent_name, items-by-case).

    Accepted shapes::

        {agent_name?, items: {case_id: [item, ...]}}
        {case_id: [item, ...]}                    # bare mapping
        [{case_id, items: [...]}, ...]            # list of per-case objects

    where each ``item`` is a string or {rank?, text, source?}. Raises
    ``ImportError_`` with an actionable message on any problem.
    """

    doc = _load_raw(name, data)
    agent_name = "retriever"
    mapping: Any
    if isinstance(doc, dict) and isinstance(doc.get("items"), dict):
        agent_name = str(doc.get("agent_name") or agent_name)
        mapping = doc["items"]
    elif isinstance(doc, dict):
        mapping = doc
    elif isinstance(doc, list):
        mapping = {}
        for entry in doc:
            if not isinstance(entry, dict):
                raise ImportError_("检索结果列表中存在非对象元素。")
            cid = entry.get("case_id") or entry.get("id")
            if not cid:
                raise ImportError_("列表元素缺少 case_id。")
            mapping[str(cid)] = entry.get("items") or entry.get("retrieved") or []
    else:
        raise ImportError_("无法识别的检索结果文件结构。")

    items: dict[str, list[RetrievedItem]] = {}
    for cid, value in mapping.items():
        parsed = _coerce_items(value)
        if parsed:
            items[str(cid)] = parsed
    if not items:
        raise ImportError_("未解析到任何检索条目（每个用例需提供 text 列表）。")
    return agent_name, items
