"""Local markdown-backed retrieval subject for smoke tests.

This is a temporary/offline substitute for the real paradigm HTTP service. It treats markdown
blocks as mock rows from ``asset_raw_segments`` and exposes both retrieval and segment resolving.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from telecom_eval.models.cases import EvaluationCase
from telecom_eval.models.traces import (
    EvidenceItem,
    RetrievalInput,
    RetrievalOutput,
    RetrievalTrace,
)


@dataclass(frozen=True)
class LocalSegment:
    segment_id: str
    content: str
    document_key: str
    segment_index: int


class LocalMarkdownSegmentStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.document_key = f"doc:/{self.path.name}"
        self._segments = self._load_segments()
        self._by_id = {segment.segment_id: segment for segment in self._segments}

    def _load_segments(self) -> list[LocalSegment]:
        text = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
        segments = []
        for index, block in enumerate(blocks):
            digest = hashlib.md5(f"{self.path}:{index}:{block}".encode("utf-8")).hexdigest()[:12]
            segments.append(
                LocalSegment(
                    segment_id=f"local_seg_{digest}",
                    content=block,
                    document_key=self.document_key,
                    segment_index=index,
                )
            )
        return segments

    def resolve(self, segment_id: str) -> str | None:
        segment = self._by_id.get(segment_id)
        return segment.content if segment else None

    def search(self, query: str, *, top_k: int = 20, hints: list[str] | None = None) -> list[LocalSegment]:
        terms = _terms(" ".join([query, *(hints or [])]))
        ranked = sorted(
            self._segments,
            key=lambda segment: _score(terms, segment.content),
            reverse=True,
        )
        return ranked[:top_k]


def _terms(text: str) -> set[str]:
    return {term.lower() for term in re.findall(r"[\w+\-/]+", text) if term.strip()}


def _score(query_terms: set[str], content: str) -> float:
    content_norm = content.lower()
    term_score = sum(1 for term in query_terms if term in content_norm)
    # Keep original order stable but put obviously relevant SDN/network sections first.
    bonus = 0.0
    for marker in ("非sdn", "hsrp", "mstp", "eth-trunk", "svf", "m-lag", "logical router", "ospf"):
        if marker in content_norm:
            bonus += 0.2
    return term_score + bonus


class LocalMarkdownRetrievalAdapter:
    def __init__(self, subject_id: str, store: LocalMarkdownSegmentStore):
        self.subject_id = subject_id
        self.store = store

    def retrieve(self, case: EvaluationCase, *, run_id: str) -> RetrievalTrace:
        hints = [
            *case.expected_evidence_contains,
            *case.expected_key_points,
            *[str(gold.get("match_text", "")) for gold in case.expected_evidence],
        ]
        segments = self.store.search(case.question, top_k=20, hints=hints)
        evidence = [
            EvidenceItem(
                evidence_id=segment.segment_id,
                observed_item_id=segment.segment_id,
                content=segment.content,
                source_id=segment.document_key,
                source_type="raw_segment",
                rank=rank,
                score=None,
                provenance={
                    "raw_segment_ids": [segment.segment_id],
                    "document_key": segment.document_key,
                    "segment_index": segment.segment_index,
                    "mock_source": str(self.store.path),
                },
            )
            for rank, segment in enumerate(segments, start=1)
        ]
        return RetrievalTrace(
            trace_id=f"trace_ret_{run_id}_{case.case_id}",
            case_id=case.case_id,
            run_id=run_id,
            subject_id=self.subject_id,
            input=RetrievalInput(query=case.question, scenario_id=case.scenario_id, filters=case.filters),
            output=RetrievalOutput(evidence_package=evidence),
            latency_ms=0,
            optional_debug_trace={"mode": "local_mock", "corpus_path": str(self.store.path)},
        )
