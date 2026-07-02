"""Map a real retrieval-service ``contextPack`` response into a RetrievalTrace.

参考样例 ``data/query_result/result.txt``（UTF-8）。这是第一版的 fixture mapper（决策 11）；
真实 HTTP adapter 待 API 规格给出后基于同样的映射规则补齐。

证据身份按决策 5 取稳定 key：``evidence_id`` 优先 raw segment id，``observed_item_id`` 保留
原始 ``items[].id``。检索侧 ``contextPack.issues`` 落为 RetrievalWarning（决策 6），不是拒答。
"""

from __future__ import annotations

import json
from typing import Any

from telecom_eval.models.traces import (
    EvidenceItem,
    RetrievalInput,
    RetrievalOutput,
    RetrievalTrace,
    RetrievalWarning,
)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _map_item(raw: dict[str, Any], rank: int) -> EvidenceItem:
    citation = raw.get("citation") or {}
    metadata = raw.get("metadata") or {}
    source_refs = raw.get("sourceRefs") or _json_dict(metadata.get("source_refs_json"))
    target_ref = _json_dict(metadata.get("target_ref_json"))
    facets = _json_dict(metadata.get("facets_json"))
    raw_segment_ids = citation.get("raw_segment_ids") or source_refs.get("raw_segment_ids") or []
    source_segment_id = metadata.get("source_segment_id")

    if raw_segment_ids:
        evidence_id = str(raw_segment_ids[0])
    elif source_segment_id:
        evidence_id = str(source_segment_id)
    else:
        evidence_id = str(raw.get("id"))

    provenance = {
        "document_key": source_refs.get("document_key") or target_ref.get("document_key"),
        "segment_index": source_refs.get("segment_index") or target_ref.get("segment_index"),
        "raw_segment_ids": raw_segment_ids,
        "source_segment_id": source_segment_id,
        "document_snapshot_id": metadata.get("document_snapshot_id"),
        "citation": citation,
        "score_chain": raw.get("scoreChain"),
        "retrieval_source": raw.get("source"),
        "target_ref": target_ref,
        "facets": facets,
    }
    source_type = raw.get("kind") or metadata.get("target_type") or metadata.get("unit_type")
    return EvidenceItem(
        evidence_id=evidence_id,
        observed_item_id=raw.get("id"),
        content=raw.get("text") or metadata.get("text") or "",
        title=raw.get("title") or metadata.get("title"),
        source_id=str(
            source_refs.get("document_key")
            or target_ref.get("document_key")
            or raw.get("sourceId")
            or ""
        ),
        source_type=str(source_type or "retrieval_unit"),
        rank=rank,
        role=raw.get("role"),
        kind=raw.get("kind") or metadata.get("unit_type"),
        score=raw.get("score"),
        metadata=metadata,
        provenance=provenance,
    )


def map_retrieval_response(
    response: dict[str, Any],
    *,
    case_id: str,
    run_id: str,
    subject_id: str,
    original_query: str | None = None,
    domain: str | None = None,
) -> RetrievalTrace:
    # 兼容两种响应形态：
    #   1) 包裹式：{"contextPack": {items/query/issues...}, "trace", "domain"}（result.txt 样例）
    #   2) 扁平式：{items/query/issues...} 直接在顶层（真实 /api/v1/search 返回）
    context_pack = response.get("contextPack")
    if not isinstance(context_pack, dict):
        context_pack = response
    query = context_pack.get("query") or {}
    items = context_pack.get("items") or context_pack.get("candidates") or []
    issues = context_pack.get("issues") or []
    trace_nodes = response.get("trace") or context_pack.get("trace") or []

    evidence_package = [_map_item(raw, rank) for rank, raw in enumerate(items, start=1)]
    warnings = [
        RetrievalWarning(
            type=str(issue.get("type", "unknown")),
            message=str(issue.get("message", "")),
            detail=issue.get("detail") or {},
        )
        for issue in issues
    ]
    latency_ms = sum(int(node.get("durationMs", 0)) for node in trace_nodes)

    return RetrievalTrace(
        trace_id=f"trace_ret_{run_id}_{case_id}",
        case_id=case_id,
        run_id=run_id,
        subject_id=subject_id,
        input=RetrievalInput(
            query=str(original_query or query.get("original", "")),
            scenario_id=str(domain or response.get("domain", "")),
            top_k=len(items) or None,
        ),
        output=RetrievalOutput(
            normalized_query=query.get("normalized"),
            evidence_package=evidence_package,
            warnings=warnings,
        ),
        latency_ms=latency_ms,
        optional_debug_trace={"trace": trace_nodes},
    )
