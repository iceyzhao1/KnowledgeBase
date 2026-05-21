from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from main_control_service.config_provider import YamlConfigProvider, parse_simple_env


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class YamlBackedService:
    repo_root: Path
    config_dir: Path
    _provider: YamlConfigProvider = field(init=False, repr=False)
    _memory_overlays: dict[str, dict[str, Any]] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._provider = YamlConfigProvider(config_dir=self.config_dir, repo_root=self.repo_root)

    def ensure_ready(self) -> None:
        self._provider.ensure_initialized()

    # ------------------------------------------------------------------
    # Bootstrap / reload
    # ------------------------------------------------------------------

    def bootstrap_import(self) -> dict[str, Any]:
        """Re-initialize config from project YAML/env files."""
        self._memory_overlays.clear()
        self._provider._init_from_project()
        domains = self._provider.list_domains()
        return {"imported_domains": len(domains), "service_instances": 3}

    # ------------------------------------------------------------------
    # Old API — read methods (YAML-backed with overlay merge)
    # ------------------------------------------------------------------

    def list_domains(self) -> list[dict[str, Any]]:
        items = self._provider.list_domains()
        for item in items:
            overlay = self._memory_overlays.get(item["domain_id"], {}).get("domain")
            if overlay:
                item.update(overlay)
            item["capabilities"] = self._build_capabilities(item["domain_id"])
        return items

    def get_domain(self, domain_id: str) -> dict[str, Any]:
        base = self._provider.get_domain(domain_id)
        if not base:
            raise HTTPException(status_code=404, detail="domain_not_found")
        overlay = self._memory_overlays.get(domain_id, {}).get("domain")
        if overlay:
            base.update(overlay)
        base["capabilities"] = self._build_capabilities(domain_id)
        base["service_bindings"] = self._build_service_bindings(domain_id)
        base["database_bindings"] = self._build_database_bindings(domain_id)
        base["overrides"] = self._build_overrides(domain_id)
        return base

    def get_runtime(self, domain_id: str) -> dict[str, Any]:
        domain = self.get_domain(domain_id)
        instance_lookup = {inst["instance_id"]: inst for inst in self.list_service_instances()}
        service_bindings: dict[str, Any] = {}
        for binding in domain["service_bindings"]:
            instance = instance_lookup.get(binding["instance_id"])
            service_bindings[binding["service_name"]] = {
                "instance_id": binding["instance_id"],
                "binding_mode": binding["binding_mode"],
                "priority": binding["priority"],
                "base_url": instance["base_url"] if instance else None,
                "healthcheck_url": instance["healthcheck_url"] if instance else None,
            }
        database_bindings = {
            item["usage_type"]: {
                "binding_id": item["binding_id"],
                "secret_ref": item["secret_ref"],
                "driver": item["driver"],
                "database_name": item["database_name"],
                "schema_name": item["schema_name"],
                "readonly": item["readonly"],
            }
            for item in domain["database_bindings"]
        }
        overrides: dict[str, Any] = {}
        for override in domain["overrides"]:
            overrides.setdefault(override["service_name"], {})[override["config_scope"]] = override["config_json"]
        return {
            "domain": domain["domain_id"],
            "display_name": domain["display_name"],
            "enabled": domain["enabled"],
            "default_channel": domain["default_channel"],
            "scenario_pack": {"ref": domain["scenario_pack_ref"], "version": "phase1"},
            "capabilities": {item["service_name"]: item["enabled"] for item in domain["capabilities"]},
            "service_bindings": service_bindings,
            "database_bindings": database_bindings,
            "overrides": overrides,
            "control_plane_mode": "observe_only",
        }

    def get_observations(self, domain_id: str) -> dict[str, Any]:
        self._provider.get_domain(domain_id) or self._raise_not_found(domain_id)
        registry = self._provider.load_domain_registry()
        entry = registry.get(domain_id, {})
        env_root = parse_simple_env(self.repo_root / ".env")
        env_kbui = parse_simple_env(self.repo_root / "kb-ui" / ".env.development")
        serving_app = self._read_file_text(
            self.repo_root / "agent_serving_java" / "src" / "main" / "resources" / "application.yml"
        )
        scenario_pack_exists = (
            self.repo_root / "scenario_packs" / entry.get("scenario_pack", domain_id) / "domain.yaml"
        ).exists()
        return {
            "domain": domain_id,
            "runtime_mode": "local_runtime",
            "knowledge_mining": {
                "current_config_source": "yaml_readonly",
                "domain_registry_path": "domain_registry.yaml",
                "scenario_pack_source": f"scenario_packs/{entry.get('scenario_pack', domain_id)}",
                "default_channel": entry.get("default_channel", "prod"),
                "database_secret_ref": f"env:{entry['database_url_env']}" if entry.get("database_url_env") else None,
            },
            "agent_serving_java": {
                "current_config_source": "yaml_readonly",
                "domain_registry_path": "domain_registry.yaml",
                "scenario_pack_source": f"scenario_packs/{entry.get('scenario_pack', domain_id)}",
                "default_domain_hint": self._extract_property(serving_app, "default-domain"),
                "llm_service_url": self._extract_property(serving_app, "base-url"),
            },
            "llm_service": {
                "current_config_source": "yaml_readonly",
                "default_base_url": env_root.get("LLM_SERVICE_URL", "http://localhost:8900"),
                "embedding_model": env_root.get("EMBEDDING_MODEL", "embedding-3"),
                "rerank_model": env_root.get("RERANK_MODEL", "rerank-pro"),
            },
            "kb_ui": {
                "current_config_source": "yaml_readonly",
                "mining_api_base": env_kbui.get("VITE_MINING_API_BASE", "http://localhost:8901"),
                "serving_api_base": env_kbui.get("VITE_SERVING_API_BASE", "http://localhost:8081"),
                "llm_api_base": env_kbui.get("VITE_LLM_API_BASE", "http://localhost:8900"),
            },
            "scenario_pack_exists": scenario_pack_exists,
        }

    def get_diff(self, domain_id: str) -> dict[str, Any]:
        runtime = self.get_runtime(domain_id)
        observations = self.get_observations(domain_id)
        diff_items: list[dict[str, Any]] = []
        diff_items.append(
            {
                "field": "runtime_mode",
                "control_plane_value": runtime["control_plane_mode"],
                "observed_value": observations["runtime_mode"],
                "status": "match" if runtime["control_plane_mode"] == observations["runtime_mode"] else "mismatch",
            }
        )
        observed_bindings = {
            "mining": "mining-default",
            "serving": "serving-default",
            "llm": "llm-default",
        }
        for service_name, observed_instance in observed_bindings.items():
            current = runtime["service_bindings"].get(service_name, {}).get("instance_id")
            diff_items.append(
                {
                    "field": f"service_binding.{service_name}",
                    "control_plane_value": current,
                    "observed_value": observed_instance,
                    "status": "match" if current == observed_instance else "mismatch",
                }
            )
        return {"domain": domain_id, "items": diff_items}

    # ------------------------------------------------------------------
    # Old API — write methods (memory-only overlays)
    # ------------------------------------------------------------------

    def patch_domain(self, domain_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        before = self.get_domain(domain_id)
        overlay_domain = {
            k: patch[k]
            for k in ("display_name", "enabled", "default_channel", "scenario_pack_ref", "description", "owner_team", "metadata_json")
            if k in patch
        }
        self._memory_overlays.setdefault(domain_id, {}).setdefault("domain", {}).update(overlay_domain)
        return self.get_domain(domain_id)

    def replace_capabilities(self, domain_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_domain_exists(domain_id)
        self._memory_overlays.setdefault(domain_id, {})["capabilities"] = items
        return self._build_capabilities(domain_id)

    def replace_service_bindings(self, domain_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_domain_exists(domain_id)
        self._memory_overlays.setdefault(domain_id, {})["service_bindings"] = items
        return self._build_service_bindings(domain_id)

    def replace_database_bindings(self, domain_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_domain_exists(domain_id)
        normalized = [{**item, "binding_id": f"{domain_id}:{item['usage_type']}"} for item in items]
        self._memory_overlays.setdefault(domain_id, {})["database_bindings"] = normalized
        return self._build_database_bindings(domain_id)

    def replace_runtime_overrides(self, domain_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_domain_exists(domain_id)
        normalized = [
            {**item, "override_id": f"{domain_id}:{item['service_name']}:{item['config_scope']}"} for item in items
        ]
        self._memory_overlays.setdefault(domain_id, {})["overrides"] = normalized
        return self._build_overrides(domain_id)

    def list_service_instances(self) -> list[dict[str, Any]]:
        env_kbui = parse_simple_env(self.repo_root / "kb-ui" / ".env.development")
        env_root = parse_simple_env(self.repo_root / ".env")
        return [
            {
                "instance_id": "mining-default",
                "service_name": "mining",
                "display_name": "Mining Default",
                "base_url": env_kbui.get("VITE_MINING_API_BASE", "http://localhost:8901"),
                "healthcheck_url": env_kbui.get("VITE_MINING_API_BASE", "http://localhost:8901").rstrip("/") + "/health",
                "environment": "dev",
                "enabled": True,
                "metadata_json": {"source": "yaml_readonly"},
            },
            {
                "instance_id": "serving-default",
                "service_name": "serving",
                "display_name": "Serving Default",
                "base_url": env_kbui.get("VITE_SERVING_API_BASE", "http://localhost:8081"),
                "healthcheck_url": env_kbui.get("VITE_SERVING_API_BASE", "http://localhost:8081").rstrip("/") + "/actuator/health",
                "environment": "dev",
                "enabled": True,
                "metadata_json": {"source": "yaml_readonly"},
            },
            {
                "instance_id": "llm-default",
                "service_name": "llm",
                "display_name": "LLM Default",
                "base_url": env_kbui.get("VITE_LLM_API_BASE", env_root.get("LLM_SERVICE_URL", "http://localhost:8900")),
                "healthcheck_url": env_kbui.get("VITE_LLM_API_BASE", env_root.get("LLM_SERVICE_URL", "http://localhost:8900")).rstrip("/") + "/health",
                "environment": "dev",
                "enabled": True,
                "metadata_json": {"source": "yaml_readonly"},
            },
        ]

    def list_audit_logs(self) -> list[dict[str, Any]]:
        return []

    # ------------------------------------------------------------------
    # V1 API — direct YAML access
    # ------------------------------------------------------------------

    def v1_list_domains(self) -> list[dict[str, Any]]:
        return self._provider.list_domains()

    def v1_get_domain(self, domain_id: str) -> dict[str, Any] | None:
        return self._provider.get_domain(domain_id)

    def v1_get_scenario(self, domain_id: str, section: str | None = None) -> dict[str, Any]:
        if section:
            return self._provider.load_scenario_section(domain_id, section)
        return self._provider.load_scenario_pack(domain_id)

    def v1_get_system_config(self, service_name: str) -> dict[str, Any]:
        return self._provider.load_system_config(service_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_domain_exists(self, domain_id: str) -> None:
        if not self._provider.get_domain(domain_id):
            raise HTTPException(status_code=404, detail="domain_not_found")

    def _raise_not_found(self, domain_id: str) -> None:
        raise HTTPException(status_code=404, detail="domain_not_found")

    def _build_capabilities(self, domain_id: str) -> list[dict[str, Any]]:
        overlay = self._memory_overlays.get(domain_id, {}).get("capabilities")
        if overlay is not None:
            return overlay
        return [
            {"domain_id": domain_id, "service_name": "mining", "enabled": True, "rollout_state": "observe_only", "notes": "yaml_readonly"},
            {"domain_id": domain_id, "service_name": "serving", "enabled": True, "rollout_state": "observe_only", "notes": "yaml_readonly"},
            {"domain_id": domain_id, "service_name": "llm", "enabled": True, "rollout_state": "observe_only", "notes": "yaml_readonly"},
            {"domain_id": domain_id, "service_name": "ui", "enabled": True, "rollout_state": "control_plane_enabled", "notes": "yaml_readonly"},
        ]

    def _build_service_bindings(self, domain_id: str) -> list[dict[str, Any]]:
        overlay = self._memory_overlays.get(domain_id, {}).get("service_bindings")
        if overlay is not None:
            return overlay
        return [
            {"domain_id": domain_id, "service_name": "mining", "instance_id": "mining-default", "binding_mode": "shared", "priority": 100, "notes": "yaml_readonly"},
            {"domain_id": domain_id, "service_name": "serving", "instance_id": "serving-default", "binding_mode": "shared", "priority": 100, "notes": "yaml_readonly"},
            {"domain_id": domain_id, "service_name": "llm", "instance_id": "llm-default", "binding_mode": "shared", "priority": 100, "notes": "yaml_readonly"},
        ]

    def _build_database_bindings(self, domain_id: str) -> list[dict[str, Any]]:
        overlay = self._memory_overlays.get(domain_id, {}).get("database_bindings")
        if overlay is not None:
            return overlay
        registry = self._provider.load_domain_registry()
        db_env = registry.get(domain_id, {}).get("database_url_env")
        return [
            {
                "binding_id": f"{domain_id}:asset_core",
                "domain_id": domain_id,
                "usage_type": "asset_core",
                "secret_ref": f"env:{db_env}" if db_env else "env:PG_DBNAME",
                "driver": "postgresql",
                "database_name": None,
                "schema_name": "public",
                "readonly": False,
                "notes": "yaml_readonly",
            },
            {
                "binding_id": f"{domain_id}:mining_runtime",
                "domain_id": domain_id,
                "usage_type": "mining_runtime",
                "secret_ref": f"env:{db_env}" if db_env else "env:PG_DBNAME",
                "driver": "postgresql",
                "database_name": None,
                "schema_name": "public",
                "readonly": False,
                "notes": "yaml_readonly",
            },
        ]

    def _build_overrides(self, domain_id: str) -> list[dict[str, Any]]:
        overlay = self._memory_overlays.get(domain_id, {}).get("overrides")
        if overlay is not None:
            return overlay
        return []

    @staticmethod
    def _read_file_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _extract_property(text: str, property_name: str) -> str | None:
        pattern = re.compile(rf"{re.escape(property_name)}:\s*(.+)")
        match = pattern.search(text)
        return match.group(1).strip() if match else None
