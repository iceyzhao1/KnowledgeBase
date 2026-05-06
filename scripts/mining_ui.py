"""Gradio UI for running the knowledge_mining pipeline.

启动方式：
    py -3.10 scripts/mining_ui.py
然后浏览器打开 http://127.0.0.1:7860

功能：
    1. 上传若干文档（.md/.txt/.html/.pdf/.docx/.chm/.hdx）
       - .chm/.hdx 会在 ingest 阶段自动解压并转成 markdown
    2. 填写 batch 参数（产品、标签、文档类型）
    3. 可选启用 LLM / Embedding
    4. 点击"开始挖掘" → 后端线程跑 run()，前端轮询 PostgreSQL (kb_db) 实时显示阶段
    5. 完成后每个阶段都展示：统计图表 + 全量数据表格
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_no_proxy = os.environ.get("NO_PROXY", "")
for _h in ("localhost", "127.0.0.1", "::1"):
    if _h not in _no_proxy:
        _no_proxy = (_no_proxy + "," + _h).strip(",")
os.environ["NO_PROXY"] = _no_proxy
os.environ["no_proxy"] = _no_proxy

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import gradio as gr  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import ConnectionPool  # noqa: E402

from knowledge_mining.mining.jobs.run import run as mining_run  # noqa: E402
from knowledge_mining.mining.contracts.models import BatchParams  # noqa: E402
from knowledge_mining.mining.infra.pg_config import MiningDbConfig  # noqa: E402

UPLOADS_ROOT = PROJECT_ROOT / "data" / "uploads"
DOMAIN_PACKS_ROOT = PROJECT_ROOT / "knowledge_mining" / "domain_packs"

UPLOADS_ROOT.mkdir(parents=True, exist_ok=True)


def _list_domain_packs() -> list[str]:
    """Discover domain pack ids by listing folders that contain domain.yaml."""
    if not DOMAIN_PACKS_ROOT.is_dir():
        return ["cloud_core_network"]
    ids = sorted(
        p.name for p in DOMAIN_PACKS_ROOT.iterdir()
        if p.is_dir() and (p / "domain.yaml").is_file()
    )
    return ids or ["cloud_core_network"]


# =====================================================================
# DB helpers (PostgreSQL via shared connection pool)
# =====================================================================

_pg_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pg_pool
    if _pg_pool is None:
        cfg = MiningDbConfig()
        _pg_pool = ConnectionPool(
            cfg.conninfo,
            min_size=1,
            max_size=4,
            open=True,
            kwargs={"row_factory": dict_row},
        )
    return _pg_pool


class _PGConn:
    """Thin wrapper around a pooled psycopg connection.

    Mimics the sqlite3.Connection.execute(...).fetchall() API used throughout
    this UI, so render functions don't need to switch to cursor context managers.
    Call .close() to return the connection to the pool.
    """

    def __init__(self, pool: ConnectionPool):
        self._pool = pool
        self._cm = pool.connection()
        self._conn = self._cm.__enter__()

    def execute(self, sql: str, params: tuple | list | None = None) -> "_PGCursor":
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        return _PGCursor(cur)

    def close(self) -> None:
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass


class _PGCursor:
    """Wraps a psycopg cursor so .fetchone()/.fetchall() can be chained
    after .execute() — the calling pattern used throughout this UI."""

    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        try:
            return self._cur.fetchone()
        finally:
            self._cur.close()

    def fetchall(self):
        try:
            return self._cur.fetchall()
        finally:
            self._cur.close()


def _open_asset() -> _PGConn:
    return _PGConn(_get_pool())


def _open_runtime() -> _PGConn:
    return _PGConn(_get_pool())


# =====================================================================
# Polling helpers (during run)
# =====================================================================

def _query_latest_run(input_path: str) -> dict | None:
    conn = _open_runtime()
    try:
        row = conn.execute(
            "SELECT * FROM mining_runs WHERE input_path = %s ORDER BY started_at DESC LIMIT 1",
            (input_path,),
        ).fetchone()
        if not row:
            return None
        events = conn.execute(
            "SELECT stage, status, error_message, created_at FROM mining_run_stage_events "
            "WHERE run_id = %s ORDER BY created_at DESC LIMIT 1",
            (row["id"],),
        ).fetchall()
        latest_event = dict(events[0]) if events else None
        return {"run": dict(row), "latest_event": latest_event}
    finally:
        conn.close()


def _truncate(s: str | None, n: int = 200) -> str:
    if s is None:
        return ""
    s = str(s).replace("\r", " ").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "..."


def _empty_counts_df(key: str) -> pd.DataFrame:
    return pd.DataFrame({key: [], "count": []})


def _counts_df(rows: list, key: str, *, fallback_label: str = "(空)") -> pd.DataFrame:
    if not rows:
        return _empty_counts_df(key)
    return pd.DataFrame(
        [{key: (r[key] if r[key] is not None else fallback_label), "count": r["c"]} for r in rows]
    )


def _bin_token(t: int | None) -> str:
    if t is None:
        return "null"
    if t < 50:   return "<50"
    if t < 100:  return "50-99"
    if t < 200:  return "100-199"
    if t < 500:  return "200-499"
    if t < 1000: return "500-999"
    return "1000+"


_BIN_ORDER = ["<50", "50-99", "100-199", "200-499", "500-999", "1000+", "null"]


def _bin_distance(d: int | None) -> str:
    if d is None:
        return "null"
    if d == 0:    return "0"
    if d == 1:    return "1"
    if d <= 3:    return "2-3"
    if d <= 5:    return "4-5"
    if d <= 10:   return "6-10"
    return "11+"


_DIST_ORDER = ["0", "1", "2-3", "4-5", "6-10", "11+", "null"]


def _bin_text_len(n: int) -> str:
    if n < 50:    return "<50"
    if n < 100:   return "50-99"
    if n < 200:   return "100-199"
    if n < 500:   return "200-499"
    if n < 1000:  return "500-999"
    if n < 2000:  return "1000-1999"
    return "2000+"


_TXTLEN_ORDER = ["<50", "50-99", "100-199", "200-499", "500-999", "1000-1999", "2000+"]


def _ordered_bin_df(counter: dict[str, int], order: list[str], key: str) -> pd.DataFrame:
    rows = [{key: b, "count": counter.get(b, 0)} for b in order if b in counter or counter.get(b, 0)]
    if not rows:
        return _empty_counts_df(key)
    return pd.DataFrame(rows)


# =====================================================================
# Per-stage data renderers
# Each renderer returns a list of values matching its components in builder.
# =====================================================================

EMPTY_RUN_TEXT = "_（尚未运行）_"
RUNNING_TEXT = "_（运行中…完成后展示）_"


def _snapshot_ids(rt: _PGConn, run_id: str) -> tuple[str, ...]:
    rows = rt.execute(
        "SELECT document_snapshot_id FROM mining_run_documents "
        "WHERE run_id = %s AND document_snapshot_id IS NOT NULL",
        (run_id,),
    ).fetchall()
    return tuple(r["document_snapshot_id"] for r in rows)


# ---------- Stage 1: Ingest ----------

def render_ingest(run_id: str) -> list[Any]:
    rt = _open_runtime()
    try:
        rows = rt.execute(
            "SELECT document_key, action, status, raw_content_hash, normalized_content_hash, error_message "
            "FROM mining_run_documents WHERE run_id = %s ORDER BY document_key",
            (run_id,),
        ).fetchall()
        action_rows = rt.execute(
            "SELECT action, COUNT(*) AS c FROM mining_run_documents WHERE run_id = %s GROUP BY action",
            (run_id,),
        ).fetchall()
        status_rows = rt.execute(
            "SELECT status, COUNT(*) AS c FROM mining_run_documents WHERE run_id = %s GROUP BY status",
            (run_id,),
        ).fetchall()
    finally:
        rt.close()

    if not rows:
        return [EMPTY_RUN_TEXT, _empty_counts_df("action"), _empty_counts_df("status"), pd.DataFrame()]

    action_dist = {r["action"] or "(空)": r["c"] for r in action_rows}
    summary = (
        f"### 摄取统计\n\n"
        f"- 共发现 **{len(rows)}** 个文档\n"
        f"- 按 action：{', '.join(f'**{k}**={v}' for k, v in action_dist.items())}\n"
        f"- 失败数：{sum(1 for r in rows if r['status'] == 'failed')}"
    )
    table = pd.DataFrame(
        [
            {
                "document_key": r["document_key"],
                "action": r["action"],
                "status": r["status"],
                "raw_hash": (r["raw_content_hash"] or "")[:12],
                "normalized_hash": (r["normalized_content_hash"] or "")[:12],
                "error_message": _truncate(r["error_message"], 200),
            }
            for r in rows
        ]
    )
    return [summary, _counts_df(action_rows, "action"), _counts_df(status_rows, "status"), table]


# ---------- Stage 2: Parse ----------

def render_parse(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        rd_rows = rt.execute(
            "SELECT document_id, document_snapshot_id, document_key FROM mining_run_documents "
            "WHERE run_id = %s AND document_snapshot_id IS NOT NULL",
            (run_id,),
        ).fetchall()
        if not rd_rows:
            return [EMPTY_RUN_TEXT, _empty_counts_df("doc"), _empty_counts_df("depth"), pd.DataFrame()]

        doc_section_counts: list[dict] = []
        depth_counter: dict[str, int] = {}
        section_rows: list[dict] = []

        for rd in rd_rows:
            snap_id = rd["document_snapshot_id"]
            doc_key = rd["document_key"]
            doc_short = doc_key.rsplit("/", 1)[-1][:40]

            sec_rows = asset.execute(
                "SELECT section_path, section_title, COUNT(*) AS c "
                "FROM asset_raw_segments WHERE document_snapshot_id = %s "
                "GROUP BY section_path, section_title ORDER BY MIN(segment_index)",
                (snap_id,),
            ).fetchall()
            doc_section_counts.append({"doc": doc_short, "count": len(sec_rows)})
            for r in sec_rows:
                try:
                    path = json.loads(r["section_path"]) if r["section_path"] else []
                    depth = len(path)
                except Exception:
                    depth = 0
                depth_key = f"L{depth}"
                depth_counter[depth_key] = depth_counter.get(depth_key, 0) + 1
                section_rows.append(
                    {
                        "doc": doc_short,
                        "depth": depth,
                        "section_path": _truncate(" / ".join(map(str, path)) if path else "(根)", 120),
                        "section_title": _truncate(r["section_title"], 80),
                        "segment_count": r["c"],
                    }
                )

        total_sections = sum(d["count"] for d in doc_section_counts)
        max_depth = max((s["depth"] for s in section_rows), default=0)
        summary = (
            f"### 解析统计\n\n"
            f"- 文档数：**{len(rd_rows)}**\n"
            f"- 识别小节总数：**{total_sections}**\n"
            f"- 最大层级：**L{max_depth}**"
        )
        depth_keys = sorted(depth_counter.keys(), key=lambda k: int(k[1:]))
        depth_df = pd.DataFrame([{"depth": k, "count": depth_counter[k]} for k in depth_keys])
        return [
            summary,
            pd.DataFrame(doc_section_counts),
            depth_df,
            pd.DataFrame(section_rows),
        ]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 3: Segment ----------

def render_segment(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        snap_ids = _snapshot_ids(rt, run_id)
        if not snap_ids:
            return [
                EMPTY_RUN_TEXT,
                _empty_counts_df("block_type"),
                _empty_counts_df("token_bin"),
                pd.DataFrame(),
            ]
        ph = ",".join(["%s"] * len(snap_ids))

        bt = asset.execute(
            f"SELECT block_type, COUNT(*) AS c FROM asset_raw_segments "
            f"WHERE document_snapshot_id IN ({ph}) GROUP BY block_type ORDER BY c DESC",
            snap_ids,
        ).fetchall()
        all_segs = asset.execute(
            f"SELECT s.segment_index, s.block_type, s.semantic_role, s.section_title, "
            f"       s.token_count, s.raw_text, ds.title AS doc_title "
            f"FROM asset_raw_segments s "
            f"JOIN asset_document_snapshots ds ON s.document_snapshot_id = ds.id "
            f"WHERE s.document_snapshot_id IN ({ph}) "
            f"ORDER BY s.document_snapshot_id, s.segment_index",
            snap_ids,
        ).fetchall()

        token_counter: dict[str, int] = {}
        total_tokens = 0
        for r in all_segs:
            b = _bin_token(r["token_count"])
            token_counter[b] = token_counter.get(b, 0) + 1
            total_tokens += r["token_count"] or 0

        summary = (
            f"### 分块统计\n\n"
            f"- 段落总数：**{len(all_segs)}**\n"
            f"- token 总量：**{total_tokens:,}**\n"
            f"- 平均 token：**{(total_tokens / len(all_segs)):.1f}**" if all_segs else "（无）"
        )
        token_df = _ordered_bin_df(token_counter, _BIN_ORDER, "token_bin")
        full_df = pd.DataFrame(
            [
                {
                    "doc": _truncate(r["doc_title"], 30),
                    "#": r["segment_index"],
                    "block_type": r["block_type"],
                    "semantic_role": r["semantic_role"],
                    "section_title": _truncate(r["section_title"], 50),
                    "tokens": r["token_count"],
                    "raw_text": _truncate(r["raw_text"], 200),
                }
                for r in all_segs
            ]
        )
        return [summary, _counts_df(bt, "block_type"), token_df, full_df]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 4: Enrich ----------

def render_enrich(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        snap_ids = _snapshot_ids(rt, run_id)
        if not snap_ids:
            return [
                EMPTY_RUN_TEXT,
                _empty_counts_df("semantic_role"),
                _empty_counts_df("entity_type"),
                _empty_counts_df("count_per_seg"),
                pd.DataFrame(),
            ]
        ph = ",".join(["%s"] * len(snap_ids))

        roles = asset.execute(
            f"SELECT semantic_role, COUNT(*) AS c FROM asset_raw_segments "
            f"WHERE document_snapshot_id IN ({ph}) GROUP BY semantic_role ORDER BY c DESC",
            snap_ids,
        ).fetchall()
        total_segs = sum(r["c"] for r in roles)
        unknown_segs = next((r["c"] for r in roles if r["semantic_role"] == "unknown"), 0)
        classified_segs = total_segs - unknown_segs
        classify_rate = (classified_segs / total_segs * 100) if total_segs else 0.0

        seg_rows = asset.execute(
            f"SELECT s.segment_index, s.section_title, s.entity_refs_json, s.raw_text, "
            f"       ds.title AS doc_title "
            f"FROM asset_raw_segments s "
            f"JOIN asset_document_snapshots ds ON s.document_snapshot_id = ds.id "
            f"WHERE s.document_snapshot_id IN ({ph}) AND s.entity_refs_json != '[]' "
            f"ORDER BY s.segment_index",
            snap_ids,
        ).fetchall()

        type_counter: dict[str, int] = {}
        per_seg_counter: dict[str, int] = {}
        entity_table: list[dict] = []
        total_entities = 0

        for r in seg_rows:
            try:
                ents = json.loads(r["entity_refs_json"])
            except Exception:
                ents = []
            n = len(ents)
            total_entities += n
            bucket = "1" if n == 1 else "2" if n == 2 else "3" if n == 3 else "4-5" if n <= 5 else "6-10" if n <= 10 else "11+"
            per_seg_counter[bucket] = per_seg_counter.get(bucket, 0) + 1
            for e in ents:
                # DB schema: {"type": "...", "name": "..."} (see RawSegmentData.entity_refs_json).
                # Tolerate richer shapes if a future enricher emits them.
                etype = e.get("type") or e.get("entity_type") or "(unknown)"
                ename = e.get("name") or e.get("canonical") or e.get("text") or ""
                type_counter[etype] = type_counter.get(etype, 0) + 1
                entity_table.append(
                    {
                        "doc": _truncate(r["doc_title"], 30),
                        "seg#": r["segment_index"],
                        "entity_type": etype,
                        "name": _truncate(ename, 80),
                        "section": _truncate(r["section_title"], 40),
                    }
                )

        entity_hint = (
            "" if total_entities > 0
            else "\n\n> ℹ️ 实体提取无产出。rule-based 提取器靠领域包正则（命令/IP/参数等），"
                 "对非命令类文档常无命中；可启用 LLM enricher（左侧勾选 LLM Service）补强。"
        )
        summary = (
            f"### 增强统计\n\n"
            f"#### semantic_role 分类\n"
            f"- 段落总数：**{total_segs}**\n"
            f"- 已分类（非 unknown）：**{classified_segs}**（**{classify_rate:.1f}%**）\n"
            f"- 分类种类：**{sum(1 for r in roles if r['semantic_role'] != 'unknown' and r['c'] > 0)}** 种\n\n"
            f"#### 实体提取\n"
            f"- 含实体的段落：**{len(seg_rows)}**\n"
            f"- 实体引用总数：**{total_entities}**\n"
            f"- 实体类型数：**{len(type_counter)}**"
            f"{entity_hint}"
        )
        type_df = pd.DataFrame(
            sorted(({"entity_type": k, "count": v} for k, v in type_counter.items()), key=lambda x: -x["count"])
        ) if type_counter else _empty_counts_df("entity_type")
        per_seg_order = ["1", "2", "3", "4-5", "6-10", "11+"]
        per_seg_df = pd.DataFrame(
            [{"count_per_seg": k, "count": per_seg_counter.get(k, 0)} for k in per_seg_order if per_seg_counter.get(k)]
        ) if per_seg_counter else _empty_counts_df("count_per_seg")
        entity_df = pd.DataFrame(entity_table) if entity_table else pd.DataFrame()
        return [summary, _counts_df(roles, "semantic_role"), type_df, per_seg_df, entity_df]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 5: Relations ----------

def render_relations(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        snap_ids = _snapshot_ids(rt, run_id)
        if not snap_ids:
            return [
                EMPTY_RUN_TEXT,
                _empty_counts_df("relation_type"),
                _empty_counts_df("distance_bin"),
                pd.DataFrame(),
            ]
        ph = ",".join(["%s"] * len(snap_ids))

        types = asset.execute(
            f"SELECT relation_type, COUNT(*) AS c FROM asset_raw_segment_relations "
            f"WHERE document_snapshot_id IN ({ph}) GROUP BY relation_type ORDER BY c DESC",
            snap_ids,
        ).fetchall()
        all_rels = asset.execute(
            f"""SELECT r.relation_type, r.weight, r.confidence, r.distance,
                       s1.segment_index AS src_idx, s1.raw_text AS src_text,
                       s2.segment_index AS tgt_idx, s2.raw_text AS tgt_text
                  FROM asset_raw_segment_relations r
                  JOIN asset_raw_segments s1 ON r.source_segment_id = s1.id
                  JOIN asset_raw_segments s2 ON r.target_segment_id = s2.id
                 WHERE r.document_snapshot_id IN ({ph})
                 ORDER BY s1.segment_index, r.relation_type""",
            snap_ids,
        ).fetchall()

        dist_counter: dict[str, int] = {}
        total_dist = 0
        with_dist = 0
        for r in all_rels:
            b = _bin_distance(r["distance"])
            dist_counter[b] = dist_counter.get(b, 0) + 1
            if r["distance"] is not None:
                total_dist += abs(r["distance"])
                with_dist += 1

        avg_dist = (total_dist / with_dist) if with_dist else 0.0
        summary = (
            f"### 关系统计\n\n"
            f"- 关系总数：**{len(all_rels)}**\n"
            f"- 关系类型数：**{len(types)}**\n"
            f"- 平均距离：**{avg_dist:.1f}** （{with_dist} 条带 distance）"
        )
        full_df = pd.DataFrame(
            [
                {
                    "relation_type": r["relation_type"],
                    "src#": r["src_idx"],
                    "src_text": _truncate(r["src_text"], 80),
                    "tgt#": r["tgt_idx"],
                    "tgt_text": _truncate(r["tgt_text"], 80),
                    "weight": round(r["weight"] or 0, 2),
                    "conf": round(r["confidence"] or 0, 2),
                    "dist": r["distance"],
                }
                for r in all_rels
            ]
        )
        return [
            summary,
            _counts_df(types, "relation_type"),
            _ordered_bin_df(dist_counter, _DIST_ORDER, "distance_bin"),
            full_df,
        ]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 6: Retrieval Units ----------

def render_retrieval_units(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        snap_ids = _snapshot_ids(rt, run_id)
        if not snap_ids:
            return [
                EMPTY_RUN_TEXT,
                _empty_counts_df("unit_type"),
                _empty_counts_df("target_type"),
                _empty_counts_df("text_len_bin"),
                pd.DataFrame(),
            ]
        ph = ",".join(["%s"] * len(snap_ids))

        ut = asset.execute(
            f"SELECT unit_type, COUNT(*) AS c FROM asset_retrieval_units "
            f"WHERE document_snapshot_id IN ({ph}) GROUP BY unit_type ORDER BY c DESC",
            snap_ids,
        ).fetchall()
        tt = asset.execute(
            f"SELECT target_type, COUNT(*) AS c FROM asset_retrieval_units "
            f"WHERE document_snapshot_id IN ({ph}) GROUP BY target_type ORDER BY c DESC",
            snap_ids,
        ).fetchall()
        all_units = asset.execute(
            f"SELECT u.unit_type, u.target_type, u.title, u.text, u.search_text, "
            f"       u.block_type, u.semantic_role, u.weight, ds.title AS doc_title "
            f"FROM asset_retrieval_units u "
            f"JOIN asset_document_snapshots ds ON u.document_snapshot_id = ds.id "
            f"WHERE u.document_snapshot_id IN ({ph}) "
            f"ORDER BY u.document_snapshot_id, u.unit_type, u.id",
            snap_ids,
        ).fetchall()

        len_counter: dict[str, int] = {}
        total_text = 0
        for r in all_units:
            n = len(r["text"] or "")
            total_text += n
            b = _bin_text_len(n)
            len_counter[b] = len_counter.get(b, 0) + 1

        avg_len = (total_text / len(all_units)) if all_units else 0.0
        summary = (
            f"### 检索单元统计\n\n"
            f"- 单元总数：**{len(all_units)}**\n"
            f"- 单元类型数：**{len(ut)}** / 目标类型数：**{len(tt)}**\n"
            f"- 平均文本长度：**{avg_len:.0f}** 字符"
        )
        full_df = pd.DataFrame(
            [
                {
                    "doc": _truncate(r["doc_title"], 30),
                    "unit_type": r["unit_type"],
                    "target_type": r["target_type"],
                    "block_type": r["block_type"],
                    "semantic_role": r["semantic_role"],
                    "title": _truncate(r["title"], 60),
                    "text": _truncate(r["text"], 200),
                    "weight": round(r["weight"] or 0, 2),
                }
                for r in all_units
            ]
        )
        return [
            summary,
            _counts_df(ut, "unit_type"),
            _counts_df(tt, "target_type"),
            _ordered_bin_df(len_counter, _TXTLEN_ORDER, "text_len_bin"),
            full_df,
        ]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 7: Snapshot ----------

def render_snapshot(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        rd_rows = rt.execute(
            "SELECT document_id, document_snapshot_id, document_key, action FROM mining_run_documents "
            "WHERE run_id = %s AND document_id IS NOT NULL",
            (run_id,),
        ).fetchall()
        if not rd_rows:
            return [EMPTY_RUN_TEXT, _empty_counts_df("action"), _empty_counts_df("mime_type"), pd.DataFrame()]

        action_counter: dict[str, int] = {}
        mime_counter: dict[str, int] = {}
        rows: list[dict] = []
        for rd in rd_rows:
            snap = asset.execute(
                "SELECT normalized_content_hash, mime_type, title FROM asset_document_snapshots WHERE id = %s",
                (rd["document_snapshot_id"],),
            ).fetchone()
            action = rd["action"] or "(空)"
            action_counter[action] = action_counter.get(action, 0) + 1
            mime = (snap["mime_type"] if snap else "?") or "?"
            mime_counter[mime] = mime_counter.get(mime, 0) + 1
            rows.append(
                {
                    "document_key": _truncate(rd["document_key"], 80),
                    "action": action,
                    "doc_id": (rd["document_id"] or "")[:12],
                    "snapshot_id": (rd["document_snapshot_id"] or "")[:12],
                    "mime": mime,
                    "title": _truncate(snap["title"], 60) if snap else "",
                    "norm_hash": (snap["normalized_content_hash"][:12] if snap else ""),
                }
            )

        summary = (
            f"### 快照统计\n\n"
            f"- 已绑定 document 的记录：**{len(rd_rows)}**\n"
            f"- action 分布：{', '.join(f'**{k}**={v}' for k, v in action_counter.items())}"
        )
        action_df = pd.DataFrame([{"action": k, "count": v} for k, v in action_counter.items()])
        mime_df = pd.DataFrame([{"mime_type": k, "count": v} for k, v in mime_counter.items()])
        return [summary, action_df, mime_df, pd.DataFrame(rows)]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 8: Build ----------

def render_build(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        run_row = rt.execute("SELECT build_id FROM mining_runs WHERE id = %s", (run_id,)).fetchone()
        if not run_row or not run_row["build_id"]:
            return [EMPTY_RUN_TEXT, _empty_counts_df("reason"), pd.DataFrame()]
        build_id = run_row["build_id"]
        b = asset.execute(
            "SELECT id, build_code, build_mode, status, created_at, finished_at, summary_json "
            "FROM asset_builds WHERE id = %s",
            (build_id,),
        ).fetchone()
        if not b:
            return [EMPTY_RUN_TEXT, _empty_counts_df("reason"), pd.DataFrame()]

        reason_rows = asset.execute(
            "SELECT reason, COUNT(*) AS c FROM asset_build_document_snapshots "
            "WHERE build_id = %s GROUP BY reason ORDER BY c DESC",
            (build_id,),
        ).fetchall()
        bds_rows = asset.execute(
            "SELECT bds.reason, bds.selection_status, ds.title, ds.mime_type, ds.normalized_content_hash "
            "FROM asset_build_document_snapshots bds "
            "JOIN asset_document_snapshots ds ON bds.document_snapshot_id = ds.id "
            "WHERE bds.build_id = %s ORDER BY bds.reason",
            (build_id,),
        ).fetchall()

        summary = (
            f"### 构建信息\n\n"
            f"- build_id：`{b['id']}`\n"
            f"- build_code：`{b['build_code']}`\n"
            f"- 模式：**{b['build_mode']}** | 状态：**{b['status']}**\n"
            f"- 创建：{b['created_at']} | 完成：{b['finished_at'] or '-'}\n"
            f"- 包含快照数：**{len(bds_rows)}**"
        )
        full_df = pd.DataFrame(
            [
                {
                    "reason": r["reason"],
                    "selection_status": r["selection_status"],
                    "title": _truncate(r["title"], 60),
                    "mime": r["mime_type"],
                    "norm_hash": (r["normalized_content_hash"] or "")[:12],
                }
                for r in bds_rows
            ]
        )
        return [summary, _counts_df(reason_rows, "reason"), full_df]
    finally:
        rt.close()
        asset.close()


# ---------- Stage 9: Release ----------

def render_release(run_id: str) -> list[Any]:
    rt = _open_runtime()
    asset = _open_asset()
    try:
        run_row = rt.execute("SELECT build_id FROM mining_runs WHERE id = %s", (run_id,)).fetchone()
        if not run_row or not run_row["build_id"]:
            return ["_（未生成 build，无 release）_", pd.DataFrame()]
        rels = asset.execute(
            "SELECT id, release_code, channel, status, activated_at, deactivated_at, "
            "       released_by, release_notes "
            "FROM asset_publish_releases WHERE build_id = %s "
            "ORDER BY COALESCE(activated_at, '') DESC",
            (run_row["build_id"],),
        ).fetchall()
        if not rels:
            return ["_（本次未 publish_release，可能 phase1_only 或失败被阻断）_", pd.DataFrame()]
        summary = f"### Release 列表（{len(rels)} 条）\n"
        full_df = pd.DataFrame(
            [
                {
                    "release_id": (r["id"] or "")[:12],
                    "release_code": r["release_code"],
                    "channel": r["channel"],
                    "status": r["status"],
                    "activated_at": r["activated_at"] or "-",
                    "deactivated_at": r["deactivated_at"] or "-",
                    "released_by": r["released_by"] or "-",
                    "notes": _truncate(r["release_notes"], 100),
                }
                for r in rels
            ]
        )
        return [summary, full_df]
    finally:
        rt.close()
        asset.close()


# ---------- Timeline ----------

def render_timeline(run_id: str) -> list[Any]:
    rt = _open_runtime()
    try:
        events = rt.execute(
            "SELECT stage, status, duration_ms, output_summary, error_message, created_at "
            "FROM mining_run_stage_events WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()
    finally:
        rt.close()
    if not events:
        return [EMPTY_RUN_TEXT, _empty_counts_df("stage"), pd.DataFrame()]

    dur_per_stage: dict[str, int] = {}
    for e in events:
        if e["duration_ms"] is not None:
            dur_per_stage[e["stage"]] = dur_per_stage.get(e["stage"], 0) + (e["duration_ms"] or 0)

    total_ms = sum(dur_per_stage.values())
    summary = (
        f"### 阶段事件\n\n"
        f"- 事件总数：**{len(events)}**\n"
        f"- 涉及阶段数：**{len(dur_per_stage)}**\n"
        f"- 总耗时：**{total_ms:,} ms**"
    )
    dur_df = pd.DataFrame(
        sorted(({"stage": k, "ms": v} for k, v in dur_per_stage.items()), key=lambda x: -x["ms"])
    ) if dur_per_stage else pd.DataFrame({"stage": [], "ms": []})
    full_df = pd.DataFrame(
        [
            {
                "time": (e["created_at"] or "")[-12:-3],
                "stage": e["stage"],
                "status": e["status"],
                "ms": e["duration_ms"],
                "output": _truncate(e["output_summary"], 100),
                "error": _truncate(e["error_message"], 100),
            }
            for e in events
        ]
    )
    return [summary, dur_df, full_df]


# =====================================================================
# Stage registry — every stage knows how to build its UI and render data
# =====================================================================

_PLOT_KW = dict(height=180, container=False)


def build_ingest_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        action_plot = gr.BarPlot(x="action", y="count", title="按 action 分布", **_PLOT_KW)
        status_plot = gr.BarPlot(x="status", y="count", title="按 status 分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全量文档列表", wrap=True, interactive=False)
    return [summary, action_plot, status_plot, table]


def build_parse_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        per_doc = gr.BarPlot(x="doc", y="count", title="每文档小节数", **_PLOT_KW)
        depth_plot = gr.BarPlot(x="depth", y="count", title="小节层级分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部小节", wrap=True, interactive=False)
    return [summary, per_doc, depth_plot, table]


def build_segment_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        bt_plot = gr.BarPlot(x="block_type", y="count", title="block_type 分布", **_PLOT_KW)
        token_plot = gr.BarPlot(x="token_bin", y="count", title="token_count 分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部段落", wrap=True, interactive=False)
    return [summary, bt_plot, token_plot, table]


def build_enrich_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        role_plot = gr.BarPlot(x="semantic_role", y="count", title="semantic_role 分布", **_PLOT_KW)
        type_plot = gr.BarPlot(x="entity_type", y="count", title="实体类型分布", **_PLOT_KW)
    per_seg_plot = gr.BarPlot(x="count_per_seg", y="count", title="每段实体数分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部实体引用", wrap=True, interactive=False)
    return [summary, role_plot, type_plot, per_seg_plot, table]


def build_relations_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        type_plot = gr.BarPlot(x="relation_type", y="count", title="relation_type 分布", **_PLOT_KW)
        dist_plot = gr.BarPlot(x="distance_bin", y="count", title="距离分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部关系", wrap=True, interactive=False)
    return [summary, type_plot, dist_plot, table]


def build_retrieval_units_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        ut_plot = gr.BarPlot(x="unit_type", y="count", title="unit_type 分布", **_PLOT_KW)
        tt_plot = gr.BarPlot(x="target_type", y="count", title="target_type 分布", **_PLOT_KW)
    len_plot = gr.BarPlot(x="text_len_bin", y="count", title="文本长度分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部检索单元", wrap=True, interactive=False)
    return [summary, ut_plot, tt_plot, len_plot, table]


def build_snapshot_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        action_plot = gr.BarPlot(x="action", y="count", title="action 分布", **_PLOT_KW)
        mime_plot = gr.BarPlot(x="mime_type", y="count", title="mime_type 分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="document → snapshot 映射", wrap=True, interactive=False)
    return [summary, action_plot, mime_plot, table]


def build_build_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    reason_plot = gr.BarPlot(x="reason", y="count", title="reason 分布", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="构建包含的快照", wrap=True, interactive=False)
    return [summary, reason_plot, table]


def build_release_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="Release 列表", wrap=True, interactive=False)
    return [summary, table]


def build_timeline_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    dur_plot = gr.BarPlot(x="stage", y="ms", title="按阶段累计耗时", **_PLOT_KW)
    with gr.Accordion("展开明细表格", open=False):
        table = gr.Dataframe(label="全部阶段事件", wrap=True, interactive=False)
    return [summary, dur_plot, table]


# Stage spec: id (stable key), label (UI), short (stepper), build, render, n_components
STAGES: list[dict[str, Any]] = [
    {"id": "ingest",    "label": "Ingest 摄取",       "short": "Ingest",    "build": build_ingest_tab,           "render": render_ingest,           "n": 4},
    {"id": "parse",     "label": "Parse 解析",        "short": "Parse",     "build": build_parse_tab,            "render": render_parse,            "n": 4},
    {"id": "segment",   "label": "Segment 分块",      "short": "Segment",   "build": build_segment_tab,          "render": render_segment,          "n": 4},
    {"id": "enrich",    "label": "Enrich 增强",       "short": "Enrich",    "build": build_enrich_tab,           "render": render_enrich,           "n": 5},
    {"id": "relations", "label": "Relations 关系",    "short": "Relations", "build": build_relations_tab,        "render": render_relations,        "n": 4},
    {"id": "units",     "label": "Retrieval Units",   "short": "Units",     "build": build_retrieval_units_tab,  "render": render_retrieval_units,  "n": 5},
    {"id": "snapshot",  "label": "Snapshot 快照",     "short": "Snapshot",  "build": build_snapshot_tab,         "render": render_snapshot,         "n": 4},
    {"id": "build",     "label": "Build 构建",        "short": "Build",     "build": build_build_tab,            "render": render_build,            "n": 3},
    {"id": "release",   "label": "Release 发布",      "short": "Release",   "build": build_release_tab,          "render": render_release,          "n": 2},
    {"id": "timeline",  "label": "事件时间线",         "short": "Timeline",  "build": build_timeline_tab,         "render": render_timeline,         "n": 3},
]
STAGE_BY_ID = {s["id"]: s for s in STAGES}
STAGE_IDS = [s["id"] for s in STAGES]
PIPELINE_STAGE_IDS = [sid for sid in STAGE_IDS if sid != "timeline"]  # 9 pipeline stages for stepper


def _empty_render_for(stage: dict[str, Any]) -> list[Any]:
    """Default placeholder per stage when run not started or stage not yet reached."""
    if stage["id"] == "ingest":
        return [EMPTY_RUN_TEXT, _empty_counts_df("action"), _empty_counts_df("status"), pd.DataFrame()]
    if stage["id"] == "parse":
        return [EMPTY_RUN_TEXT, _empty_counts_df("doc"), _empty_counts_df("depth"), pd.DataFrame()]
    if stage["id"] == "segment":
        return [EMPTY_RUN_TEXT, _empty_counts_df("block_type"), _empty_counts_df("token_bin"), pd.DataFrame()]
    if stage["id"] == "enrich":
        return [EMPTY_RUN_TEXT, _empty_counts_df("semantic_role"), _empty_counts_df("entity_type"),
                _empty_counts_df("count_per_seg"), pd.DataFrame()]
    if stage["id"] == "relations":
        return [EMPTY_RUN_TEXT, _empty_counts_df("relation_type"), _empty_counts_df("distance_bin"), pd.DataFrame()]
    if stage["id"] == "units":
        return [EMPTY_RUN_TEXT, _empty_counts_df("unit_type"), _empty_counts_df("target_type"),
                _empty_counts_df("text_len_bin"), pd.DataFrame()]
    if stage["id"] == "snapshot":
        return [EMPTY_RUN_TEXT, _empty_counts_df("action"), _empty_counts_df("mime_type"), pd.DataFrame()]
    if stage["id"] == "build":
        return [EMPTY_RUN_TEXT, _empty_counts_df("reason"), pd.DataFrame()]
    if stage["id"] == "release":
        return [EMPTY_RUN_TEXT, pd.DataFrame()]
    return [EMPTY_RUN_TEXT, _empty_counts_df("stage"), pd.DataFrame()]  # timeline


def _safe_render(stage: dict[str, Any], run_id: str | None) -> list[Any]:
    """Render a stage, falling back to placeholder on error or missing run."""
    if not run_id:
        return _empty_render_for(stage)
    try:
        return stage["render"](run_id)
    except Exception as e:
        msg = f"❌ 渲染失败：{type(e).__name__}: {e}"
        out = _empty_render_for(stage)
        out[0] = msg
        return out


# =====================================================================
# Per-stage state derivation (status + KPI) from PG
# =====================================================================

def _stage_event_status(events_by_stage: dict[str, dict], names: tuple[str, ...]) -> str | None:
    """Return 'completed' if any of the named stage_events is completed,
    'started' if any started but none completed, else None."""
    seen = "none"
    for name in names:
        ev = events_by_stage.get(name)
        if not ev:
            continue
        if ev["status"] == "completed":
            return "completed"
        if ev["status"] == "failed":
            return "failed"
        if ev["status"] == "started":
            seen = "started"
    return seen if seen != "none" else None


def _compute_pipeline_status(run_id: str, run_row: dict) -> dict[str, dict]:
    """Compute per-stage UI status: pending / running / done / failed / cancelled.

    Returns dict keyed by stage id (only the 9 pipeline stages, no timeline).
    Each value has: {status, kpi_text, duration_ms}
    """
    if run_row is None:
        return {sid: {"status": "pending", "kpi": None, "duration_ms": None}
                for sid in PIPELINE_STAGE_IDS}
    rt = _open_runtime()
    asset = _open_asset()
    overall_status = run_row["status"]
    is_terminal = overall_status in ("completed", "failed", "cancelled")
    try:
        # Latest stage event per stage name
        ev_rows = rt.execute(
            "SELECT stage, status, duration_ms, error_message, created_at "
            "FROM mining_run_stage_events "
            "WHERE run_id = %s ORDER BY created_at",
            (run_id,),
        ).fetchall()
        events_by_stage: dict[str, dict] = {}
        for r in ev_rows:
            cur = events_by_stage.get(r["stage"])
            # Keep the latest for each stage; "completed" trumps "started".
            if cur is None or (cur["status"] == "started" and r["status"] in ("completed", "failed")):
                events_by_stage[r["stage"]] = dict(r)

        snap_ids = _snapshot_ids(rt, run_id)
        snap_ph = ",".join(["%s"] * len(snap_ids)) if snap_ids else None

        # ingest: based on mining_run_documents
        doc_rows = rt.execute(
            "SELECT status, action FROM mining_run_documents WHERE run_id = %s",
            (run_id,),
        ).fetchall()
        n_docs = len(doc_rows)
        n_docs_failed = sum(1 for r in doc_rows if r["status"] == "failed")
        n_docs_committed = sum(1 for r in doc_rows if r["status"] == "committed")

        # asset-side counts for downstream stages
        n_segments = 0
        n_enriched_segs = 0
        n_relations = 0
        n_units = 0
        n_snapshots = 0
        if snap_ph:
            n_segments = (asset.execute(
                f"SELECT COUNT(*) AS c FROM asset_raw_segments WHERE document_snapshot_id IN ({snap_ph})",
                snap_ids,
            ).fetchone() or {"c": 0})["c"]
            n_enriched_segs = (asset.execute(
                f"SELECT COUNT(*) AS c FROM asset_raw_segments "
                f"WHERE document_snapshot_id IN ({snap_ph}) AND entity_refs_json != '[]'",
                snap_ids,
            ).fetchone() or {"c": 0})["c"]
            n_relations = (asset.execute(
                f"SELECT COUNT(*) AS c FROM asset_raw_segment_relations WHERE document_snapshot_id IN ({snap_ph})",
                snap_ids,
            ).fetchone() or {"c": 0})["c"]
            n_units = (asset.execute(
                f"SELECT COUNT(*) AS c FROM asset_retrieval_units WHERE document_snapshot_id IN ({snap_ph})",
                snap_ids,
            ).fetchone() or {"c": 0})["c"]
            n_snapshots = (asset.execute(
                f"SELECT COUNT(*) AS c FROM asset_document_snapshots WHERE id IN ({snap_ph})",
                snap_ids,
            ).fetchone() or {"c": 0})["c"]

        # build / release
        build_id = run_row.get("build_id")
        n_releases = 0
        if build_id:
            n_releases = (asset.execute(
                "SELECT COUNT(*) AS c FROM asset_publish_releases WHERE build_id = %s",
                (build_id,),
            ).fetchone() or {"c": 0})["c"]
    finally:
        rt.close()
        asset.close()

    seg_evt = events_by_stage.get("segment")
    rel_evt = events_by_stage.get("build_relations")
    snap_evt = events_by_stage.get("select_snapshot")
    ru_evt = events_by_stage.get("build_retrieval_units")
    ab_evt = events_by_stage.get("assemble_build")
    pr_evt = events_by_stage.get("publish_release")

    def derive(has_data: bool, evt: dict | None, *, started_others: bool = False) -> tuple[str, int | None]:
        """Return (status, duration_ms)."""
        if evt and evt["status"] == "failed":
            return ("failed", evt.get("duration_ms"))
        if evt and evt["status"] == "completed":
            return ("done", evt.get("duration_ms"))
        if has_data:
            return ("done" if is_terminal else "running", None)
        if evt and evt["status"] == "started":
            return ("running", None)
        if started_others or overall_status == "running":
            return ("pending", None)
        if overall_status == "cancelled":
            return ("cancelled", None)
        if overall_status == "failed" and not has_data:
            return ("pending", None)
        return ("pending", None)

    stages: dict[str, dict] = {}
    # ingest
    if n_docs > 0:
        st = "failed" if (n_docs_failed > 0 and n_docs_committed == 0 and overall_status == "failed") else "done"
        kpi = f"📄 {n_docs} 个 · ✓{n_docs_committed} ✗{n_docs_failed}"
    else:
        st = "running" if overall_status == "running" else "pending"
        kpi = "—"
    stages["ingest"] = {"status": st, "kpi": kpi, "duration_ms": None}

    # parse — segment events are written together with seg DB write; parse alone has no event
    parse_status, parse_dur = derive(n_segments > 0, seg_evt)
    stages["parse"] = {"status": parse_status, "kpi": f"≈{n_segments} 段" if n_segments else "—", "duration_ms": None}

    # segment
    seg_st, seg_dur = derive(n_segments > 0, seg_evt)
    stages["segment"] = {"status": seg_st, "kpi": f"{n_segments} 段" if n_segments else "—", "duration_ms": seg_dur}

    # enrich — no explicit event; presence of enriched segs OR completion of relations implies done
    if n_enriched_segs > 0:
        en_st, en_kpi = ("done" if is_terminal else "done"), f"{n_enriched_segs} 段含实体"
    elif rel_evt and rel_evt.get("status") == "completed":
        en_st, en_kpi = "done", "0 实体（rule-based 无命中）"
    elif n_segments > 0:
        en_st = "running" if overall_status == "running" else "pending"
        en_kpi = "—"
    else:
        en_st = "running" if overall_status == "running" else "pending"
        en_kpi = "—"
    stages["enrich"] = {"status": en_st, "kpi": en_kpi, "duration_ms": None}

    # relations
    rel_st, rel_dur = derive(n_relations > 0, rel_evt)
    stages["relations"] = {"status": rel_st, "kpi": f"{n_relations} 条" if n_relations else "—", "duration_ms": rel_dur}

    # units
    ru_st, ru_dur = derive(n_units > 0, ru_evt)
    stages["units"] = {"status": ru_st, "kpi": f"{n_units} 单元" if n_units else "—", "duration_ms": ru_dur}

    # snapshot
    snap_st, snap_dur = derive(n_snapshots > 0, snap_evt)
    stages["snapshot"] = {"status": snap_st, "kpi": f"{n_snapshots} 快照" if n_snapshots else "—", "duration_ms": snap_dur}

    # build
    has_build = bool(build_id)
    bd_st, bd_dur = derive(has_build, ab_evt)
    stages["build"] = {"status": bd_st, "kpi": f"build {build_id[:8]}" if has_build and build_id else "—", "duration_ms": bd_dur}

    # release
    rel2_st, rel2_dur = derive(n_releases > 0, pr_evt)
    stages["release"] = {"status": rel2_st, "kpi": f"{n_releases} release" if n_releases else "—", "duration_ms": rel2_dur}

    # If overall is cancelled and stage is still pending, mark as cancelled
    if overall_status == "cancelled":
        for sid, sd in stages.items():
            if sd["status"] in ("pending", "running"):
                sd["status"] = "cancelled"

    return stages


# =====================================================================
# UI helpers (rendering)
# =====================================================================

_STATUS_GLYPH = {
    "done":      "✓",
    "running":   "⟳",
    "pending":   "○",
    "failed":    "✗",
    "cancelled": "⊘",
}
_STATUS_COLOR = {
    "done":      "#10b981",
    "running":   "#3b82f6",
    "pending":   "#94a3b8",
    "failed":    "#ef4444",
    "cancelled": "#f59e0b",
}


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    m, s = divmod(ms // 1000, 60)
    return f"{m}m{s:02d}s"


def _stepper_choices(stages: dict[str, dict]) -> list[tuple[str, str]]:
    """Build the list of (label, value) tuples for the Stepper Radio (9 stages)."""
    out: list[tuple[str, str]] = []
    for sid in PIPELINE_STAGE_IDS:
        s = STAGE_BY_ID[sid]
        st = stages.get(sid, {}).get("status", "pending")
        glyph = _STATUS_GLYPH[st]
        dur = _fmt_duration(stages.get(sid, {}).get("duration_ms"))
        kpi = stages.get(sid, {}).get("kpi", "—")
        if st == "running":
            extra = "运行中…"
        elif st == "done":
            extra = dur
        elif st == "failed":
            extra = "失败"
        elif st == "cancelled":
            extra = "已取消"
        else:
            extra = "待运行"
        label = f"{glyph} {s['short']} · {extra}"
        if kpi != "—" and st in ("done", "running", "failed"):
            label += f" · {kpi}"
        out.append((label, sid))
    return out


def _phase_from_run(run_row: dict | None) -> str:
    """Map mining_runs.status → UI phase."""
    if run_row is None:
        return "running"  # run not yet appeared, but worker spawned
    s = run_row["status"]
    if s == "completed":
        return "done"
    if s == "failed":
        return "failed"
    if s == "cancelled":
        return "cancelled"
    return "running"


def _elapsed_seconds(started_at: str | None, finished_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        from datetime import datetime, timezone
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
        return max(0, int((end - start).total_seconds()))
    except Exception:
        return 0


def _progress_pct(stages: dict[str, dict]) -> int:
    done = sum(1 for sid in PIPELINE_STAGE_IDS if stages.get(sid, {}).get("status") in ("done",))
    return int(done / len(PIPELINE_STAGE_IDS) * 100)


def _status_bar_html(run_row: dict | None, stages: dict[str, dict], phase: str) -> str:
    if run_row is None:
        return '<div class="status-bar">⏳ 启动中…</div>'
    elapsed = _elapsed_seconds(run_row.get("started_at"), run_row.get("finished_at"))
    mm, ss = divmod(elapsed, 60)
    pct = _progress_pct(stages)
    rid = (run_row.get("id") or "")[:8]
    if phase == "running":
        icon, label, color = "🏃", "运行中", "#3b82f6"
    elif phase == "done":
        icon, label, color = "✅", "完成", "#10b981"
    elif phase == "failed":
        icon, label, color = "❌", "失败", "#ef4444"
    elif phase == "cancelled":
        icon, label, color = "⊘", "已取消", "#f59e0b"
    else:
        icon, label, color = "•", phase, "#94a3b8"
    return (
        f'<div class="status-bar">'
        f'  <span class="sb-badge" style="background:{color}1a;color:{color};">{icon} {label}</span>'
        f'  <span class="sb-meta">run <code>{rid}</code></span>'
        f'  <span class="sb-meta">⏱ {mm}:{ss:02d}</span>'
        f'  <span class="sb-meta">进度 {pct}%</span>'
        f'  <div class="sb-bar"><div class="sb-bar-fill" style="width:{pct}%;background:{color};"></div></div>'
        f'</div>'
    )


def _focus_label_md(focus_stage: str | None, stages: dict[str, dict], auto_follow: bool) -> str:
    if not focus_stage:
        return "_（请选择阶段）_"
    s = STAGE_BY_ID.get(focus_stage)
    if not s:
        return "_（未知阶段）_"
    st = stages.get(focus_stage, {}).get("status", "pending")
    glyph = _STATUS_GLYPH.get(st, "•")
    follow = " · 📍 自动跟随最新已完成阶段" if auto_follow else ""
    return f"### {glyph} 当前焦点 · {s['label']}{follow}"


def _kpi_html(stage_id: str, stages: dict[str, dict]) -> str:
    """4-card KPI strip for the focused stage panel."""
    sd = stages.get(stage_id, {})
    st = sd.get("status", "pending")
    glyph = _STATUS_GLYPH.get(st, "•")
    color = _STATUS_COLOR.get(st, "#94a3b8")
    dur = _fmt_duration(sd.get("duration_ms"))
    kpi = sd.get("kpi", "—")
    label_map = {
        "done": "已完成", "running": "运行中",
        "pending": "等待中", "failed": "失败", "cancelled": "已取消",
    }
    return (
        f'<div class="kpi-row">'
        f'  <div class="kpi-card"><div class="kpi-num" style="color:{color};">{glyph}</div>'
        f'    <div class="kpi-label">{label_map.get(st, st)}</div></div>'
        f'  <div class="kpi-card"><div class="kpi-num">{dur}</div>'
        f'    <div class="kpi-label">耗时</div></div>'
        f'  <div class="kpi-card"><div class="kpi-num">{kpi}</div>'
        f'    <div class="kpi-label">本阶段产出</div></div>'
        f'</div>'
    )


# =====================================================================
# Run-orchestration helpers
# =====================================================================

_RUN_LOCK = threading.Lock()


def _next_focus(prev: str | None, stages: dict[str, dict], auto_follow: bool) -> str:
    """Pick the stage to focus on.

    auto_follow=True: latest stage with status='done', else first 'running', else first 'pending'.
    auto_follow=False: keep prev (or fallback if invalid).
    """
    valid = [sid for sid in PIPELINE_STAGE_IDS if stages.get(sid, {}).get("status") != "pending"]
    if not auto_follow and prev and prev in PIPELINE_STAGE_IDS and (
        stages.get(prev, {}).get("status") != "pending"
    ):
        return prev
    # auto follow: latest done first, then running, else first pipeline stage
    last_done = None
    first_running = None
    for sid in PIPELINE_STAGE_IDS:
        st = stages.get(sid, {}).get("status")
        if st == "done":
            last_done = sid
        if st == "running" and first_running is None:
            first_running = sid
    return last_done or first_running or (valid[0] if valid else PIPELINE_STAGE_IDS[0])


def _cancel_run_in_db(run_id: str) -> None:
    """Best-effort: flip mining_runs.status to 'cancelled' (UI-side cancel signal)."""
    pool = _get_pool()
    with pool.connection() as conn:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE mining_runs SET status = 'cancelled', finished_at = %s "
            "WHERE id = %s AND status IN ('running', 'pending', 'queued')",
            (now, run_id),
        )


# =====================================================================
# Callbacks
# =====================================================================

INITIAL_STATE: dict[str, Any] = {
    "phase": "ready",          # ready | running | done | failed | cancelled
    "run_id": None,
    "input_path": None,
    "focus_stage": None,
    "auto_follow": True,
    "started_at_local": None,  # python time, for orphan-detection
    "stages": {sid: {"status": "pending", "kpi": "—", "duration_ms": None} for sid in PIPELINE_STAGE_IDS},
}


def _build_outputs_for_phase(
    state: dict[str, Any],
    run_row: dict | None = None,
) -> tuple[list[Any], list[Any]]:
    """Build (focus-area updates, per-stage updates) lists for current state.

    Returns:
      focus_updates — [status_bar_html, stepper_choices, focus_label_md]
      stage_updates — flat list of per-stage [group_visible, kpi_html, *components(n)]
                      for all 10 stages (pipeline 9 + timeline)
    """
    phase = state["phase"]
    stages_state = state["stages"]
    focus = state["focus_stage"]
    auto_follow = state.get("auto_follow", True)

    sb_html = _status_bar_html(run_row, stages_state, phase)
    stepper_choices = _stepper_choices(stages_state)
    if focus and focus in PIPELINE_STAGE_IDS:
        focus_value = focus
    else:
        focus_value = PIPELINE_STAGE_IDS[0]
    focus_label = _focus_label_md(focus, stages_state, auto_follow)
    focus_updates = [
        sb_html,
        gr.update(choices=stepper_choices, value=focus_value),
        focus_label,
    ]

    # Per-stage updates: render only the focused stage; others get their components untouched (gr.skip)
    run_id = state.get("run_id")
    stage_updates: list[Any] = []
    for stage in STAGES:
        sid = stage["id"]
        is_focus = (sid == focus) or (sid == "timeline" and focus == "timeline")
        # Group visibility: focus visible; non-pipeline timeline visible if focus == 'timeline'
        # For pipeline stages, only the focused one is visible
        visible = (sid == focus) if focus else (sid == PIPELINE_STAGE_IDS[0])
        stage_updates.append(gr.update(visible=visible))  # group
        # KPI HTML
        if sid in PIPELINE_STAGE_IDS:
            stage_updates.append(_kpi_html(sid, stages_state))
        else:
            stage_updates.append("<div class='kpi-row'></div>")
        # Components
        if is_focus and run_id:
            rendered = _safe_render(stage, run_id)
            stage_updates.extend(rendered)
        else:
            # Don't touch other stages' components; pad with gr.skip()
            stage_updates.extend([gr.skip()] * stage["n"])
    return focus_updates, stage_updates


def _empty_outputs() -> tuple[list[Any], list[Any]]:
    """Outputs for the READY phase (run group hidden)."""
    sb_html = ""
    empty_choices = [(f"○ {s['short']} · 待运行", s["id"]) for s in STAGES if s["id"] != "timeline"]
    focus_updates = [sb_html, gr.update(choices=empty_choices, value=PIPELINE_STAGE_IDS[0]), ""]
    stage_updates: list[Any] = []
    for stage in STAGES:
        stage_updates.append(gr.update(visible=False))  # group
        stage_updates.append("<div class='kpi-row'></div>")
        stage_updates.extend(_empty_render_for(stage))
    return focus_updates, stage_updates


def _ingest_files(files, target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for f in files or []:
        src = Path(f.name if hasattr(f, "name") else f)
        dst = target / src.name
        shutil.copy(src, dst)
        out.append(dst)
    return out


def cb_start_mining(
    state, files, product, tags, doc_type, domain_pack, use_llm, llm_url, embedding_key,
):
    """Click handler for the Start button. Spawns worker, switches to RUNNING."""
    if not files:
        gr.Warning("请先上传文件")
        # Return current outputs unchanged (basically gr.skip everywhere except maybe state)
        focus_updates, stage_updates = _empty_outputs()
        return [
            state,                      # state
            gr.update(visible=True),    # ready_group
            gr.update(visible=False),   # run_group
            gr.update(active=False),    # timer
            gr.update(visible=False),   # cancel_btn
            gr.update(visible=False),   # restart_btn
        ] + focus_updates + stage_updates

    # Acquire run lock
    if not _RUN_LOCK.acquire(blocking=False):
        gr.Warning("已经有挖掘任务在执行")
        focus_updates, stage_updates = _empty_outputs()
        return [
            state,
            gr.update(visible=True), gr.update(visible=False), gr.update(active=False),
            gr.update(visible=False), gr.update(visible=False),
        ] + focus_updates + stage_updates

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = UPLOADS_ROOT / ts
    _ingest_files(files, target)

    product_v = (product or "").strip()
    tags_v = (tags or "").strip()
    doc_type_v = (doc_type or "").strip()
    domain_pack_v = (domain_pack or "cloud_core_network").strip() or "cloud_core_network"
    llm_url_v = (llm_url or "").strip()
    embedding_key_v = (embedding_key or "").strip()

    def worker():
        try:
            mining_run(
                input_path=target,
                batch_params=BatchParams(
                    default_source_type="folder_scan",
                    default_document_type=doc_type_v or None,
                    batch_scope=({"products": [product_v]} if product_v else {}),
                    tags=[t.strip() for t in tags_v.split(",") if t.strip()],
                ),
                domain_pack=domain_pack_v,
                llm_base_url=(llm_url_v if (use_llm and llm_url_v) else None),
                llm_bypass_proxy=True,
                embedding_api_key=(embedding_key_v or None),
            )
        except Exception:
            traceback.print_exc()
        finally:
            _RUN_LOCK.release()

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    new_state = {
        **state,
        "phase": "running",
        "input_path": str(target),
        "started_at_local": time.time(),
        "auto_follow": True,
        "focus_stage": PIPELINE_STAGE_IDS[0],
        "run_id": None,
        "stages": INITIAL_STATE["stages"].copy(),
    }
    focus_updates, stage_updates = _build_outputs_for_phase(new_state, None)
    return [
        new_state,
        gr.update(visible=False),  # ready_group hidden
        gr.update(visible=True),   # run_group visible
        gr.update(active=True),    # timer ON
        gr.update(visible=True),   # cancel_btn visible
        gr.update(visible=False),  # restart_btn hidden
    ] + focus_updates + stage_updates


def cb_poll_tick(state):
    """Timer callback — read PG, refresh UI."""
    if state["phase"] == "ready":
        # Should not happen (timer disabled), but guard anyway
        focus_updates, stage_updates = _empty_outputs()
        return [
            state, gr.update(visible=True), gr.update(visible=False),
            gr.update(active=False), gr.update(visible=False), gr.update(visible=False),
        ] + focus_updates + stage_updates

    input_path = state.get("input_path")
    if not input_path:
        focus_updates, stage_updates = _build_outputs_for_phase(state, None)
        return [
            state, gr.update(visible=False), gr.update(visible=True),
            gr.update(active=True), gr.update(visible=True), gr.update(visible=False),
        ] + focus_updates + stage_updates

    snap = _query_latest_run(input_path)
    if snap is None:
        # Run not yet visible in DB
        if state.get("started_at_local") and time.time() - state["started_at_local"] > 30:
            # Orphan — worker likely failed before creating run row
            new_state = {**state, "phase": "failed"}
            focus_updates, stage_updates = _build_outputs_for_phase(new_state, None)
            return [
                new_state, gr.update(visible=False), gr.update(visible=True),
                gr.update(active=False), gr.update(visible=False), gr.update(visible=True),
            ] + focus_updates + stage_updates
        focus_updates, stage_updates = _build_outputs_for_phase(state, None)
        return [
            state, gr.update(visible=False), gr.update(visible=True),
            gr.update(active=True), gr.update(visible=True), gr.update(visible=False),
        ] + focus_updates + stage_updates

    run_row = snap["run"]
    run_id = run_row["id"]
    new_phase = _phase_from_run(run_row)
    stages_state = _compute_pipeline_status(run_id, run_row)
    focus = _next_focus(state.get("focus_stage"), stages_state, state.get("auto_follow", True))

    new_state = {
        **state,
        "phase": new_phase,
        "run_id": run_id,
        "stages": stages_state,
        "focus_stage": focus,
    }

    is_terminal = new_phase in ("done", "failed", "cancelled")
    focus_updates, stage_updates = _build_outputs_for_phase(new_state, run_row)
    return [
        new_state,
        gr.update(visible=False),                 # ready_group hidden
        gr.update(visible=True),                  # run_group visible
        gr.update(active=not is_terminal),        # timer
        gr.update(visible=(new_phase == "running")),    # cancel_btn
        gr.update(visible=is_terminal),                 # restart_btn
    ] + focus_updates + stage_updates


def cb_cancel(state):
    """Click handler for ▣ 终止."""
    run_id = state.get("run_id")
    if not run_id:
        gr.Warning("尚未拿到 run_id，请稍等再点")
        focus_updates, stage_updates = _build_outputs_for_phase(state, None)
        return [
            state, gr.update(visible=False), gr.update(visible=True),
            gr.update(active=True), gr.update(visible=True), gr.update(visible=False),
        ] + focus_updates + stage_updates
    try:
        _cancel_run_in_db(run_id)
        gr.Info("已发送终止信号，worker 将在最近一次检查点退出")
    except Exception as e:
        gr.Warning(f"发送终止失败：{e}")
    # Disable cancel button visually until next poll
    focus_updates, stage_updates = _build_outputs_for_phase(state, None)
    return [
        state,
        gr.update(visible=False), gr.update(visible=True),
        gr.update(active=True),
        gr.update(visible=False, value="⏳ 正在停止…"),  # cancel_btn disabled visually
        gr.update(visible=False),
    ] + focus_updates + stage_updates


def cb_stepper_change(state, new_focus):
    """User clicked a different stage on the stepper — pin focus, disable auto-follow."""
    if not new_focus or new_focus not in PIPELINE_STAGE_IDS:
        return [state] + [gr.skip()] * (5 + 3 + sum(2 + s["n"] for s in STAGES))
    new_state = {**state, "focus_stage": new_focus, "auto_follow": False}
    focus_updates, stage_updates = _build_outputs_for_phase(
        new_state, _query_latest_run(state["input_path"])["run"] if state.get("input_path") else None
    )
    return [
        new_state,
        gr.update(),  # ready_group
        gr.update(),  # run_group
        gr.update(),  # timer
        gr.update(),  # cancel_btn
        gr.update(),  # restart_btn
    ] + focus_updates + stage_updates


def cb_enable_follow(state):
    new_state = {**state, "auto_follow": True}
    snap = _query_latest_run(state["input_path"]) if state.get("input_path") else None
    run_row = snap["run"] if snap else None
    if run_row:
        stages_state = _compute_pipeline_status(run_row["id"], run_row)
        new_state["stages"] = stages_state
        new_state["focus_stage"] = _next_focus(None, stages_state, True)
    focus_updates, stage_updates = _build_outputs_for_phase(new_state, run_row)
    return [
        new_state,
        gr.update(),  # ready_group
        gr.update(),  # run_group
        gr.update(),  # timer
        gr.update(),  # cancel_btn
        gr.update(),  # restart_btn
    ] + focus_updates + stage_updates


def cb_restart(state):
    """Reset to READY phase."""
    new_state = INITIAL_STATE.copy()
    new_state["stages"] = {sid: {"status": "pending", "kpi": "—", "duration_ms": None} for sid in PIPELINE_STAGE_IDS}
    focus_updates, stage_updates = _empty_outputs()
    return [
        new_state,
        gr.update(visible=True),   # ready_group
        gr.update(visible=False),  # run_group
        gr.update(active=False),
        gr.update(visible=False, value="▣ 终止"),
        gr.update(visible=False),
    ] + focus_updates + stage_updates


def cb_show_timeline(state):
    """Switch focus to timeline tab."""
    new_state = {**state, "focus_stage": "timeline", "auto_follow": False}
    snap = _query_latest_run(state["input_path"]) if state.get("input_path") else None
    run_row = snap["run"] if snap else None
    focus_updates, stage_updates = _build_outputs_for_phase(new_state, run_row)
    return [
        new_state,
        gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
    ] + focus_updates + stage_updates


# =====================================================================
# UI layout
# =====================================================================

CUSTOM_CSS = """
.gradio-container {
    max-width: 1480px !important;
    margin: 0 auto !important;
    font-family: "Inter", -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #f5f7fb !important;
}
#app-header {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 4px 8px 4px; margin-bottom: 6px;
    border-bottom: 1px solid #e6e8f0;
}
#app-header h1 {
    margin: 0 !important; font-size: 22px !important; font-weight: 700 !important;
    color: #1f2937 !important; letter-spacing: -0.01em;
}
#app-header .subtitle { color: #6b7280; font-size: 13px; }

