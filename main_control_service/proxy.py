"""Reverse-proxy module — streams requests to per-domain backend services."""

from __future__ import annotations

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse


# Service key → field name in domain_registry.yaml services section
SERVICE_MAP: dict[str, str] = {
    "llm": "llm_url",
    "mining": "mining_url",
    "serving": "serving_url",
}

# Shared client — created once in lifespan, reused across requests.
_proxy_client: httpx.AsyncClient | None = None


def create_proxy_client() -> httpx.AsyncClient:
    """Call once during app lifespan."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        follow_redirects=False,
    )


def get_proxy_client() -> httpx.AsyncClient:
    if _proxy_client is None:
        raise RuntimeError("Proxy client not initialized — call init in lifespan")
    return _proxy_client


def set_proxy_client(client: httpx.AsyncClient) -> None:
    global _proxy_client
    _proxy_client = client


async def shutdown_proxy_client() -> None:
    global _proxy_client
    if _proxy_client is not None:
        await _proxy_client.aclose()
        _proxy_client = None


def _resolve_target_url(domain_services: dict, service: str) -> str:
    """Look up the internal URL for a (domain, service) pair."""
    field = SERVICE_MAP.get(service)
    if field is None:
        raise HTTPException(status_code=400, detail=f"Unknown service: {service}")
    url = domain_services.get(field)
    if not url:
        raise HTTPException(status_code=502, detail=f"Service {service} not configured for this domain")
    return url.rstrip("/")


async def proxy_request(
    request: Request,
    domain_id: str,
    service: str,
    path: str,
    domain_services: dict,
) -> Response:
    """Stream a request to the target backend and stream the response back."""
    target_base = _resolve_target_url(domain_services, service)
    target_url = f"{target_base}/{path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    client = get_proxy_client()

    # Build upstream headers — forward original context
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    body = await request.body()

    try:
        upstream = client.stream(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
        resp = await upstream.__aenter__()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"Backend {service} unreachable: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"Backend {service} timeout: {exc}") from exc

    # Stream response back
    async def _stream():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await upstream.__aexit__(None, None, None)

    # Filter out hop-by-hop headers
    response_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("transfer-encoding", "connection", "keep-alive")
    }

    return StreamingResponse(
        _stream(),
        status_code=resp.status_code,
        headers=response_headers,
        media_type=resp.headers.get("content-type"),
    )
