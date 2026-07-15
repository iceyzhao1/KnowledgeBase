"""Knowledge asset read-only query routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("/stats")
async def knowledge_stats(
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
) -> dict:
    """Global statistics across all asset tables."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        counts = {}
        tables = [
            ("documents", "asset_documents"),
            ("snapshots", "asset_document_snapshots"),
            ("segments", "asset_raw_segments"),
            ("relations", "asset_raw_segment_relations"),
            ("retrieval_units", "asset_retrieval_units"),
            ("embeddings", "asset_retrieval_embeddings"),
            ("builds", "asset_builds"),
            ("releases", "asset_publish_releases"),
        ]
        for key, table in tables:
            domain_filter = _domain_filter_for_table(table, domain)
            from_expr = "asset_documents d" if table == "asset_documents" and domain else table
            cur = await conn.execute(
                f"SELECT COUNT(*) as c FROM {from_expr} {domain_filter.where}",
                domain_filter.params(channel),
            )
            counts[key] = (await cur.fetchone())["c"]

        # Retrieval units by type
        unit_filter = _domain_filter_for_table("asset_retrieval_units", domain)
        cur = await conn.execute(
            f"SELECT unit_type, COUNT(*) as c FROM asset_retrieval_units {unit_filter.where} GROUP BY unit_type",
            unit_filter.params(channel),
        )
        type_dist = {r["unit_type"]: r["c"] for r in await cur.fetchall()}

        # Active releases (by domain+channel)
        cur = await conn.execute(
            "SELECT id, domain, channel FROM asset_publish_releases WHERE status = 'active'"
        )
        active_releases = [dict(r) for r in await cur.fetchall()]

    return {
        **counts,
        "retrieval_units_by_type": type_dist,
        "active_releases": active_releases,
    }


