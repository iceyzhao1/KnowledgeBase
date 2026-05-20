"""Full Mining Workbench integration test — covers all API endpoints.

Usage:
    pytest tests/integration/test_full_integration.py -v --tb=short

Prerequisites:
    - Mining API running on port 8901
    - Database populated with demo_run.py data
"""
from __future__ import annotations

import os
import time

import pytest
import requests

BASE = os.environ.get("MINING_API_URL", "http://localhost:8901")
RUN_ID: str | None = None
DOC_ID: str | None = None
DOCUMENT_ID: str | None = None  # asset_documents.id (UUID)


# ── Fixtures ──


@pytest.fixture(scope="session", autouse=True)
def discover_ids():
    """Discover run_id, doc_id, document_id from existing data."""
    global RUN_ID, DOC_ID, DOCUMENT_ID

    r = requests.get(f"{BASE}/api/runs", timeout=10)
    assert r.status_code == 200, f"list_runs failed: {r.status_code} {r.text}"
    data = r.json()
    items = data.get("items") or data.get("documents") or []
    assert len(items) > 0, "No runs found — run demo_run.py first"
    RUN_ID = items[0]["id"]

    # Get first document
    r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents", timeout=10)
    assert r.status_code == 200
    docs = r.json().get("documents", [])
    assert len(docs) > 0, "No documents in run"
    DOC_ID = docs[0]["id"]

    # Get asset document ID from the run document
    DOCUMENT_ID = docs[0].get("document_id")

    yield


# ── 1. Health ──


class TestHealth:
    def test_health(self):
        r = requests.get(f"{BASE}/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["postgresql"] == "connected"


# ── 2. Knowledge Stats ──


class TestKnowledgeStats:
    def test_stats(self):
        r = requests.get(f"{BASE}/api/knowledge/stats", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "documents" in data
        assert "segments" in data
        assert "retrieval_units" in data
        assert "relations" in data
        assert "retrieval_units_by_type" in data
        assert data["segments"] == 28
        assert data["retrieval_units"] == 55  # 28 raw_text + 27 generated_question
        assert data["relations"] == 16  # discourse relations from LLM
        assert data["retrieval_units_by_type"].get("raw_text", 0) == 28
        assert data["retrieval_units_by_type"].get("generated_question", 0) == 27


# ── 3. Runs ──


class TestRuns:
    def test_list_runs(self):
        r = requests.get(f"{BASE}/api/runs", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert data["total"] >= 1

    def test_get_run(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == RUN_ID
        assert data["status"] == "completed"
        assert data["total_documents"] == 1
        assert data["committed_count"] == 1

    def test_get_run_stages(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/stages", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "stages" in data
        stages = data["stages"]
        assert len(stages) > 0
        stage_names = {s["stage"] for s in stages}
        # Verify expected backend stages exist
        expected = {"parse", "segment", "enrich", "build_relations", "discourse",
                    "retrieval_units", "build_retrieval_units", "select_snapshot",
                    "assemble_build", "validate_build", "publish_release"}
        assert expected.issubset(stage_names), f"Missing stages: {expected - stage_names}"

    def test_get_run_documents(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "documents" in data
        assert len(data["documents"]) >= 1
        doc = data["documents"][0]
        assert "document_name" in doc
        assert "status" in doc
        assert "action" in doc
        assert "current_stage" in doc
        assert "duration_ms" in doc

    def test_get_run_progress(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/progress", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["progress_percent"] == 100.0
        assert data["total"] == 1
        assert data["completed"] == 1
        assert data["failed"] == 0

    def test_get_run_artifacts(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/artifacts", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["segment_count"] == 28
        assert data["unit_count"] == 55
        assert data["relation_count"] == 16


# ── 4. Run Document Details ──


class TestRunDocumentDetails:
    def test_get_run_document(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents/{DOC_ID}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == DOC_ID
        assert data["status"] == "committed"
        assert "document_name" in data

    def test_get_run_document_stages(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents/{DOC_ID}/stages", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "stages" in data
        assert len(data["stages"]) > 0

    def test_get_run_document_artifacts(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents/{DOC_ID}/artifacts", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["segment_count"] == 28
        assert data["unit_count"] == 55
        assert data["relation_count"] == 16

    def test_get_run_document_segments(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents/{DOC_ID}/segments", timeout=10)
        assert r.status_code == 200
        data = r.json()
        items = data.get("items", [])
        assert len(items) == 28
        seg = items[0]
        assert "raw_text" in seg
        assert "block_type" in seg
        assert "segment_index" in seg

    def test_get_run_document_units(self):
        r = requests.get(f"{BASE}/api/runs/{RUN_ID}/documents/{DOC_ID}/units", timeout=10)
        assert r.status_code == 200
        data = r.json()
        items = data.get("items", [])
        assert len(items) == 55  # 28 raw_text + 27 generated_question
        unit = items[0]
        assert unit["unit_type"] == "raw_text"
        assert "title" in unit
        assert "text" in unit


# ── 5. Knowledge Endpoints ──


class TestKnowledgeEndpoints:
    @pytest.fixture(autouse=True)
    def skip_if_no_document_id(self):
        if not DOCUMENT_ID:
            pytest.skip("No document_id available from run document")

    def test_list_knowledge_documents(self):
        r = requests.get(f"{BASE}/api/knowledge/documents", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert data["total"] >= 1

    def test_get_knowledge_document(self):
        r = requests.get(f"{BASE}/api/knowledge/documents/{DOCUMENT_ID}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == DOCUMENT_ID
        assert "document_name" in data
        assert "snapshots" in data

    def test_get_knowledge_document_segments(self):
        r = requests.get(f"{BASE}/api/knowledge/documents/{DOCUMENT_ID}/segments", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert len(data["items"]) == 28

    def test_get_knowledge_document_units(self):
        r = requests.get(f"{BASE}/api/knowledge/documents/{DOCUMENT_ID}/units", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert len(data["items"]) == 55  # 28 raw_text + 27 generated_question

    def test_list_knowledge_segments(self):
        r = requests.get(f"{BASE}/api/knowledge/segments", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 28

    def test_list_knowledge_units(self):
        r = requests.get(f"{BASE}/api/knowledge/units", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 28
        # Verify unit_type filter
        r2 = requests.get(f"{BASE}/api/knowledge/units?unit_type=raw_text", timeout=10)
        assert r2.status_code == 200
        assert r2.json()["total"] >= 28

    def test_list_knowledge_relations(self):
        r = requests.get(f"{BASE}/api/knowledge/relations", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] == 16  # discourse relations from LLM


# ── 6. Uploads ──


class TestUploads:
    def test_list_uploads(self):
        r = requests.get(f"{BASE}/api/uploads", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data


# ── 7. Cancel (should fail on completed run) ──


class TestActions:
    def test_cancel_completed_run(self):
        r = requests.post(f"{BASE}/api/runs/{RUN_ID}/cancel", timeout=10)
        assert r.status_code == 400


# ── 8. External Services (reachability only) ──


class TestExternalServices:
    def test_llm_health(self):
        try:
            r = requests.get("http://localhost:8900/health", timeout=3)
            assert r.status_code == 200
        except requests.ConnectionError:
            pytest.skip("LLM service not running on port 8900")

    def test_serving_health(self):
        try:
            r = requests.get("http://localhost:8081/actuator/health", timeout=3)
            assert r.status_code == 200
        except requests.ConnectionError:
            pytest.skip("Serving service not running on port 8081")
