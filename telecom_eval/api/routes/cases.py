from fastapi import APIRouter, HTTPException, Request

from telecom_eval.api.schemas import UpdateCaseRequest

router = APIRouter()


@router.get("/cases/{case_id}")
def get_case(case_id: str, request: Request) -> dict:
    case = request.app.state.store.load_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
    return case.model_dump()


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, request: Request) -> dict:
    try:
        dataset_id = request.app.state.dataset_service.delete_case(case_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
    return {"deleted": True, "case_id": case_id, "dataset_id": dataset_id}


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, request: Request) -> dict:
    deleted = request.app.state.dataset_service.delete_case(case_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
    return {"deleted": True, "case_id": case_id}


@router.patch("/cases/{case_id}")
def update_case(case_id: str, payload: UpdateCaseRequest, request: Request) -> dict:
    try:
        case = request.app.state.dataset_service.update_case(
            case_id,
            payload.model_dump(exclude_none=True, exclude={"allow_confirmed_edit"}),
            allow_confirmed_edit=payload.allow_confirmed_edit,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"case not found: {case_id}")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid case update: {exc}")
    return case.model_dump()
