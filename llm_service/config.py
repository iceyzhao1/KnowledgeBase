"""LLM Service configuration — single source: control plane YAML.

ALL config comes from main_control_service/config/system/llm_service.yaml.
No defaults, no env fallbacks. Missing required fields = hard error.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

CONTROL_PLANE_BASE_URL = os.getenv("CONTROL_PLANE_BASE_URL", "http://localhost:8910")

# ── All required field paths — missing = fatal ──
_REQUIRED_PATHS: list[tuple[str, ...]] = [
    ("host",),
    ("port",),
    ("provider", "base_url"),
    ("provider", "api_key"),
    ("provider", "model"),
    ("provider", "timeout"),
    ("provider", "bypass_proxy"),
    ("provider", "headers"),
    ("embedding", "base_url"),
    ("embedding", "api_key"),
    ("embedding", "model"),
    ("embedding", "dimensions"),
    ("rerank", "base_url"),
    ("rerank", "api_key"),
    ("rerank", "model"),
    ("model", "timeout"),
    ("model", "bypass_proxy"),
    ("model", "extra_headers"),
    ("worker", "concurrency"),
    ("worker", "poll_interval"),
    ("task", "default_max_attempts"),
    ("task", "retry_backoff_base"),
    ("task", "retry_backoff_max"),
    ("task", "execute_timeout"),
    ("task", "lease_duration"),
    ("task", "lease_recovery_interval"),
    ("template", "cache_ttl"),
]


def dig(data: dict, *keys: str) -> Any:
    """Walk nested dict by *keys*; raise ValueError on miss."""
    node: Any = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            raise ValueError(f"Missing config field: {'.'.join(keys)}")
        node = node[k]
    return node


def _validate_required(data: dict) -> None:
    """Raise ValueError if any required field is missing or empty string."""
    missing: list[str] = []
    for path in _REQUIRED_PATHS:
        try:
            val = data
            for k in path:
                val = val[k]
            if isinstance(val, str) and not val.strip():
                missing.append(".".join(path))
        except (KeyError, TypeError):
            missing.append(".".join(path))
    if missing:
        raise ValueError(
            f"Missing required config fields: {', '.join(missing)}. "
            f"Update llm_service.yaml in the control plane."
        )


def fetch_config_from_control_plane(
    base_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Fetch and validate llm_service.yaml from control plane. Fatal on failure."""
    url = (base_url or CONTROL_PLANE_BASE_URL).rstrip("/")
    endpoint = f"{url}/api/v1/system/llm_service/raw"
    try:
        resp = httpx.get(endpoint, timeout=timeout)
        resp.raise_for_status()
        data = yaml.safe_load(resp.text)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot fetch config from control plane ({endpoint}): {exc}. "
            f"Ensure main_control_service is running and llm_service.yaml exists."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(f"Control plane returned non-dict YAML for llm_service")

    _validate_required(data)
    logger.info("Loaded config from control plane (%s)", endpoint)
    return data


def load_llm_config() -> dict[str, Any]:
    """Load config from control plane. Raises on any failure."""
    return fetch_config_from_control_plane()
