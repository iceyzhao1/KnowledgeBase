from fastapi import APIRouter, HTTPException, Request

from telecom_eval.api.schemas import (
    CommitDatasetImportRequest,
    DatasetImportPreviewRequest,
)

router = APIRouter()


@router.post("/datasets/{dataset_id}/imports:preview")
def preview_import(dataset_id: str, payload: DatasetImportPreviewRequest, request: Request) -> dict:
    job = request.app.state.dataset_service.preview_import(
        dataset_id, filename=payload.filename, content=payload.content
    )
    return job.model_dump()


@router.post("/datasets/{dataset_id}/imports")
def commit_import(dataset_id: str, payload: CommitDatasetImportRequest, request: Request) -> dict:
    try:
        return request.app.state.dataset_service.commit_import(
            dataset_id,
            import_id=payload.import_id,
            duplicate_policy=payload.duplicate_policy,
            confirm_complete_rows=payload.confirm_complete_rows,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"import not found: {payload.import_id}")


@router.get("/dataset-imports/{import_id}")
def get_import(import_id: str, request: Request) -> dict:
    job = request.app.state.store.load_dataset_import(import_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"import not found: {import_id}")
    return job.model_dump()


@router.get("/dataset-snapshots/{dataset_snapshot_id}")
def get_snapshot(dataset_snapshot_id: str, request: Request) -> dict:
    snapshot = request.app.state.store.load_dataset_snapshot(dataset_snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"snapshot not found: {dataset_snapshot_id}")
    return snapshot.model_dump()
