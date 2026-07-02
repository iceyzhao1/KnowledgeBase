"""Evidence identity keys used to match observed evidence against gold.

决策 5：检索证据的身份是多层的。真实检索 API 里 ``items[].id`` 可能随 build 漂移
（尤其 ``unit_type == "generated_question"``），不能作为长期 gold 主键。匹配优先级：

1. ``raw_segment:<raw_segment_id>`` —— 来自 ``citation.raw_segment_ids`` /
   ``metadata.source_segment_id``，跨 build 稳定，默认主键。
2. ``doc_segment:<document_key>#<segment_index>`` —— 文档级定位。
3. ``observed_item:<items[].id>`` —— 仅用于显式按索引版本匹配。

这些 key 在观测侧（EvidenceItem）和期望侧（gold dict / RetrievalTarget）用同一套
函数生成，保证两边可比。
"""

from __future__ import annotations

from typing import Any


def raw_segment_key(seg_id: Any) -> str:
    return f"raw_segment:{seg_id}"


def doc_segment_key(document_key: Any, segment_index: Any) -> str:
    return f"doc_segment:{document_key}#{segment_index}"


def observed_item_key(item_id: Any) -> str:
    return f"observed_item:{item_id}"


def _collect_raw_segment_ids(provenance: dict[str, Any]) -> list[str]:
    ids: list[str] = []

    def _add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            ids.extend(str(item) for item in value if item is not None)
        else:
            ids.append(str(value))

    _add(provenance.get("raw_segment_ids"))
    _add(provenance.get("source_segment_id"))
    citation = provenance.get("citation")
    if isinstance(citation, dict):
        _add(citation.get("raw_segment_ids"))
    return ids


def build_match_keys(
    *,
    evidence_id: Any | None = None,
    observed_item_id: Any | None = None,
    provenance: dict[str, Any] | None = None,
) -> list[str]:
    """Build the ordered, de-duplicated set of match keys for one evidence/gold item."""
    provenance = provenance or {}
    keys: list[str] = []

    for seg_id in _collect_raw_segment_ids(provenance):
        keys.append(raw_segment_key(seg_id))

    document_key = provenance.get("document_key")
    segment_index = provenance.get("segment_index")
    if document_key is not None and segment_index is not None:
        keys.append(doc_segment_key(document_key, segment_index))

    if observed_item_id:
        keys.append(observed_item_key(observed_item_id))

    if evidence_id:
        # 裸 evidence_id 作为兜底/显式 gold id，兼容只给 evidence_id 的轻量样本。
        keys.append(str(evidence_id))

    seen: set[str] = set()
    ordered: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered
