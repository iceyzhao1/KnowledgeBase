"""Read-only puller for the serving DB (检索层评测的真实流量来源).

Pulls rows from ``serving_query_logs``（agent_serving_java 通过 AOP 落库的每次检索）
and reconstructs, per query, the ranked evidence the retriever returned:

- 用户问题      <- query_text
- 召回项排名     <- result_items_json 的数组顺序（已按 score 降序）
- 召回项文本     <- 用 item.id JOIN asset_retrieval_units.text（日志本身不存文本）
- 篇章路径       <- item.source_id 对到 result_sources_json[].relative_path
- 查询时长       <- duration_ms

日志里**没有黄金标注**，所以这条路只支撑「无需 gold 的精确率族指标」
（HitRate/MRR/NDCG/ContextPrecision + 时长）；召回率族需另行标注，后续再做。

模块对 psycopg 是惰性依赖：只有真正拉数时才 import，未安装则给出清晰报错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import ApiConfig


@dataclass
class LiveItem:
    """One retrieved evidence item reconstructed from a query-log row."""

    rank: int  # 1-based, 按日志中 score 降序的原始顺序
    text: str
    source_path: str | None = None  # 篇章路径（relative_path），seed 项可能为 None
    score: float | None = None
    role: str | None = None  # seed / neighbor / ...


@dataclass
class LiveCase:
    """One real query pulled from serving_query_logs, with its ranked evidence."""

    query_id: str
    question: str
    domain: str = ""
    intent: str | None = None
    duration_ms: int | None = None
    queried_at: str = ""
    items: list[LiveItem] = field(default_factory=list)


class ServingDBUnavailable(RuntimeError):
    """Raised when the serving DB is not configured or psycopg is missing."""


_SELECT = """
    SELECT id, query_text, domain, intent, duration_ms, queried_at,
           result_items_json, result_sources_json
    FROM serving_query_logs
    {where}
    ORDER BY queried_at DESC
    LIMIT %(limit)s
"""

_SELECT_ONE = """
    SELECT id, query_text, domain, intent, duration_ms, queried_at,
           result_items_json, result_sources_json
    FROM serving_query_logs
    WHERE {cond}
    ORDER BY queried_at DESC
    LIMIT 1
