from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "kb-ui/src/api/mining.ts").read_text(encoding="utf-8")
STORE_SOURCE = (ROOT / "kb-ui/src/stores/mining.ts").read_text(encoding="utf-8")
PROXY_SOURCE = (ROOT / "kb-ui/src/api/proxyClient.ts").read_text(encoding="utf-8")


def test_run_list_api_requires_domain_query_parameter():
    assert "async getRuns(domain: string" in API_SOURCE
    assert "params: { ...params, domain }" in API_SOURCE


def test_mining_store_lists_runs_in_current_domain():
    assert "miningApi.getRuns(domainStore.currentDomain)" in STORE_SOURCE


def test_all_mining_requests_include_the_current_domain():
    assert "if (service === 'mining')" in PROXY_SOURCE
    assert "domain: config.params?.domain ?? domain.currentDomain" in PROXY_SOURCE
