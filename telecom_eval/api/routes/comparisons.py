from fastapi import APIRouter, HTTPException, Request

from telecom_eval.api.schemas import CreateComparisonRequest

router = APIRouter()


@router.post("/comparisons")
def create_comparison(payload: CreateComparisonRequest, request: Request) -> dict:
    view = request.app.state.report_view_service.create_comparison(
        baseline_run_id=payload.baseline_run_id,
        candidate_run_id=payload.candidate_run_id,
        metric_id=payload.metric_id,
        comparison_type=payload.comparison_type,
    )
    return view.model_dump()


@router.get("/comparisons/{comparison_id}")
@router.get("/comparisons/{comparison_id}/report")
def get_comparison(comparison_id: str, request: Request) -> dict:
    view = request.app.state.report_view_service.build_comparison_report(comparison_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"comparison not found: {comparison_id}")
    return view.model_dump()
