from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from main_control_service.config_provider import YamlConfigProvider


@dataclass(slots=True)
class YamlBackedService:
    repo_root: Path
    config_dir: Path
    _provider: YamlConfigProvider = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._provider = YamlConfigProvider(config_dir=self.config_dir, repo_root=self.repo_root)

    def ensure_ready(self) -> None:
        self._provider.ensure_initialized()

    def reload(self) -> dict[str, Any]:
        self._provider._init_from_project()
        domains = self._provider.list_domains()
        return {"imported_domains": len(domains)}

    # ── Domain ────────────────────────────────────────────────────────

    def list_domains(self) -> list[dict[str, Any]]:
        return self._provider.list_domains()

    def get_domain(self, domain_id: str) -> dict[str, Any]:
        result = self._provider.get_domain(domain_id)
        if not result:
            raise HTTPException(status_code=404, detail="domain_not_found")
        return result

    def get_scenario(self, domain_id: str, section: str | None = None) -> dict[str, Any]:
        self.get_domain(domain_id)  # 404 if not found
        if section:
            return self._provider.load_scenario_section(domain_id, section)
        return self._provider.load_scenario_pack(domain_id)

    # ── System config ─────────────────────────────────────────────────

    def get_system_config(self, service_name: str) -> dict[str, Any]:
        result = self._provider.load_system_config(service_name)
        if not result:
            raise HTTPException(status_code=404, detail="config_not_found")
        return result
