"""N3 单测：ontology_induction 纯逻辑（无需 DB/LLM）。

验证 _build_type_candidates：
- 把 LLM 归纳结果按规范名对回确认实体，换成 entity_id；
- 大小写/空白不敏感匹配 members；
- DF（成员文档支持度）< min_df 的类型被去噪丢弃；
- members 全对不上 / type_name 空 → 不产候选。
"""
from __future__ import annotations

from knowledge_mining.mining.stages.ontology_induction import _build_type_candidates


def _ent(eid: str, name: str, *, docs: int, mentions: int) -> dict:
    return {"id": eid, "canonical_name": name, "document_count": docs,
            "mention_count": mentions, "members": []}


def test_members_mapped_to_ids_and_kept() -> None:
    entities = [
        _ent("e1", "网络切片", docs=2, mentions=5),
        _ent("e2", "切片实例", docs=1, mentions=2),
    ]
    llm = {"new_types": [{
        "type_name": "network_slice", "definition": "逻辑网络切片",
        "examples": ["网络切片"], "members": ["网络切片", "切片实例"], "layer": "concept",
    }]}
    cands = _build_type_candidates(entities, llm, min_df=2)
    assert len(cands) == 1
    c = cands[0]
    assert c.type_name == "network_slice"
    assert set(c.member_entity_ids) == {"e1", "e2"}
    assert c.df == 3          # 2 + 1
    assert c.support == 7     # 5 + 2
    assert c.layer == "concept"


def test_case_and_space_insensitive_match() -> None:
    entities = [_ent("e1", "UPF", docs=2, mentions=3)]
    llm = {"new_types": [{"type_name": "func", "members": ["  upf "]}]}
    cands = _build_type_candidates(entities, llm, min_df=2)
    assert len(cands) == 1
    assert cands[0].member_entity_ids == ["e1"]


def test_low_df_type_denoised() -> None:
    # 单文档单提及的孤立概念 → DF=1 < 2，丢弃
    entities = [_ent("e1", "某私有部件", docs=1, mentions=1)]
    llm = {"new_types": [{"type_name": "vendor_widget", "members": ["某私有部件"]}]}
    assert _build_type_candidates(entities, llm, min_df=2) == []


def test_unmatched_members_drop_type() -> None:
    entities = [_ent("e1", "网络切片", docs=2, mentions=5)]
    llm = {"new_types": [{"type_name": "ghost", "members": ["不存在的实体"]}]}
    assert _build_type_candidates(entities, llm, min_df=1) == []


def test_blank_type_name_skipped() -> None:
    entities = [_ent("e1", "网络切片", docs=2, mentions=5)]
    llm = {"new_types": [{"type_name": "  ", "members": ["网络切片"]}]}
    assert _build_type_candidates(entities, llm, min_df=1) == []


def test_empty_llm_result() -> None:
    entities = [_ent("e1", "网络切片", docs=2, mentions=5)]
    assert _build_type_candidates(entities, {}, min_df=1) == []


def test_members_carry_structured_evidence() -> None:
    # payload.members 要带 entity_id/name/篇数/次数/原文摘录，供评审页小表展示
    ent = {
        "id": "e1", "canonical_name": "网络切片",
        "document_count": 2, "mention_count": 5,
        "members": [{"quote": "网络切片是一种逻辑上的专用网络"}],
    }
    llm = {"new_types": [{"type_name": "network_slice", "members": ["网络切片"]}]}
    cands = _build_type_candidates([ent], llm, min_df=2)
    assert len(cands) == 1
    m = cands[0].members[0]
    assert m["entity_id"] == "e1"
    assert m["name"] == "网络切片"
    assert m["document_count"] == 2
    assert m["mention_count"] == 5
    assert "网络切片" in m["quote"]
