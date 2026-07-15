from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "kb-ui/src/components/layout/Sidebar.vue").read_text(encoding="utf-8")
ROUTER = (ROOT / "kb-ui/src/router/index.ts").read_text(encoding="utf-8")


def test_advanced_graph_pages_are_absent_from_sidebar():
    assert "{ path: '/entities'" not in SIDEBAR
    assert "{ path: '/ontology'" not in SIDEBAR
    assert "{ path: '/ontology/graph'" not in SIDEBAR


def test_advanced_graph_urls_redirect_home():
    for path in ("entities", "ontology", "ontology/graph"):
        route = f"path: '{path}'"
        start = ROUTER.index(route)
        block = ROUTER[start : start + 160]
        assert "redirect: '/'" in block
        assert "component:" not in block


def test_review_routes_remain_available():
    assert "path: 'candidates/review'" in ROUTER
    assert "path: 'mentions/review'" in ROUTER