/* READY group */
#ready-group .gr-block {
    background: #fff; border-radius: 12px; padding: 16px;
    border: 1px solid #e6e8f0; box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
#start-btn {
    background: #5b6cff !important; color: #fff !important;
    font-weight: 600 !important; font-size: 15px !important; padding: 12px !important;
    border: none !important; border-radius: 10px !important;
    box-shadow: 0 1px 4px rgba(91,108,255,0.25) !important;
}
#start-btn:hover { background: #4a59e8 !important; }

/* Status bar (RUNNING) */
.status-bar {
    display: flex; align-items: center; gap: 14px;
    padding: 10px 14px; background: #fff;
    border-radius: 12px; border: 1px solid #e6e8f0;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
    font-size: 13px;
}
.status-bar .sb-badge {
    padding: 4px 10px; border-radius: 6px; font-weight: 600; font-size: 12px;
}
.status-bar .sb-meta { color: #475569; }
.status-bar .sb-meta code {
    background: #f1f5f9; padding: 1px 6px; border-radius: 4px; font-size: 12px;
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
}
.status-bar .sb-bar {
    flex: 1 1 auto; height: 6px; background: #e6e8f0; border-radius: 3px;
    overflow: hidden; min-width: 80px;
}
.status-bar .sb-bar-fill { height: 100%; transition: width 0.3s ease; }

#cancel-btn {
    background: #fff !important; color: #ef4444 !important;
    border: 1px solid #ef4444 !important; border-radius: 8px !important;
    font-weight: 600 !important; font-size: 13px !important;
    padding: 6px 14px !important;
}
#cancel-btn:hover { background: #fef2f2 !important; }

