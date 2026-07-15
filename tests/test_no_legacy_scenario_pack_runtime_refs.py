from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_mining_has_no_root_level_config_fallback() -> None:
    loader = (ROOT / "knowledge_mining/mining/infra/domain_pack.py").read_text(
        encoding="utf-8"
    )
    route = (ROOT / "knowledge_mining/mining/api/routes/config.py").read_text(
        encoding="utf-8"
    )

    assert "_LEGACY_REGISTRY_PATH" not in loader
    assert "_LEGACY_SCENARIO_PACKS_ROOT" not in loader
    assert 'parents[4] / "scenario_packs"' not in route