@router.get("/documents")
async def list_documents(
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    type: str | None = None,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List documents with optional type filter."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        conditions = []
        params: list[str] = []
        domain_filter = _domain_filter_for_table("asset_documents", domain)
        if domain_filter.condition:
            conditions.append(domain_filter.condition)
            params.extend(domain_filter.params(channel))
        if type:
            conditions.append("d.document_type = %s")
            params.append(type)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_cur = await conn.execute(
            f"SELECT COUNT(DISTINCT d.id) as c FROM asset_documents d {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT d.id, d.document_key, d.document_name, d.document_type, d.created_at "
            f"FROM asset_documents d {where} "
            f"GROUP BY d.id, d.document_key, d.document_name, d.document_type, d.created_at "
            f"ORDER BY d.created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
) -> dict:
    """Get document detail with snapshot history."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        domain_filter = _domain_filter_for_table("asset_documents", domain)
        conditions = ["d.id = %s"]
        params: list[str] = [document_id]
        if domain_filter.condition:
            conditions.append(domain_filter.condition)
            params.extend(domain_filter.params(channel))
        cur = await conn.execute(
            "SELECT d.id, d.document_key, d.document_name, d.document_type, "
            "created_at "
            f"FROM asset_documents d WHERE {' AND '.join(conditions)} "
            "GROUP BY d.id, d.document_key, d.document_name, d.document_type, d.created_at",
            params,
        )
        doc = await cur.fetchone()
        if not doc:
            raise HTTPException(404, f"Document {document_id} not found")

        snapshot_conditions = ["dsl.document_id = %s"]
        snapshot_params: list[str] = [document_id]
        if domain:
            snapshot_conditions.append(
                "EXISTS ("
                "SELECT 1 FROM asset_publish_releases apr "
                "JOIN asset_build_document_snapshots abds ON abds.build_id = apr.build_id "
                "WHERE apr.status = 'active' AND apr.domain = %s AND apr.channel = %s "
                "AND abds.document_snapshot_id = dsl.document_snapshot_id"
                ")"
            )
            snapshot_params.extend([domain, channel])
        cur = await conn.execute(
            "SELECT ds.id, ds.title, ds.normalized_content_hash, ds.mime_type, "
            "ds.created_at, dsl.linked_at, dsl.relative_path, dsl.source_uri "
            "FROM asset_document_snapshot_links dsl "
            "JOIN asset_document_snapshots ds ON dsl.document_snapshot_id = ds.id "
            f"WHERE {' AND '.join(snapshot_conditions)} "
            "ORDER BY dsl.linked_at DESC",
            snapshot_params,
        )
        snapshots = [dict(r) for r in await cur.fetchall()]

    return {**dict(doc), "snapshots": snapshots}


@router.get("/documents/{document_id}/segments")
async def get_document_segments(
    document_id: str,
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get segments for a document (via latest snapshot)."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        # Find latest snapshot
        domain_clause = _snapshot_domain_exists_clause(domain)
        link_params: list[str] = [document_id]
        if domain:
            link_params.extend([domain, channel])
        cur = await conn.execute(
            "SELECT document_snapshot_id FROM asset_document_snapshot_links "
            f"WHERE document_id = %s {domain_clause} ORDER BY linked_at DESC LIMIT 1",
            link_params,
        )
        link = await cur.fetchone()
        if not link:
            raise HTTPException(404, f"No snapshots found for document {document_id}")

        snapshot_id = link["document_snapshot_id"]

        count_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segments WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            "SELECT id, segment_key, segment_index, block_type, semantic_role, "
            "section_title, raw_text, token_count "
            "FROM asset_raw_segments "
            "WHERE document_snapshot_id = %s "
            "ORDER BY segment_index LIMIT %s OFFSET %s",
            [snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/documents/{document_id}/units")
async def get_document_units(
    document_id: str,
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    unit_type: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get retrieval units for a document (via latest snapshot)."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        domain_clause = _snapshot_domain_exists_clause(domain)
        link_params: list[str] = [document_id]
        if domain:
            link_params.extend([domain, channel])
        cur = await conn.execute(
            "SELECT document_snapshot_id FROM asset_document_snapshot_links "
            f"WHERE document_id = %s {domain_clause} ORDER BY linked_at DESC LIMIT 1",
            link_params,
        )
        link = await cur.fetchone()
        if not link:
            raise HTTPException(404, f"No snapshots found for document {document_id}")

        snapshot_id = link["document_snapshot_id"]
        where = "AND unit_type = %s" if unit_type else ""
        params: list[str] = [snapshot_id] + ([unit_type] if unit_type else [])

        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_retrieval_units WHERE document_snapshot_id = %s {where}",
            params,
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT id, unit_key, unit_type, target_type, title, text, "
            f"block_type, semantic_role, weight, created_at "
            f"FROM asset_retrieval_units "
            f"WHERE document_snapshot_id = %s {where} "
            f"ORDER BY created_at LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/documents/{document_id}/relations")
async def get_document_relations(
    document_id: str,
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """Get segment relations for a document (via latest snapshot)."""
    pool = request.app.state.pg_pool

    async with pool.connection() as conn:
        domain_clause = _snapshot_domain_exists_clause(domain)
        link_params: list[str] = [document_id]
        if domain:
            link_params.extend([domain, channel])
        cur = await conn.execute(
            "SELECT document_snapshot_id FROM asset_document_snapshot_links "
            f"WHERE document_id = %s {domain_clause} ORDER BY linked_at DESC LIMIT 1",
            link_params,
        )
        link = await cur.fetchone()
        if not link:
            raise HTTPException(404, f"No snapshots found for document {document_id}")

        snapshot_id = link["document_snapshot_id"]

        count_cur = await conn.execute(
            "SELECT COUNT(*) as c FROM asset_raw_segment_relations WHERE document_snapshot_id = %s",
            [snapshot_id],
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            "SELECT r.id, r.document_snapshot_id, r.source_segment_id, "
            "r.target_segment_id, r.relation_type, r.weight, "
            "r.confidence, r.distance, "
            "s1.raw_text AS source_text, s2.raw_text AS target_text "
            "FROM asset_raw_segment_relations r "
            "LEFT JOIN asset_raw_segments s1 ON s1.id = r.source_segment_id "
            "LEFT JOIN asset_raw_segments s2 ON s2.id = r.target_segment_id "
            "WHERE r.document_snapshot_id = %s "
            "ORDER BY r.confidence DESC NULLS LAST LIMIT %s OFFSET %s",
            [snapshot_id, limit, offset],
        )
        rows = await cur.fetchall()

    return {
        "document_id": document_id, "snapshot_id": snapshot_id,
        "total": total, "limit": limit, "offset": offset,
        "items": [dict(r) for r in rows],
    }


@router.get("/segments")
async def list_segments(
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    role: str | None = None,
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List segments across all documents."""
    pool = request.app.state.pg_pool

    conditions = []
    params: list[str] = []
    domain_filter = _domain_filter_for_table("asset_raw_segments", domain)
    if domain_filter.condition:
        conditions.append(domain_filter.condition)
        params.extend(domain_filter.params(channel))
    if role:
        conditions.append("semantic_role = %s")
        params.append(role)
    if type:
        conditions.append("block_type = %s")
        params.append(type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.connection() as conn:
        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_raw_segments {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT id, document_snapshot_id, segment_key, segment_index, "
            f"block_type, semantic_role, section_title, "
            f"LEFT(raw_text, 200) as raw_text_preview, token_count "
            f"FROM asset_raw_segments {where} "
            f"ORDER BY document_snapshot_id, segment_index LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {"total": total, "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}


class _DomainFilter:
    def __init__(self, condition: str = "") -> None:
        self.condition = condition
        self.where = f"WHERE {condition}" if condition else ""

    def params(self, channel: str) -> list[str]:
        return []


class _ActiveBuildDomainFilter(_DomainFilter):
    def __init__(self, domain: str, snapshot_expr: str) -> None:
        self._domain = domain
        super().__init__(
            "EXISTS ("
            "SELECT 1 FROM asset_publish_releases apr "
            "JOIN asset_build_document_snapshots abds ON abds.build_id = apr.build_id "
            f"WHERE apr.status = 'active' AND apr.domain = %s AND apr.channel = %s "
            f"AND abds.document_snapshot_id = {snapshot_expr}"
            ")"
        )

    def params(self, channel: str) -> list[str]:
        return [self._domain, channel]


class _SimpleDomainFilter(_DomainFilter):
    def __init__(self, domain: str, condition: str) -> None:
        self._domain = domain
        super().__init__(condition)

    def params(self, channel: str) -> list[str]:
        return [self._domain]


class _DocumentDomainFilter(_ActiveBuildDomainFilter):
    def __init__(self, domain: str) -> None:
        self._domain = domain
        _DomainFilter.__init__(
            self,
            "EXISTS ("
            "SELECT 1 FROM asset_document_snapshot_links dsl "
            "JOIN asset_publish_releases apr ON apr.status = 'active' "
            "JOIN asset_build_document_snapshots abds ON abds.build_id = apr.build_id "
            "WHERE dsl.document_id = d.id "
            "AND apr.domain = %s AND apr.channel = %s "
            "AND abds.document_snapshot_id = dsl.document_snapshot_id"
            ")",
        )


def _domain_filter_for_table(table: str, domain: str | None) -> _DomainFilter:
    if not domain:
        return _DomainFilter()
    if table == "asset_source_batches":
        return _SimpleDomainFilter(domain, "domain = %s")
    if table == "asset_builds":
        return _SimpleDomainFilter(domain, "domain = %s")
    if table == "asset_publish_releases":
        return _SimpleDomainFilter(domain, "domain = %s")
    if table == "asset_documents":
        return _DocumentDomainFilter(domain)
    if table == "asset_document_snapshots":
        return _ActiveBuildDomainFilter(domain, "id")
    if table == "asset_raw_segments":
        return _ActiveBuildDomainFilter(domain, "document_snapshot_id")
    if table == "asset_raw_segment_relations":
        return _ActiveBuildDomainFilter(domain, "document_snapshot_id")
    if table == "asset_retrieval_units":
        return _ActiveBuildDomainFilter(domain, "document_snapshot_id")
    if table == "asset_retrieval_embeddings":
        return _ActiveBuildDomainFilter(
            domain,
            "(SELECT ru.document_snapshot_id FROM asset_retrieval_units ru WHERE ru.id = retrieval_unit_id)",
        )
    return _DomainFilter()


def _snapshot_domain_exists_clause(domain: str | None) -> str:
    if not domain:
        return ""
    return (
        "AND EXISTS ("
        "SELECT 1 FROM asset_publish_releases apr "
        "JOIN asset_build_document_snapshots abds ON abds.build_id = apr.build_id "
        "WHERE apr.status = 'active' AND apr.domain = %s AND apr.channel = %s "
        "AND abds.document_snapshot_id = asset_document_snapshot_links.document_snapshot_id"
        ")"
    )


@router.get("/units")
async def list_units(
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    unit_type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List retrieval units across all documents."""
    pool = request.app.state.pg_pool

    conditions = []
    params: list[str] = []
    domain_filter = _domain_filter_for_table("asset_retrieval_units", domain)
    if domain_filter.condition:
        conditions.append(domain_filter.condition)
        params.extend(domain_filter.params(channel))
    if unit_type:
        conditions.append("unit_type = %s")
        params.append(unit_type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.connection() as conn:
        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_retrieval_units {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT id, document_snapshot_id, unit_key, unit_type, target_type, "
            f"title, LEFT(text, 200) as text_preview, block_type, semantic_role, "
            f"weight, created_at "
            f"FROM asset_retrieval_units {where} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {"total": total, "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}


@router.get("/relations")
async def list_relations(
    request: Request,
    domain: str | None = None,
    channel: str = Query("prod"),
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """List segment relations."""
    pool = request.app.state.pg_pool

    conditions = []
    params: list[str] = []
    if domain:
        domain_filter = _ActiveBuildDomainFilter(domain, "r.document_snapshot_id")
        conditions.append(domain_filter.condition)
        params.extend(domain_filter.params(channel))
    if type:
        conditions.append("r.relation_type = %s")
        params.append(type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.connection() as conn:
        count_cur = await conn.execute(
            f"SELECT COUNT(*) as c FROM asset_raw_segment_relations r {where}", params
        )
        total = (await count_cur.fetchone())["c"]

        cur = await conn.execute(
            f"SELECT r.id, r.document_snapshot_id, r.source_segment_id, "
            f"r.target_segment_id, r.relation_type, r.weight, "
            f"r.confidence, r.distance, "
            f"s1.raw_text AS source_text, s2.raw_text AS target_text "
            f"FROM asset_raw_segment_relations r "
            f"LEFT JOIN asset_raw_segments s1 ON s1.id = r.source_segment_id "
            f"LEFT JOIN asset_raw_segments s2 ON s2.id = r.target_segment_id "
            f"{where} "
            f"ORDER BY r.document_snapshot_id LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        rows = await cur.fetchall()

    return {"total": total, "limit": limit, "offset": offset, "items": [dict(r) for r in rows]}