#restart-btn {
    background: #5b6cff !important; color: #fff !important;
    border: none !important; border-radius: 8px !important;
    font-weight: 600 !important; font-size: 13px !important;
    padding: 6px 14px !important;
}

#follow-btn, #timeline-btn {
    background: #fff !important; color: #475569 !important;
    border: 1px solid #cbd5e1 !important; border-radius: 8px !important;
    font-size: 12px !important; padding: 4px 10px !important; font-weight: 500 !important;
}
#follow-btn:hover, #timeline-btn:hover { background: #f8fafc !important; }

/* Stepper Radio styled as horizontal stepper */
#stepper {
    background: #fff; padding: 12px; border-radius: 12px;
    border: 1px solid #e6e8f0;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
#stepper > label > span { display: none !important; }  /* hide "Radio" label text */
#stepper .wrap, #stepper fieldset {
    flex-direction: row !important; gap: 6px !important;
    flex-wrap: wrap !important;
}
#stepper label {
    flex: 1 1 0 !important; min-width: 110px !important;
    border: 1px solid #e6e8f0 !important; border-radius: 8px !important;
    padding: 8px 10px !important; cursor: pointer !important;
    background: #f8fafc !important; transition: all .15s ease;
    font-size: 12px !important;
}
#stepper label:hover { background: #f1f5f9 !important; border-color: #cbd5e1 !important; }
#stepper input[type="radio"]:checked + span,
#stepper label:has(input:checked) {
    background: #eef2ff !important; border-color: #5b6cff !important;
    color: #4338ca !important; font-weight: 600 !important;
}

