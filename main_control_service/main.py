from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from main_control_service.config import MainControlSettings
from main_control_service.models import (
    CapabilityReplaceRequest,
    DatabaseBindingReplaceRequest,
    DomainPatchRequest,
    RuntimeOverrideReplaceRequest,
    ServiceBindingReplaceRequest,
)
from main_control_service.service import YamlBackedService

READONLY_HEADER = {"X-Control-Plane-Mode": "yaml_readonly"}


def create_app(
    *,
    repo_root: Path | None = None,
    config_dir: Path | None = None,
    settings: MainControlSettings | None = None,
) -> FastAPI:
    cfg = settings or MainControlSettings()
    effective_repo_root = repo_root or cfg.repo_root
    effective_config_dir = config_dir or cfg.config_dir
    service = YamlBackedService(repo_root=effective_repo_root, config_dir=effective_config_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.ensure_ready()
        app.state.main_control = service
        yield

    app = FastAPI(
        title="Main Control Service",
        version="0.2.0",
        description="YAML-backed read-only control plane for multi-domain CoreMasterKB.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ────────────────────────────────────────────────────────

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "yaml_readonly"}

    # ── Bootstrap ─────────────────────────────────────────────────────

    @app.post("/api/bootstrap/import-current-state")
    def bootstrap_import() -> dict:
        return service.bootstrap_import()

    # ── Old read endpoints ────────────────────────────────────────────

    @app.get("/api/domains")
    def list_domains() -> dict:
        return {"items": service.list_domains()}

    @app.get("/api/domains/{domain_id}")
    def get_domain(domain_id: str) -> dict:
        return service.get_domain(domain_id)

    @app.get("/api/domains/{domain_id}/runtime")
    def get_runtime(domain_id: str) -> dict:
        return service.get_runtime(domain_id)

    @app.get("/api/domains/{domain_id}/observations")
    def get_observations(domain_id: str) -> dict:
        return service.get_observations(domain_id)

    @app.get("/api/domains/{domain_id}/diff")
    def get_diff(domain_id: str) -> dict:
        return service.get_diff(domain_id)

    # ── Old write endpoints (memory-only, readonly header) ────────────

    @app.patch("/api/domains/{domain_id}")
    def patch_domain(domain_id: str, body: DomainPatchRequest) -> dict:
        return service.patch_domain(domain_id, body.model_dump(exclude_none=True))

    @app.put("/api/domains/{domain_id}/capabilities")
    def put_capabilities(domain_id: str, body: CapabilityReplaceRequest) -> dict:
        return {"items": service.replace_capabilities(domain_id, [item.model_dump() for item in body.capabilities])}

    @app.put("/api/domains/{domain_id}/service-bindings")
    def put_service_bindings(domain_id: str, body: ServiceBindingReplaceRequest) -> dict:
        return {"items": service.replace_service_bindings(domain_id, [item.model_dump() for item in body.bindings])}

    @app.put("/api/domains/{domain_id}/database-bindings")
    def put_database_bindings(domain_id: str, body: DatabaseBindingReplaceRequest) -> dict:
        return {"items": service.replace_database_bindings(domain_id, [item.model_dump() for item in body.bindings])}

    @app.put("/api/domains/{domain_id}/overrides")
    def put_overrides(domain_id: str, body: RuntimeOverrideReplaceRequest) -> dict:
        return {"items": service.replace_runtime_overrides(domain_id, [item.model_dump() for item in body.overrides])}

    @app.get("/api/service-instances")
    def list_service_instances() -> dict:
        return {"items": service.list_service_instances()}

    @app.get("/api/audit-logs")
    def list_audit_logs() -> dict:
        return {"items": service.list_audit_logs()}

    # ── V1 endpoints — direct YAML access ─────────────────────────────

    @app.get("/api/v1/domains")
    def v1_list_domains() -> dict:
        return {"items": service.v1_list_domains()}

    @app.get("/api/v1/domains/{domain_id}")
    def v1_get_domain(domain_id: str) -> dict:
        result = service.v1_get_domain(domain_id)
        if not result:
            return JSONResponse(status_code=404, content={"detail": "domain_not_found"})
        return result

    @app.get("/api/v1/domains/{domain_id}/scenario")
    def v1_get_scenario(domain_id: str, section: str | None = None) -> dict:
        return service.v1_get_scenario(domain_id, section)

    @app.get("/api/v1/system/{service_name}")
    def v1_get_system_config(service_name: str) -> dict:
        result = service.v1_get_system_config(service_name)
        if not result:
            return JSONResponse(status_code=404, content={"detail": "config_not_found"})
        return result

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    cfg = MainControlSettings()
    uvicorn.run("main_control_service.main:app", host=cfg.host, port=cfg.port, reload=False)
