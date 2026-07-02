from fastapi import APIRouter, HTTPException, Request

from telecom_eval.api.schemas import CreateRunRequest

router = APIRouter()


@router.post("/runs")
def create_run(payload: CreateRunRequest, request: Request) -> dict:
    run = request.app.state.evaluation_service.create_run(payload, execute_immediately=False)
    request.app.state.evaluation_queue_service.enqueue(run["run_id"])
    return run


@router.get("/runs")
def list_runs(request: Request, dataset_id: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    return {"items": request.app.state.store.list_runs(dataset_id=dataset_id, limit=limit, offset=offset)}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    try:
        return request.app.state.evaluation_service.get_run_summary(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, request: Request) -> dict:
    deleted = request.app.state.store.delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return {"deleted": True, "run_id": run_id}


@router.get("/runs/{run_id}/cases")
def list_run_cases(run_id: str, request: Request) -> dict:
    store = request.app.state.store
    try:
        payload = store.load_run_payload(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    diagnoses = {d["case_id"]: d for d in store.list_run_diagnoses(run_id)}
    rows = []
    for case_id in payload.get("case_ids", []):
        case_metrics = store.list_run_metrics(run_id, case_id)
        metrics = {m.metric_id: m.value for m in case_metrics}
        metric_statuses = {m.metric_id: m.status for m in case_metrics}
        diag = diagnoses.get(case_id)
        rows.append(
            {
                "case_id": case_id,
                "metrics": metrics,
                "metric_statuses": metric_statuses,
                "failure_type": diag["failure_type"] if diag else None,
                "severity": diag["severity"] if diag else None,
            }
        )
    return {"items": rows}


@router.get("/runs/{run_id}/cases/{case_id}")
def get_run_case_debug(run_id: str, case_id: str, request: Request) -> dict:
    return request.app.state.debug_service.build_case_debug(run_id, case_id).model_dump()
