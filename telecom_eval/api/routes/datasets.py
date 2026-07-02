from fastapi import APIRouter, HTTPException, Request

from telecom_eval.api.schemas import (
    AddCaseRequest,
    CreateDatasetRequest,
    CreateSnapshotRequest,
    UpdateDatasetRequest,
)

router = APIRouter()


@router.get("/datasets")
def list_datasets(request: Request, status: str | None = None) -> dict:
    svc = request.app.state.dataset_service
    items = []
    for d in svc.list_datasets(status=status):
        row = d.model_dump()
        # 兼容旧前端字段
        row["confirmed_count"] = d.confirmed_case_count
        items.append(row)
    return {"items": items}


@router.post("/datasets")
def create_dataset(payload: CreateDatasetRequest, request: Request) -> dict:
    svc = request.app.state.dataset_service
    dataset = svc.create_dataset(
        name=payload.name,
        scenario_id=payload.scenario_id,
        dataset_type=payload.dataset_type,
        description=payload.description,
        tags=payload.tags,
        owner=payload.owner,
    )
    return dataset.model_dump()


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, request: Request) -> dict:
    dataset = request.app.state.dataset_service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return dataset.model_dump()


@router.patch("/datasets/{dataset_id}")
def update_dataset(dataset_id: str, payload: UpdateDatasetRequest, request: Request) -> dict:
    try:
        dataset = request.app.state.dataset_service.update_dataset(
            dataset_id, payload.model_dump(exclude_none=True)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return dataset.model_dump()


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, request: Request) -> dict:
    deleted = request.app.state.store.delete_dataset(dataset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return {"deleted": True, "dataset_id": dataset_id}


@router.get("/datasets/{dataset_id}/cases")
def list_dataset_cases(dataset_id: str, request: Request, confirmed_only: bool = False) -> dict:
    cases = request.app.state.store.list_cases(dataset_id=dataset_id, confirmed_only=confirmed_only)
    return {"items": [c.model_dump() for c in cases]}


@router.post("/datasets/{dataset_id}/cases")
def add_dataset_case(dataset_id: str, payload: AddCaseRequest, request: Request) -> dict:
    try:
        case = request.app.state.dataset_service.add_case(dataset_id, payload.case)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid case: {exc}")
    return case.model_dump()


@router.post("/datasets/{dataset_id}/cases:confirm")
def confirm_dataset_cases(dataset_id: str, request: Request) -> dict:
    if request.app.state.dataset_service.get_dataset(dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"dataset not found: {dataset_id}")
    return request.app.state.dataset_service.confirm_dataset_drafts(dataset_id)


@router.post("/datasets/{dataset_id}/snapshots")
def create_snapshot(dataset_id: str, payload: CreateSnapshotRequest, request: Request) -> dict:
    snapshot = request.app.state.dataset_service.create_snapshot(
        dataset_id, confirmed_only=payload.confirmed_only, gold_policy=payload.gold_policy
    )
    return snapshot.model_dump()
