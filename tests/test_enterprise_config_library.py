from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ENTERPRISE_CONFIG = ROOT / "config_library" / "enterprise"
SITE_CONFIG = ROOT / "config_library" / "site"


def test_enterprise_registry_only_enables_tender_rfp() -> None:
    registry = yaml.safe_load(
        (ENTERPRISE_CONFIG / "domain_registry.yaml").read_text(encoding="utf-8")
    )

    assert set(registry["domains"]) == {"tender_rfp"}
    assert registry["domains"]["tender_rfp"]["scenario_pack"] == "tender_rfp"


def test_enterprise_domain_pack_has_required_tender_knowledge() -> None:
    pack = yaml.safe_load(
        (
            ENTERPRISE_CONFIG
            / "scenario_packs"
            / "tender_rfp"
            / "domain.yaml"
        ).read_text(encoding="utf-8")
    )

    assert {
        "requirement",
        "deliverable",
        "responsibility",
        "evaluation_criterion",
        "contract_clause",
    } <= set(pack["ontology"]["entity_types"])
    assert {
        "mandatory_requirement",
        "optional_requirement",
        "acceptance_criterion",
        "commercial_clause",
    } <= set(pack["mining"]["semantic_roles"])
    assert pack["mining"]["retrieval_policy"]["table_row"] == "structured_tables"
    assert pack["mining"]["llm_templates"]


def test_enterprise_domain_pack_can_be_loaded_by_runtime_loader() -> None:
    from knowledge_mining.mining.infra.domain_pack import load_domain_pack

    profile = load_domain_pack(
        "tender_rfp", packs_root=ENTERPRISE_CONFIG / "scenario_packs"
    )

    assert profile.domain_id == "tender_rfp"
    assert profile.display_name == "政企招投标文档知识库"
    assert "requirement" in profile.strong_entity_types


def test_config_library_is_excluded_from_docker_context() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "config_library/" in patterns


def test_site_registry_only_enables_civil_engineering_and_odn() -> None:
    registry = yaml.safe_load(
        (SITE_CONFIG / "domain_registry.yaml").read_text(encoding="utf-8")
    )

    assert set(registry["domains"]) == {"civil_engineering", "odn"}
    assert registry["domains"]["civil_engineering"]["scenario_pack"] == "civil_engineering"
    assert registry["domains"]["odn"]["scenario_pack"] == "odn"


def test_site_domain_packs_can_be_loaded_by_runtime_loader() -> None:
    from knowledge_mining.mining.infra.domain_pack import load_domain_pack

    packs_root = SITE_CONFIG / "scenario_packs"
    civil = load_domain_pack("civil_engineering", packs_root=packs_root)
    odn = load_domain_pack("odn", packs_root=packs_root)

    assert civil.domain_id == "civil_engineering"
    assert odn.domain_id == "odn"