/* Active panel cards */
.kpi-row {
    display: flex; gap: 12px; margin: 12px 0;
}
.kpi-card {
    flex: 1 1 0; min-width: 110px;
    background: #fff; border-radius: 10px; padding: 14px;
    border: 1px solid #e6e8f0;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
.kpi-card .kpi-num {
    font-size: 22px; font-weight: 700; color: #1f2937;
    margin-bottom: 4px; letter-spacing: -0.01em;
}
.kpi-card .kpi-label {
    font-size: 12px; color: #6b7280;
}

/* Stage panel container */
.stage-panel {
    background: #fff; padding: 16px; border-radius: 12px;
    border: 1px solid #e6e8f0;
    box-shadow: 0 1px 2px rgba(15,23,42,0.04);
}
"""

_GRADIO_THEME = gr.themes.Soft(
    primary_hue="indigo",
    neutral_hue="slate",
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui"],
)

with gr.Blocks(title="Knowledge Mining Studio") as demo:
    state = gr.State(value=INITIAL_STATE)

    # ===== Header =====
    with gr.Row(elem_id="app-header"):
        gr.HTML('<h1>🛠️ Knowledge Mining Studio</h1>'
                '<span class="subtitle">v1.1 · PostgreSQL backend</span>')

    # ===== READY region =====
    with gr.Group(visible=True, elem_id="ready-group") as ready_group:
        with gr.Row(equal_height=False):
            with gr.Column(scale=1, min_width=340):
                gr.Markdown("### 📎 上传文档")
                files = gr.Files(
                    label="支持 .md / .txt / .html / .pdf / .docx / .chm / .hdx",
                    file_count="multiple",
                    height=160,
                )
                gr.Markdown("### 🏷️ Batch 参数")
                product = gr.Textbox(label="产品名", value="UI-Test", info="写入 batch_scope.products")
                tags = gr.Textbox(label="标签", value="ui,test", info="逗号分隔")
                doc_type = gr.Dropdown(
                    label="document_type",
                    choices=[
                        "procedure", "feature", "command", "troubleshooting",
                        "alarm", "constraint", "checklist", "expert_note",
                        "project_note", "standard", "training", "reference", "other",
                    ],
                    value="procedure",
                )
                _pack_choices = _list_domain_packs()
                _pack_default = (
                    "cloud_core_network" if "cloud_core_network" in _pack_choices
                    else _pack_choices[0]
                )
                domain_pack = gr.Dropdown(
                    label="domain_pack",
                    choices=_pack_choices,
                    value=_pack_default,
                )
                with gr.Accordion("⚙️ LLM / Embedding（可选）", open=False):
                    use_llm = gr.Checkbox(label="启用 LLM Service", value=False)
                    llm_url = gr.Textbox(label="LLM URL", value="http://localhost:8900")
                    embedding_key = gr.Textbox(
                        label="Embedding API Key",
                        type="password",
                        info="留空则跳过 embedding",
                    )
            with gr.Column(scale=2):
                gr.Markdown("### ✅ 准备开始")
                gr.Markdown(
                    "上传文件并选择领域包后，点击下方按钮开始 9 阶段挖掘流水线。\n\n"
                    "运行期间：实时显示阶段进度 / 阶段产出 / 可终止 / 可点 stepper 切换查看任一已完成阶段的结果。"
                )
                start_btn = gr.Button("▶  开始挖掘", variant="primary", size="lg", elem_id="start-btn")

    # ===== RUNNING / DONE / FAILED / CANCELLED region =====
    with gr.Group(visible=False) as run_group:
        # Top status bar + actions
        with gr.Row():
            with gr.Column(scale=4):
                status_bar = gr.HTML('<div class="status-bar">⏳ 启动中…</div>')
            with gr.Column(scale=1, min_width=120):
                cancel_btn = gr.Button("▣  终止", visible=True, elem_id="cancel-btn")
                restart_btn = gr.Button("🔄  上传新批次重跑", visible=False, elem_id="restart-btn")

        # Stepper
        gr.Markdown("#### 流程进度")
        stepper = gr.Radio(
            choices=[(f"○ {s['short']} · 待运行", s["id"]) for s in STAGES if s["id"] != "timeline"],
            value=PIPELINE_STAGE_IDS[0],
            show_label=False,
            elem_id="stepper",
        )

        # Focus label + auto-follow toggle
        with gr.Row():
            focus_label = gr.Markdown("### 当前焦点")
            with gr.Column(scale=0, min_width=200):
                follow_btn = gr.Button("📍  跟随最新", elem_id="follow-btn", size="sm")
                timeline_btn = gr.Button("⏱  事件时间线", elem_id="timeline-btn", size="sm")

        # Active panel — one Group per stage (only one visible at a time)
        stage_groups: list[Any] = []
        stage_kpi_components: list[Any] = []
        stage_components_flat: list[Any] = []  # nested in stage order
        stage_components_per_stage: list[list[Any]] = []
        for stage in STAGES:
            with gr.Group(visible=False, elem_classes=f"stage-panel stage-panel-{stage['id']}") as g:
                kpi_html = gr.HTML("<div class='kpi-row'></div>")
                comps = stage["build"]()
            stage_groups.append(g)
            stage_kpi_components.append(kpi_html)
            stage_components_per_stage.append(comps)
            stage_components_flat.extend(comps)

    # Hidden timer (1s) — drives polling while phase=='running'
    timer = gr.Timer(1.0, active=False)

    # ===== Wiring =====
    # Flat output list shape (used by all callbacks):
    #   [state,
    #    ready_group, run_group, timer, cancel_btn, restart_btn,
    #    status_bar, stepper, focus_label,
    #    *for each stage: [group, kpi_html, *components]]
    per_stage_outputs: list[Any] = []
    for i, stage in enumerate(STAGES):
        per_stage_outputs.append(stage_groups[i])
        per_stage_outputs.append(stage_kpi_components[i])
        per_stage_outputs.extend(stage_components_per_stage[i])

    common_outputs = [
        state,
        ready_group, run_group, timer, cancel_btn, restart_btn,
        status_bar, stepper, focus_label,
    ] + per_stage_outputs

    start_btn.click(
        cb_start_mining,
        inputs=[state, files, product, tags, doc_type, domain_pack, use_llm, llm_url, embedding_key],
        outputs=common_outputs,
    )
    timer.tick(cb_poll_tick, inputs=[state], outputs=common_outputs)
    cancel_btn.click(cb_cancel, inputs=[state], outputs=common_outputs)
    stepper.change(cb_stepper_change, inputs=[state, stepper], outputs=common_outputs)
    follow_btn.click(cb_enable_follow, inputs=[state], outputs=common_outputs)
    timeline_btn.click(cb_show_timeline, inputs=[state], outputs=common_outputs)
    restart_btn.click(cb_restart, inputs=[state], outputs=common_outputs)


if __name__ == "__main__":
    demo.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
        css=CUSTOM_CSS,
        theme=_GRADIO_THEME,
    )
