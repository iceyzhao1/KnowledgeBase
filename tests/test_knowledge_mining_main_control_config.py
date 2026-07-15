from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_mining.mining.infra.domain_pack import load_domain_pack, load_domain_registry, resolve_domain


def test_knowledge_mining_resolves_site_domain_from_main_control_registry():
    entry = resolve_domain("civil_engineering")

    assert entry["enabled"] is True
    assert entry["scenario_pack"] == "civil_engineering"


def test_knowledge_mining_loads_site_scenario_packs_from_main_control():
    civil = load_domain_pack("civil_engineering")
    odn = load_domain_pack("odn")

    assert civil.domain_id == "civil_engineering"
    assert odn.domain_id == "odn"


def test_main_control_registry_contains_active_site_domains():
    domains = load_domain_registry()["domains"]

    assert {"civil_engineering", "odn"} <= set(domains)
