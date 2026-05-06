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

_PLOT_KW = dict(height=260, container=False)


def build_ingest_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        action_plot = gr.BarPlot(x="action", y="count", title="按 action 分布", **_PLOT_KW)
        status_plot = gr.BarPlot(x="status", y="count", title="按 status 分布", **_PLOT_KW)
    table = gr.Dataframe(label="全量文档列表", wrap=True, interactive=False)
    return [summary, action_plot, status_plot, table]


def build_parse_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        per_doc = gr.BarPlot(x="doc", y="count", title="每文档小节数", **_PLOT_KW)
        depth_plot = gr.BarPlot(x="depth", y="count", title="小节层级分布", **_PLOT_KW)
    table = gr.Dataframe(label="全部小节", wrap=True, interactive=False)
    return [summary, per_doc, depth_plot, table]


def build_segment_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        bt_plot = gr.BarPlot(x="block_type", y="count", title="block_type 分布", **_PLOT_KW)
        token_plot = gr.BarPlot(x="token_bin", y="count", title="token_count 分布", **_PLOT_KW)
    table = gr.Dataframe(label="全部段落", wrap=True, interactive=False)
    return [summary, bt_plot, token_plot, table]


def build_enrich_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        role_plot = gr.BarPlot(x="semantic_role", y="count", title="semantic_role 分布（含 unknown）", **_PLOT_KW)
        type_plot = gr.BarPlot(x="entity_type", y="count", title="实体类型分布", **_PLOT_KW)
    per_seg_plot = gr.BarPlot(x="count_per_seg", y="count", title="每段实体数分布", **_PLOT_KW)
    table = gr.Dataframe(label="全部实体引用", wrap=True, interactive=False)
    return [summary, role_plot, type_plot, per_seg_plot, table]


def build_relations_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        type_plot = gr.BarPlot(x="relation_type", y="count", title="relation_type 分布", **_PLOT_KW)
        dist_plot = gr.BarPlot(x="distance_bin", y="count", title="距离分布", **_PLOT_KW)
    table = gr.Dataframe(label="全部关系", wrap=True, interactive=False)
    return [summary, type_plot, dist_plot, table]


def build_retrieval_units_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        ut_plot = gr.BarPlot(x="unit_type", y="count", title="unit_type 分布", **_PLOT_KW)
        tt_plot = gr.BarPlot(x="target_type", y="count", title="target_type 分布", **_PLOT_KW)
    len_plot = gr.BarPlot(x="text_len_bin", y="count", title="文本长度分布", **_PLOT_KW)
    table = gr.Dataframe(label="全部检索单元", wrap=True, interactive=False)
    return [summary, ut_plot, tt_plot, len_plot, table]


def build_snapshot_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    with gr.Row():
        action_plot = gr.BarPlot(x="action", y="count", title="action 分布", **_PLOT_KW)
        mime_plot = gr.BarPlot(x="mime_type", y="count", title="mime_type 分布", **_PLOT_KW)
    table = gr.Dataframe(label="document → snapshot 映射", wrap=True, interactive=False)
    return [summary, action_plot, mime_plot, table]


def build_build_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    reason_plot = gr.BarPlot(x="reason", y="count", title="reason 分布", **_PLOT_KW)
    table = gr.Dataframe(label="构建包含的快照", wrap=True, interactive=False)
    return [summary, reason_plot, table]


def build_release_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    table = gr.Dataframe(label="Release 列表", wrap=True, interactive=False)
    return [summary, table]


def build_timeline_tab():
    summary = gr.Markdown(EMPTY_RUN_TEXT)
    dur_plot = gr.BarPlot(x="stage", y="ms", title="按阶段累计耗时", **_PLOT_KW)
    table = gr.Dataframe(label="全部阶段事件", wrap=True, interactive=False)
    return [summary, dur_plot, table]


STAGES: list[dict[str, Any]] = [
    {"label": "1️⃣ Ingest 摄取",        "build": build_ingest_tab,           "render": render_ingest},
    {"label": "2️⃣ Parse 解析",         "build": build_parse_tab,            "render": render_parse},
    {"label": "3️⃣ Segment 分块",       "build": build_segment_tab,          "render": render_segment},
    {"label": "4️⃣ Enrich 增强",        "build": build_enrich_tab,           "render": render_enrich},
    {"label": "5️⃣ Relations 关系",     "build": build_relations_tab,        "render": render_relations},
    {"label": "6️⃣ Retrieval Units",    "build": build_retrieval_units_tab,  "render": render_retrieval_units},
    {"label": "7️⃣ Snapshot 快照",      "build": build_snapshot_tab,         "render": render_snapshot},
    {"label": "8️⃣ Build 构建",         "build": build_build_tab,            "render": render_build},
    {"label": "9️⃣ Release 发布",       "build": build_release_tab,          "render": render_release},
    {"label": "⏱️ 事件时间线",          "build": build_timeline_tab,         "render": render_timeline},
]


