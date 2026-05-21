from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ServiceName = Literal["mining", "serving", "llm", "ui"]
BindingMode = Literal["shared", "exclusive"]
UsageType = Literal["asset_core", "mining_runtime", "llm_runtime", "shared"]


class DomainPatchRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    default_channel: str | None = Field(default=None, min_length=1, max_length=32)
    scenario_pack_ref: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    owner_team: str | None = Field(default=None, max_length=200)
    metadata_json: dict[str, Any] | None = None


class CapabilityItem(BaseModel):
    service_name: ServiceName
    enabled: bool
    rollout_state: str = Field(default="observe_only", max_length=64)
    notes: str | None = Field(default=None, max_length=500)


class CapabilityReplaceRequest(BaseModel):
    capabilities: list[CapabilityItem]


class ServiceBindingItem(BaseModel):
    service_name: ServiceName
    instance_id: str = Field(min_length=1, max_length=200)
    binding_mode: BindingMode = "shared"
    priority: int = Field(default=100, ge=0, le=10_000)
    notes: str | None = Field(default=None, max_length=500)


class ServiceBindingReplaceRequest(BaseModel):
    bindings: list[ServiceBindingItem]


class DatabaseBindingItem(BaseModel):
    usage_type: UsageType
    secret_ref: str = Field(min_length=1, max_length=200)
    driver: str = Field(default="postgresql", max_length=64)
    database_name: str | None = Field(default=None, max_length=200)
    schema_name: str | None = Field(default=None, max_length=200)
    readonly: bool = False
    notes: str | None = Field(default=None, max_length=500)


class DatabaseBindingReplaceRequest(BaseModel):
    bindings: list[DatabaseBindingItem]


class RuntimeOverrideItem(BaseModel):
    service_name: ServiceName
    config_scope: str = Field(min_length=1, max_length=120)
    config_json: dict[str, Any]
    version_tag: str | None = Field(default=None, max_length=120)


class RuntimeOverrideReplaceRequest(BaseModel):
    overrides: list[RuntimeOverrideItem]

