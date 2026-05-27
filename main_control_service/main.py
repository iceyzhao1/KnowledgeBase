from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from main_control_service.config import MainControlSettings
from main_control_service.service import YamlConfigService


def create_app(
    *,
    config_dir: Path | None = None,
    settings: MainControlSettings | None = None,
) -> FastAPI:
    cfg = settings or MainControlSettings()
    effective_config_dir = config_dir or cfg.config_dir
    service = YamlConfigService(config_dir=effective_config_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.main_control = service
        yield

    app = FastAPI(
        title="Main Control Service",
        version="2.0.0",
        description="YAML config center for CoreMasterKB services — full CRUD.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "yaml_crud"}

    # ------------------------------------------------------------------
    # System config — YAML text passthrough
    # ------------------------------------------------------------------

    @app.get("/api/v1/system")
    def list_system_configs() -> dict:
        return {"items": service.list_system_configs()}

    @app.get("/api/v1/system/{service_name}")
    def get_system_config(service_name: str) -> dict:
        return service.get_system_config(service_name)

    @app.get("/api/v1/system/{service_name}/raw")
    def get_system_config_raw(service_name: str) -> str:
        return service.get_system_config_yaml(service_name)

    @app.put("/api/v1/system/{service_name}/raw")
    async def update_system_config_raw(service_name: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_system_config_yaml(service_name, text)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Domains — JSON list + YAML text CRUD
    # ------------------------------------------------------------------

    @app.get("/api/v1/domains")
    def list_domains() -> dict:
        return {"items": service.list_domains()}

    @app.get("/api/v1/domains/{domain_id}")
    def get_domain(domain_id: str) -> dict:
        return service.get_domain(domain_id)

    @app.get("/api/v1/domains/{domain_id}/raw")
    def get_domain_raw(domain_id: str) -> str:
        return service.get_domain_yaml(domain_id)

    @app.post("/api/v1/domains")
    async def create_domain(request: Request) -> dict:
        body = await request.json()
        domain_id = body.get("domain_id")
        if not domain_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="domain_id is required")
        return service.create_domain(domain_id, body)

    @app.put("/api/v1/domains/{domain_id}/raw")
    async def update_domain_raw(domain_id: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_domain_yaml(domain_id, text)
        return {"ok": True}

    @app.delete("/api/v1/domains/{domain_id}")
    def delete_domain(domain_id: str) -> dict:
        service.delete_domain(domain_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Scenario packs — YAML text passthrough
    # ------------------------------------------------------------------

    @app.get("/api/v1/domains/{domain_id}/scenario")
    def get_scenario(domain_id: str, section: str | None = None) -> dict:
        return service.get_scenario(domain_id, section)

    @app.get("/api/v1/domains/{domain_id}/scenario/raw")
    def get_scenario_raw(domain_id: str) -> str:
        return service.get_scenario_yaml(domain_id)

    @app.put("/api/v1/domains/{domain_id}/scenario/raw")
    async def update_scenario_raw(domain_id: str, request: Request) -> dict:
        body = await request.body()
        text = body.decode("utf-8")
        service.update_scenario_yaml(domain_id, text)
        return {"ok": True}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    cfg = MainControlSettings()
    uvicorn.run("main_control_service.main:app", host=cfg.host, port=cfg.port, reload=False)
