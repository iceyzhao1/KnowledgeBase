from fastapi import APIRouter, Request

from telecom_eval.api.schemas import RetrievalSearchRequest

router = APIRouter()


@router.post("/retrieval/search")
def retrieval_search(payload: RetrievalSearchRequest, request: Request) -> dict:
    """只读取证：按 query 返回候选原文段落，供案例编辑器关联到标准答案。"""
    search = request.app.state.retrieval_search
    items = search(payload.query, payload.domain, payload.top_k)
    return {"items": items}
