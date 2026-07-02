from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/debug/runs/{run_id}/cases/{case_id}")
def debug_case(run_id: str, case_id: str, request: Request) -> dict:
    return request.app.state.debug_service.build_case_debug(run_id, case_id).model_dump()


@router.get("/debug/traces/{trace_id}")
def debug_trace(trace_id: str, request: Request) -> dict:
    trace = request.app.state.store.get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"trace not found: {trace_id}")
    return trace


@router.get("/debug/artifacts/{artifact_id}")
def debug_artifact(artifact_id: str, request: Request) -> dict:
    artifact = request.app.state.store.get_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"artifact not found: {artifact_id}")
    return artifact


@router.get("/debug/judge-invocations")
def debug_judge_invocations(
    request: Request, run_id: str | None = None, case_id: str | None = None
) -> dict:
    return {
        "items": request.app.state.store.list_judge_invocations(run_id=run_id, case_id=case_id)
    }