"""


def _require_db(config: ApiConfig):
    """Return psycopg.connect kwargs + the psycopg module, or raise clearly."""

    kwargs = config.serving_db_kwargs()
    if kwargs is None:
        raise ServingDBUnavailable(
            "serving DB 未配置：请在 runtime_eval/.env 设置 SERVING_PG_HOST / "
            "SERVING_PG_DBNAME / SERVING_PG_USER（及 PASSWORD）。"
        )
    try:
        import psycopg  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ServingDBUnavailable(
            "缺少 psycopg：请先 `pip install 'psycopg[binary]'` 再拉取真实查询日志。"
        ) from exc
    return kwargs, psycopg


def pull_latest_for_question(
    config: ApiConfig,
    question: str,
    *,
    domain: str | None = None,
) -> LiveCase | None:
    """Pull the single newest query-log row whose text matches ``question``.

    Powers the per-question「检索数据库」button: for one parsed question we fetch
    the latest real retrieval that the被测 retriever served. An exact
    ``query_text`` match is preferred; if none exists we fall back to a case-
    insensitive substring (ILIKE) match so小幅措辞差异也能命中最近一条。Returns
    ``None`` when nothing matches.
    """

    q = (question or "").strip()
    if not q:
        return None
    kwargs, psycopg = _require_db(config)

    domain_cond = " AND domain = %(domain)s" if domain else ""
    params: dict[str, object] = {"q": q, "like": f"%{q}%"}
    if domain:
        params["domain"] = domain

    with psycopg.connect(**kwargs) as conn:  # type: ignore[arg-type]
        with conn.cursor() as cur:
            cur.execute(
                _SELECT_ONE.format(cond="query_text = %(q)s" + domain_cond), params
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    _SELECT_ONE.format(cond="query_text ILIKE %(like)s" + domain_cond),
                    params,
                )
                row = cur.fetchone()
            if row is None:
                return None
            return _row_to_case(cur, row)


def _row_to_case(cur, row) -> LiveCase:
    """Resolve a query-log row into a LiveCase, dropping items with empty text."""
    items_raw = json.loads(row[6] or "[]")
    unit_ids = [it.get("id") for it in items_raw if it.get("id")]
    texts = _resolve_texts(cur, unit_ids)
    case = _build_case(row, texts)
    case.items = [it for it in case.items if it.text.strip()]
    return case


def search_similar_questions(
    config: ApiConfig,
    question: str,
    *,
    domain: str | None = None,
    limit: int = 5,
    min_similarity: float | None = None,
) -> list[dict]:
    """Find logged queries textually similar to ``question`` (pg_trgm ranked).

    Used when an exact/substring match fails: agent 常把问题改写成别的措辞，所以
    我们返回相似度最高的若干条日志问题供人工确认，而**不**自动选一条。每个候选给
    出 ``query_id``（确认后据此精确拉取）、原始 ``query_text`` 与相似度。Returns
    ``[]`` when pg_trgm 不可用或没有达到阈值的候选。
    """

    q = (question or "").strip()
    if not q:
        return []
    kwargs, psycopg = _require_db(config)
    thr = config.serving_match_threshold if min_similarity is None else float(min_similarity)
    domain_cond = " AND domain = %(domain)s" if domain else ""
    params: dict[str, object] = {"q": q, "thr": float(thr), "limit": max(1, int(limit))}
    if domain:
        params["domain"] = domain
    sql = """
        SELECT id, query_text, domain, intent, queried_at,
               similarity(query_text, %(q)s) AS sim
        FROM serving_query_logs
        WHERE similarity(query_text, %(q)s) >= %(thr)s {domain}
        ORDER BY sim DESC, queried_at DESC
        LIMIT %(limit)s
    """.format(domain=domain_cond)
    try:
        with psycopg.connect(**kwargs) as conn:  # type: ignore[arg-type]
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except psycopg.errors.UndefinedFunction:  # pragma: no cover - pg_trgm 未装
        return []
    return [
        {
            "query_id": r[0],
            "query_text": r[1] or "",
            "domain": r[2] or "",
            "intent": r[3],
            "queried_at": r[4] or "",
            "similarity": round(float(r[5]), 4),
        }
        for r in rows
    ]


def pull_by_query_id(config: ApiConfig, query_id: str) -> LiveCase | None:
    """Pull one specific query-log row by id (used after 人工确认 a candidate)."""

    qid = (query_id or "").strip()
    if not qid:
        return None
    kwargs, psycopg = _require_db(config)
    with psycopg.connect(**kwargs) as conn:  # type: ignore[arg-type]
        with conn.cursor() as cur:
            cur.execute(_SELECT_ONE.format(cond="id = %(qid)s"), {"qid": qid})
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_case(cur, row)


def _resolve_texts(cur, unit_ids: list[str]) -> dict[str, str]:
    """Batch-fetch evidence text for retrieval-unit ids from asset_retrieval_units."""

    if not unit_ids:
        return {}
    cur.execute(
        "SELECT id, text FROM asset_retrieval_units WHERE id = ANY(%s)",
        (unit_ids,),
    )
    return {row[0]: (row[1] or "") for row in cur.fetchall()}


def _build_case(row, texts: dict[str, str]) -> LiveCase:
    (qid, qtext, domain, intent, dur, queried_at, items_json, sources_json) = row
    items_raw = json.loads(items_json or "[]")
    sources_raw = json.loads(sources_json or "[]")
    path_by_source = {
        s.get("id"): s.get("relative_path") for s in sources_raw if s.get("id")
    }
    items: list[LiveItem] = []
    for i, it in enumerate(items_raw, start=1):
        uid = it.get("id")
        items.append(
            LiveItem(
                rank=i,
                text=texts.get(uid, ""),
                source_path=path_by_source.get(it.get("source_id")),
                score=it.get("score"),
                role=it.get("role"),
            )
        )
    return LiveCase(
        query_id=qid,
        question=qtext or "",
        domain=domain or "",
        intent=intent,
        duration_ms=dur,
        queried_at=queried_at or "",
        items=items,
    )


def pull_live_cases(
    config: ApiConfig,
    *,
    domain: str | None = None,
    channel: str | None = None,
    since: str | None = None,
    limit: int = 50,
    drop_empty_text: bool = True,
) -> list[LiveCase]:
    """Pull recent query-log rows as LiveCases (newest first).

    Filters are optional and ANDed: ``domain``, ``channel`` (correlation handle),
    ``since`` (ISO timestamp, compared against the text ``queried_at`` column).
    Items whose text could not be resolved are dropped when ``drop_empty_text``.
    """

    kwargs = config.serving_db_kwargs()
    if kwargs is None:
        raise ServingDBUnavailable(
            "serving DB 未配置：请在 runtime_eval/.env 设置 SERVING_PG_HOST / "
            "SERVING_PG_DBNAME / SERVING_PG_USER（及 PASSWORD）。"
        )
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on env
        raise ServingDBUnavailable(
            "缺少 psycopg：请先 `pip install 'psycopg[binary]'` 再拉取真实查询日志。"
        ) from exc

    conds: list[str] = []
    params: dict[str, object] = {"limit": max(1, int(limit))}
    if domain:
        conds.append("domain = %(domain)s")
        params["domain"] = domain
    if channel:
        conds.append("channel = %(channel)s")
        params["channel"] = channel
    if since:
        conds.append("queried_at >= %(since)s")
        params["since"] = since
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    sql = _SELECT.format(where=where)

    with psycopg.connect(**kwargs) as conn:  # type: ignore[arg-type]
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            # collect every retrieval-unit id across all rows, resolve text in one pass
            all_ids: list[str] = []
            seen: set[str] = set()
            per_row_items: list[list[dict]] = []
            for r in rows:
                items_raw = json.loads(r[6] or "[]")
                per_row_items.append(items_raw)
                for it in items_raw:
                    uid = it.get("id")
                    if uid and uid not in seen:
                        seen.add(uid)
                        all_ids.append(uid)
            texts = _resolve_texts(cur, all_ids)

    cases = [_build_case(r, texts) for r in rows]
    if drop_empty_text:
        for c in cases:
            c.items = [it for it in c.items if it.text.strip()]
    return cases
