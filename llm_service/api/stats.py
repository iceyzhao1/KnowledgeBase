from __future__ import annotations

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/api/v1")


@router.get("/stats")
async def get_stats(request: Request):
    """Global statistics overview."""
    db = request.app.state.db
    by_status_rows = await db.fetchall("SELECT status, COUNT(*) as cnt FROM agent_llm_tasks GROUP BY status")
    by_status = {row["status"]: row["cnt"] for row in by_status_rows}

    succeeded_row = await db.fetchone("SELECT COUNT(*) as cnt FROM agent_llm_attempts WHERE status = 'succeeded'")
    succeeded_attempts = succeeded_row["cnt"] if succeeded_row else 0

    tokens_row = await db.fetchone("SELECT SUM(total_tokens) as total FROM agent_llm_attempts")
    total_tokens = (tokens_row["total"] if tokens_row else 0) or 0

    latency_row = await db.fetchone("SELECT AVG(latency_ms) as avg_lat FROM agent_llm_attempts WHERE latency_ms IS NOT NULL")
    avg_latency = (latency_row["avg_lat"] if latency_row else 0) or 0

    by_type_rows = await db.fetchall("SELECT task_type, COUNT(*) as cnt FROM agent_llm_tasks GROUP BY task_type")
    by_type = {row["task_type"]: row["cnt"] for row in by_type_rows}

    domain_rows = await db.fetchall("SELECT DISTINCT caller_domain FROM agent_llm_tasks ORDER BY caller_domain")
    domains = [row["caller_domain"] for row in domain_rows]

    stage_rows = await db.fetchall("SELECT DISTINCT pipeline_stage FROM agent_llm_tasks ORDER BY pipeline_stage")
    stages = [row["pipeline_stage"] for row in stage_rows]

    # Token usage by domain
    token_domain_rows = await db.fetchall(
        """SELECT t.caller_domain, SUM(a.total_tokens) as tokens
           FROM agent_llm_attempts a
           JOIN agent_llm_tasks t ON t.id = a.task_id
           GROUP BY t.caller_domain"""
    )
    tokens_by_domain = {row["caller_domain"]: row["tokens"] or 0 for row in token_domain_rows}

    # Recent failures count
    recent_fail_row = await db.fetchone(
        "SELECT COUNT(*) as cnt FROM agent_llm_tasks WHERE status = 'failed' AND created_at > NOW() - INTERVAL '1 hour'"
    )
    recent_failures = recent_fail_row["cnt"] if recent_fail_row else 0

    # Success rate
    total_tasks = sum(by_status.values())
    success_count = by_status.get("succeeded", 0)
    success_rate = round(success_count / total_tasks, 4) if total_tasks > 0 else 0.0

    return {
        "success": True,
        "data": {
            "tasks_by_status": by_status,
            "tasks_by_type": by_type,
            "succeeded_attempts": succeeded_attempts,
            "total_tokens": total_tokens,
            "avg_latency_ms": round(avg_latency, 1),
            "success_rate": success_rate,
            "domains": domains,
            "stages": stages,
            "recent_failures": recent_failures,
            "tokens_by_domain": tokens_by_domain,
        },
    }


@router.get("/stats/tokens")
async def get_token_stats(
    request: Request,
    domain: str = Query(None, description="Filter by caller_domain"),
    stage: str = Query(None, description="Filter by pipeline_stage"),
):
    """Token usage statistics with optional filters."""
    db = request.app.state.db
    conditions = []
    params = []
    if domain:
        conditions.append("t.caller_domain = %s")
        params.append(domain)
    if stage:
        conditions.append("t.pipeline_stage = %s")
        params.append(stage)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    rows = await db.fetchall(
        f"""SELECT t.caller_domain, t.pipeline_stage, t.task_type,
                   SUM(a.total_tokens) as total_tokens,
                   COUNT(*) as attempt_count,
                   AVG(a.latency_ms) as avg_latency
           FROM agent_llm_attempts a
           JOIN agent_llm_tasks t ON t.id = a.task_id
           {where}
           GROUP BY t.caller_domain, t.pipeline_stage, t.task_type
           ORDER BY total_tokens DESC""",
        tuple(params),
    )
    return {"success": True, "data": rows}


@router.get("/tasks")
async def list_tasks(
    request: Request,
    status: str = Query(None),
    domain: str = Query(None),
    stage: str = Query(None),
    task_type: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Task list with pagination and filters."""
    db = request.app.state.db
    conditions = []
    params = []
    if status:
        conditions.append("t.status = %s")
        params.append(status)
    if domain:
        conditions.append("t.caller_domain = %s")
        params.append(domain)
    if stage:
        conditions.append("t.pipeline_stage = %s")
        params.append(stage)
    if task_type:
        conditions.append("t.task_type = %s")
        params.append(task_type)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_row = await db.fetchone(
        f"SELECT COUNT(*) as cnt FROM agent_llm_tasks t{where}",
        tuple(params),
    )
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    tasks = await db.fetchall(
        f"""SELECT t.id, t.caller_domain, t.pipeline_stage, t.status, t.task_type,
                   t.attempt_count, t.max_attempts, t.priority, t.created_at,
                   t.started_at, t.finished_at, t.metadata_json
            FROM agent_llm_tasks t
            {where}
            ORDER BY t.created_at DESC
            LIMIT %s OFFSET %s""",
        tuple(params) + (page_size, offset),
    )

    return {
        "success": True,
        "data": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": tasks,
        },
    }
