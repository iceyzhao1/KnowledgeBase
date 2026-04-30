"""PostgreSQL tsvector + pg_trgm BM25 retriever — pure retrieval, no post-filtering.

Post-filtering (role/block_type preference, truncation) is handled
by the Reranker stage, not the retriever.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from psycopg_pool import AsyncConnectionPool

from agent_serving.serving.schemas.constants import ROUTE_LEXICAL_BM25
from agent_serving.serving.schemas.models import RetrievalCandidate, RetrievalQuery
from agent_serving.serving.retrieval.retriever import Retriever

logger = logging.getLogger(__name__)


class FTS5BM25Retriever(Retriever):
    """PostgreSQL tsvector + pg_trgm BM25 retrieval over asset_retrieval_units.

    Returns raw scored candidates; role/block_type preference
    and budget truncation are handled by the Reranker stage.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def retrieve(
        self,
        query: RetrievalQuery,
        snapshot_ids: list[str],
        top_k: int = 50,
    ) -> list[RetrievalCandidate]:
        if not snapshot_ids:
            return []

        # Collect search terms: keywords + sub_queries, fallback to original_query
        search_terms = list(query.keywords)
        for sq in query.sub_queries:
            search_terms.extend(sq.split())
        if not search_terms and query.original_query:
            search_terms = query.original_query.split()

        if not search_terms:
            return []

        recall_limit = top_k * 5
        query_text = " ".join(search_terms)

        placeholders = ",".join("%s" for _ in snapshot_ids)

        # Scope/filter pushdown from query.scope (parameterized to prevent SQL injection)
        scope_filter, scope_params = self._build_scope_filter(query.scope)

        # Primary: tsvector full-text search with ts_rank_cd scoring
        sql = f"""
            SELECT
                ru.id,
                ru.document_snapshot_id,
                ru.text,
                ru.title,
                ru.block_type,
                ru.semantic_role,
                ru.source_refs_json,
                ru.facets_json,
                ru.target_type,
                ru.target_ref_json,
                ru.unit_type,
                ru.source_segment_id,
                ts_rank_cd(ru.search_vector, plainto_tsquery('simple', %s)) AS fts_score
            FROM asset_retrieval_units ru
            WHERE ru.search_vector @@ plainto_tsquery('simple', %s)
              AND ru.document_snapshot_id IN ({placeholders})
              {scope_filter}
            ORDER BY fts_score DESC
            LIMIT %s
        """
        params: list[Any] = [query_text, query_text, *snapshot_ids, *scope_params, recall_limit]

        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
        except Exception:
            logger.warning("tsvector query failed, falling back to trigram similarity", exc_info=True)
            return await self._fallback_trigram(query, snapshot_ids, top_k)

        if not rows:
            return await self._fallback_trigram(query, snapshot_ids, top_k)

        return self._rows_to_candidates(rows, source=ROUTE_LEXICAL_BM25)

    async def _fallback_trigram(
        self,
        query: RetrievalQuery,
        snapshot_ids: list[str],
        top_k: int = 50,
    ) -> list[RetrievalCandidate]:
        """pg_trgm similarity fallback for Chinese text or short queries."""
        search_terms = list(query.keywords)
        for sq in query.sub_queries:
            search_terms.extend(sq.split())
        if not search_terms and query.original_query:
            search_terms = query.original_query.split()

        if not search_terms or not snapshot_ids:
            return []

        query_text = " ".join(search_terms)
        placeholders = ",".join("%s" for _ in snapshot_ids)
        recall_limit = top_k * 5

        scope_filter, scope_params = self._build_scope_filter(query.scope)

        sql = f"""
            SELECT
                ru.id,
                ru.document_snapshot_id,
                ru.text,
                ru.title,
                ru.block_type,
                ru.semantic_role,
                ru.source_refs_json,
                ru.facets_json,
                ru.target_type,
                ru.target_ref_json,
                ru.unit_type,
                ru.source_segment_id,
                similarity(ru.text, %s) AS sim_score
            FROM asset_retrieval_units ru
            WHERE ru.text %% %s
              AND ru.document_snapshot_id IN ({placeholders})
              {scope_filter}
            ORDER BY sim_score DESC
            LIMIT %s
        """
        params: list[Any] = [query_text, query_text, *snapshot_ids, *scope_params, recall_limit]

        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(sql, params)
                rows = await cursor.fetchall()
        except Exception:
            logger.warning("Trigram query also failed", exc_info=True)
            return await self._fallback_like(query, snapshot_ids, top_k)

        candidates = []
        for row in rows:
            r = dict(row)
            score = r.get("sim_score", 0.0) or 0.0
            candidates.append(self._row_to_candidate(r, score, source="trigram_fallback"))
        return candidates

    async def _fallback_like(
        self,
        query: RetrievalQuery,
        snapshot_ids: list[str],
        top_k: int = 50,
    ) -> list[RetrievalCandidate]:
        """LIKE fallback when both tsvector and trigram fail."""
        search_terms = list(query.keywords)
        for sq in query.sub_queries:
            search_terms.extend(sq.split())
        if not search_terms and query.original_query:
            search_terms = query.original_query.split()

        if not search_terms or not snapshot_ids:
            return []

        placeholders = ",".join("%s" for _ in snapshot_ids)
        like_clauses = " OR ".join(
            "ru.text LIKE %s" for _ in search_terms
        )
        params: list[Any] = [f"%{k}%" for k in search_terms]
        params.extend(snapshot_ids)

        recall_limit = top_k * 5

        scope_filter, scope_params = self._build_scope_filter(query.scope)

        sql = f"""
            SELECT
                ru.id,
                ru.document_snapshot_id,
                ru.text,
                ru.title,
                ru.block_type,
                ru.semantic_role,
                ru.source_refs_json,
                ru.facets_json,
                ru.target_type,
                ru.target_ref_json,
                ru.unit_type,
                ru.source_segment_id
            FROM asset_retrieval_units ru
            WHERE ({like_clauses})
              AND ru.document_snapshot_id IN ({placeholders})
              {scope_filter}
            LIMIT %s
        """
        params.extend(scope_params)
        params.append(recall_limit)

        async with self._pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        candidates = []
        for row in rows:
            r = dict(row)
            text = (r.get("text", "") or "").lower()
            hit_count = sum(
                1 for kw in search_terms if kw.lower() in text
            )
            score = hit_count / max(len(search_terms), 1)
            candidates.append(self._row_to_candidate(r, score, source="like_fallback"))

        return candidates

    def _rows_to_candidates(
        self,
        rows: list[Any],
        source: str,
    ) -> list[RetrievalCandidate]:
        candidates = []
        for row in rows:
            r = dict(row)
            score = r.get("fts_score", 0.0) or 0.0
            candidates.append(self._row_to_candidate(r, score, source))
        return candidates

    @staticmethod
    def _build_scope_filter(scope: dict) -> tuple[str, list[str]]:
        """Build SQL filter from query scope for facets_json pushdown.

        Returns (sql_fragment, params) using parameterized JSONB to prevent injection.
        """
        if not scope:
            return "", []
        conditions: list[str] = []
        params: list[str] = []
        for key, values in scope.items():
            if isinstance(values, list) and values:
                conditions.append("ru.facets_json @> %s::jsonb")
                params.append(json.dumps({key: values}))
        if not conditions:
            return "", []
        return " AND " + " AND ".join(conditions), params

    def _row_to_candidate(
        self,
        r: dict,
        score: float,
        source: str,
    ) -> RetrievalCandidate:
        return RetrievalCandidate(
            retrieval_unit_id=r["id"],
            score=score,
            source=source,
            metadata={
                "document_snapshot_id": r["document_snapshot_id"],
                "title": r.get("title"),
                "block_type": r.get("block_type", "unknown"),
                "semantic_role": r.get("semantic_role", "unknown"),
                "text": r.get("text", ""),
                "source_refs_json": r.get("source_refs_json", "{}"),
                "facets_json": r.get("facets_json", "{}"),
                "target_type": r.get("target_type", ""),
                "target_ref_json": r.get("target_ref_json", "{}"),
                "unit_type": r.get("unit_type", ""),
                "source_segment_id": r.get("source_segment_id"),
            },
        )
