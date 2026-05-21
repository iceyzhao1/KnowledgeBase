from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def parse_simple_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


@dataclass(slots=True)
class YamlConfigProvider:
    config_dir: Path
    repo_root: Path

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def ensure_initialized(self) -> None:
        """Populate config_dir from repo_root if empty / missing."""
        registry = self.config_dir / "domain_registry.yaml"
        if registry.exists():
            return
        self._init_from_project()

    def _init_from_project(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 1. domain_registry.yaml
        src = self.repo_root / "domain_registry.yaml"
        if src.exists():
            shutil.copy2(src, self.config_dir / "domain_registry.yaml")

        # 2. scenario packs
        sp_src = self.repo_root / "scenario_packs"
        if sp_src.exists():
            for pack_dir in sp_src.iterdir():
                if pack_dir.is_dir():
                    domain_yaml = pack_dir / "domain.yaml"
                    if domain_yaml.exists():
                        dst = self.config_dir / "scenario_packs" / pack_dir.name
                        dst.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(domain_yaml, dst / "domain.yaml")

        # 3. system configs
        sys_dir = self.config_dir / "system"
        sys_dir.mkdir(parents=True, exist_ok=True)

        env_root = parse_simple_env(self.repo_root / ".env")
        env_kbui = parse_simple_env(self.repo_root / "kb-ui" / ".env.development")

        self._write_yaml(
            sys_dir / "llm_service.yaml",
            {
                "service_url": "http://localhost:8900",
                "provider": {
                    "base_url": "https://api.deepseek.com/chat/completions",
                    "model": "deepseek-chat",
                },
                "embedding": {
                    "base_url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
                    "model": env_root.get("EMBEDDING_MODEL", "embedding-3"),
                    "dimensions": 1024,
                },
                "rerank": {
                    "base_url": "https://open.bigmodel.cn/api/paas/v4/rerank",
                    "model": env_root.get("RERANK_MODEL", "rerank-pro"),
                },
            },
        )

        self._write_yaml(
            sys_dir / "mining.yaml",
            {
                "service_url": "http://localhost:8901",
                "llm_service_url": "http://localhost:8900",
                "max_workers": int(env_root.get("MAX_WORKERS", "4")),
                "embedding_model": env_root.get("EMBEDDING_MODEL", "embedding-3"),
                "upload": {
                    "root": env_root.get("UPLOAD_ROOT", "./uploads"),
                    "max_file_size": int(env_root.get("UPLOAD_MAX_FILE_SIZE", "104857600")),
                    "max_archive_size": int(env_root.get("UPLOAD_MAX_ARCHIVE_SIZE", "524288000")),
                },
            },
        )

        self._write_yaml(
            sys_dir / "serving.yaml",
            {
                "service_url": "http://localhost:8081",
                "port": 8081,
                "llm_base_url": "http://localhost:8900",
                "embedding_model": env_root.get("EMBEDDING_MODEL", "embedding-3"),
                "embedding_dimensions": 1024,
                "rerank_model": env_root.get("RERANK_MODEL", "rerank-pro"),
            },
        )

        self._write_yaml(
            sys_dir / "database.yaml",
            {
                "driver": "postgresql",
                "host_env": "PG_HOST",
                "port_env": "PG_PORT",
                "dbname_env": "PG_DBNAME",
                "user_env": "PG_USER",
                "sslmode_env": "PG_SSLMODE",
                "pool_min_env": "PG_POOL_MIN",
                "pool_max_env": "PG_POOL_MAX",
            },
        )

        self._write_yaml(
            sys_dir / "ui.yaml",
            {
                "mining_api_base": env_kbui.get("VITE_MINING_API_BASE", "http://localhost:8901"),
                "serving_api_base": env_kbui.get("VITE_SERVING_API_BASE", "http://localhost:8081"),
                "llm_api_base": env_kbui.get("VITE_LLM_API_BASE", "http://localhost:8900"),
                "control_plane_api_base": env_kbui.get("VITE_CONTROL_PLANE_API_BASE", "http://localhost:8910"),
            },
        )

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def load_domain_registry(self) -> dict[str, Any]:
        path = self.config_dir / "domain_registry.yaml"
        if not path.exists():
            return {}
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload.get("domains", {})

    def load_scenario_pack(self, domain_id: str) -> dict[str, Any]:
        registry = self.load_domain_registry()
        pack_ref = registry.get(domain_id, {}).get("scenario_pack", domain_id)
        path = self.config_dir / "scenario_packs" / pack_ref / "domain.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def load_scenario_section(self, domain_id: str, section: str) -> dict[str, Any]:
        pack = self.load_scenario_pack(domain_id)
        return pack.get(section, {})

    def load_system_config(self, service_name: str) -> dict[str, Any]:
        path = self.config_dir / "system" / f"{service_name}.yaml"
        if not path.exists():
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def list_domains(self) -> list[dict[str, Any]]:
        registry = self.load_domain_registry()
        results: list[dict[str, Any]] = []
        for domain_id, entry in registry.items():
            pack = self.load_scenario_pack(domain_id)
            results.append(
                {
                    "domain_id": domain_id,
                    "display_name": pack.get("display_name", domain_id.replace("_", " ").title()),
                    "enabled": bool(entry.get("enabled", True)),
                    "default_channel": entry.get("default_channel", "prod"),
                    "scenario_pack_ref": entry.get("scenario_pack", domain_id),
                    "description": "",
                    "owner_team": "platform",
                    "metadata_json": {
                        "source": "yaml_readonly",
                        "database_url_env": entry.get("database_url_env"),
                    },
                }
            )
        return results

    def get_domain(self, domain_id: str) -> dict[str, Any] | None:
        registry = self.load_domain_registry()
        entry = registry.get(domain_id)
        if not entry:
            return None
        pack = self.load_scenario_pack(domain_id)
        return {
            "domain_id": domain_id,
            "display_name": pack.get("display_name", domain_id.replace("_", " ").title()),
            "enabled": bool(entry.get("enabled", True)),
            "default_channel": entry.get("default_channel", "prod"),
            "scenario_pack_ref": entry.get("scenario_pack", domain_id),
            "description": "",
            "owner_team": "platform",
            "metadata_json": {
                "source": "yaml_readonly",
                "database_url_env": entry.get("database_url_env"),
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _write_yaml(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False), encoding="utf-8")