def _placeholder_outputs() -> list[Any]:
    """Return a list of placeholder values matching the flat stage components."""
    out: list[Any] = []
    for stage in STAGES:
        # Each stage builder produces: 1 markdown + N plots + 1 table
        # Use the stage's render-empty path: call with non-existent run_id won't work
        # Instead: produce neutral defaults — running text + empty dfs
        if stage["label"].startswith("1️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("action"), _empty_counts_df("status"), pd.DataFrame()])
        elif stage["label"].startswith("2️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("doc"), _empty_counts_df("depth"), pd.DataFrame()])
        elif stage["label"].startswith("3️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("block_type"),
                        _empty_counts_df("token_bin"), pd.DataFrame()])
        elif stage["label"].startswith("4️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("semantic_role"), _empty_counts_df("entity_type"),
                        _empty_counts_df("count_per_seg"), pd.DataFrame()])
        elif stage["label"].startswith("5️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("relation_type"), _empty_counts_df("distance_bin"), pd.DataFrame()])
        elif stage["label"].startswith("6️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("unit_type"), _empty_counts_df("target_type"),
                        _empty_counts_df("text_len_bin"), pd.DataFrame()])
        elif stage["label"].startswith("7️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("action"), _empty_counts_df("mime_type"), pd.DataFrame()])
        elif stage["label"].startswith("8️⃣"):
            out.extend([RUNNING_TEXT, _empty_counts_df("reason"), pd.DataFrame()])
        elif stage["label"].startswith("9️⃣"):
            out.extend([RUNNING_TEXT, pd.DataFrame()])
        else:  # timeline
            out.extend([RUNNING_TEXT, _empty_counts_df("stage"), pd.DataFrame()])
    return out


def _render_all(run_id: str | None) -> list[Any]:
    out: list[Any] = []
    if not run_id:
        return _placeholder_outputs()
    for stage in STAGES:
        try:
            out.extend(stage["render"](run_id))
        except Exception as e:
            err = f"❌ 渲染失败：{type(e).__name__}: {e}"
            # Fall back to placeholders for this stage
            placeholders = _placeholder_outputs()
            # Find the slice for this stage and replace summary with err
            # Simpler: just push the err as the first slot then pad with empty dfs
            out.append(err)
            # Pad
            stage_components = stage["build"].__name__
            # Count slots by re-running build is not safe — use heuristic from labels
            label = stage["label"]
            if label.startswith("1️⃣"):    pad = 3
            elif label.startswith("2️⃣"):  pad = 3
            elif label.startswith("3️⃣"):  pad = 3
            elif label.startswith("4️⃣"):  pad = 4
            elif label.startswith("5️⃣"):  pad = 3
            elif label.startswith("6️⃣"):  pad = 4
            elif label.startswith("7️⃣"):  pad = 3
            elif label.startswith("8️⃣"):  pad = 2
            elif label.startswith("9️⃣"):  pad = 1
            else:                          pad = 2
            for _ in range(pad):
                out.append(pd.DataFrame())
    return out


# =====================================================================
# Main run handler
# =====================================================================

def do_mining(
    files,
    product,
    tags,
    doc_type,
    domain_pack,
    use_llm,
    llm_url,
    embedding_key,
    progress=gr.Progress(),
):
    placeholder = _placeholder_outputs()

    if not files:
        yield ["❌ 请先上传文件", None] + placeholder
        return

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = UPLOADS_ROOT / ts
    target.mkdir(parents=True, exist_ok=True)
    for f in files:
        src = Path(f.name if hasattr(f, "name") else f)
        shutil.copy(src, target / src.name)

    yield [f"📁 已落盘 {len(files)} 个文件 → {target}\n⏳ 启动 mining...", None] + placeholder

    holder: dict = {}

    product_v = (product or "").strip()
    tags_v = (tags or "").strip()
    doc_type_v = (doc_type or "").strip()
    domain_pack_v = (domain_pack or "cloud_core_network").strip() or "cloud_core_network"
    llm_url_v = (llm_url or "").strip()
    embedding_key_v = (embedding_key or "").strip()

    def worker():
        try:
            holder["result"] = mining_run(
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
        except Exception as e:
            holder["error"] = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    last_msg = "⏳ 正在执行..."
    current_run_id: str | None = None
    while th.is_alive():
        time.sleep(1.0)
        snap = _query_latest_run(str(target))
        if snap:
            r = snap["run"]
            current_run_id = r["id"]
            ev = snap["latest_event"]
            stage_line = (
                f"阶段 {ev['stage']} [{ev['status']}]" if ev else "（尚未产生 stage event）"
            )
            last_msg = (
                f"🏃 run_id={r['id'][:12]}... | status={r['status']}\n"
                f"📄 文档 total={r['total_documents']} new={r['new_count']} "
                f"updated={r['updated_count']} skipped={r['skipped_count']} "
                f"failed={r['failed_count']} committed={r['committed_count']}\n"
                f"🔧 {stage_line}"
            )
        progress(0.5, desc="挖掘中...")
        yield [last_msg, None] + placeholder

    th.join()

    if "error" in holder:
        stage_outputs = _render_all(current_run_id)
        yield [f"❌ 失败:\n{holder['error']}", None] + stage_outputs
        return

    result = holder["result"]
    final_run_id = result.get("run_id") or current_run_id
    stage_outputs = _render_all(final_run_id)
    yield ["✅ 完成", result] + stage_outputs


# =====================================================================
# UI layout
# =====================================================================

CUSTOM_CSS = """
.gradio-container {
    max-width: 1480px !important;
    margin: 0 auto !important;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
#hero {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 28px 32px;
    border-radius: 16px;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(102, 126, 234, 0.25);
}
#hero h1 {
    margin: 0 0 6px 0 !important;
    color: white !important;
    font-size: 26px !important;
    font-weight: 700;
}
#hero p {
    margin: 0 !important;
    color: rgba(255,255,255,0.92) !important;
    font-size: 14px;
}
#sidebar {
    background: #f8f9fc;
    padding: 18px;
    border-radius: 12px;
    border: 1px solid #e6e8f0;
}
#run-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    font-weight: 600 !important;
    font-size: 16px !important;
    padding: 14px !important;
    border: none !important;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.35) !important;
    transition: transform .15s ease;
}
#run-btn:hover { transform: translateY(-1px); }
#status-box textarea {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace !important;
    font-size: 13px !important;
    background: #1a1d24 !important;
    color: #c8d3e1 !important;
    border-radius: 10px !important;
}
.tabs button.selected {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
}
h2 { color: #2d3748 !important; }
.gr-form, .gr-box { border-radius: 10px !important; }
"""

HERO_HTML = """
<div id="hero">
    <h1>🛠️ Knowledge Mining Studio</h1>
    <p>上传文档 → 一键跑完 9 阶段 pipeline → 每阶段「统计图表 + 全量数据」</p>
</div>
"""


with gr.Blocks(title="Knowledge Mining Studio") as demo:
    gr.HTML(HERO_HTML)

    with gr.Row(equal_height=False):
        # ---------- 左侧：参数 ----------
        with gr.Column(scale=1, min_width=340, elem_id="sidebar"):
            gr.Markdown("### 📎 上传文档")
            files = gr.Files(
                label="支持 .md / .txt / .html / .pdf / .docx / .chm / .hdx",
                file_count="multiple",
                height=180,
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
                info="数据库 CHECK 约束允许的枚举值（chm 安装/部署文档建议选 procedure）",
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
                info="选择领域知识包（决定实体抽取规则、语义角色关键词、LLM 模板）",
            )

            with gr.Accordion("⚙️ LLM / Embedding（可选）", open=False):
                use_llm = gr.Checkbox(label="启用 LLM Service", value=False)
                llm_url = gr.Textbox(label="LLM URL", value="http://localhost:8900")
                embedding_key = gr.Textbox(
                    label="Embedding API Key",
                    type="password",
                    info="留空则跳过 embedding",
                )

            btn = gr.Button("🚀 开始挖掘", variant="primary", size="lg", elem_id="run-btn")

        # ---------- 右侧：运行状态 + 结果 ----------
        with gr.Column(scale=2):
            gr.Markdown("### 📡 运行状态")
            status = gr.Textbox(
                label="",
                interactive=False,
                lines=5,
                show_label=False,
                elem_id="status-box",
                placeholder="点击「开始挖掘」后这里会实时刷新阶段、文档计数...",
            )
            with gr.Accordion("📦 run() 完整返回值", open=False):
                result = gr.JSON(label="")

    gr.Markdown("## 📊 阶段产物")
    gr.Markdown("_每个 Tab 展示该阶段的统计图表与全量数据。_")

    stage_components_flat: list[Any] = []
    with gr.Tabs():
        for stage in STAGES:
            with gr.Tab(stage["label"]):
                comps = stage["build"]()
                stage_components_flat.extend(comps)

    btn.click(
        do_mining,
        inputs=[files, product, tags, doc_type, domain_pack, use_llm, llm_url, embedding_key],
        outputs=[status, result] + stage_components_flat,
    )


if __name__ == "__main__":
    demo.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
        theme=gr.themes.Soft(
            primary_hue="indigo",
            secondary_hue="purple",
            neutral_hue="slate",
            font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui"],
        ),
        css=CUSTOM_CSS,
    )
