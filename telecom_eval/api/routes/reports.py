from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/runs/{run_id}/report")
def get_run_report(run_id: str, request: Request) -> dict:
    try:
        return request.app.state.report_view_service.build_run_report(run_id).model_dump()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
