from fastapi import APIRouter, Request

from telecom_eval.api.config import build_model_catalog

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "telecom_eval", "version": "2.0"}


@router.get("/models")
def models(request: Request) -> dict:
    return build_model_catalog(request.app.state.config)
