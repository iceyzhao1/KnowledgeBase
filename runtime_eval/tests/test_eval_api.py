"""eval-api full-chain test driving an in-process eval-llm via injected poster.

Covers: project -> document upload/parse -> suite generate -> responses ->
judge -> metrics -> report, all on the deterministic mock provider (no network).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from runtime_eval.eval_api.app import create_app
from runtime_eval.eval_api.config import ApiConfig
from runtime_eval.eval_api.llm_client import LLMClient
from runtime_eval.eval_api.store import Store
from runtime_eval.eval_llm.app import create_app as create_llm_app
from runtime_eval.eval_llm.config import LLMConfig
from runtime_eval.eval_llm.providers.mock import MockProvider

SAMPLE_DOC = """# APN 配置说明

默认 APN 为 cmnet，连接超时阈值为 30 秒。

## 故障处理

若连接失败，先检查 APN 拼写与超时配置。
"""


@pytest.fixture()
def client(tmp_path) -> TestClient:
    llm_config = LLMConfig(provider="mock")
    llm_app = create_llm_app(config=llm_config, provider=MockProvider(llm_config))
    llm_tc = TestClient(llm_app)

    def poster(path: str, payload: dict) -> dict:
        if path == "/health":
            return llm_tc.get(path).json()
        return llm_tc.post(path, json=payload).json()

    api_config = ApiConfig(workspace_dir=tmp_path / "workspace", per_type=1)
    store = Store(api_config)
    llm_client = LLMClient(poster=poster)
    app = create_app(config=api_config, store=store, client=llm_client)
    return TestClient(app)


def test_full_chain(client):
    # 1. project
    r = client.post("/api/v1/projects", json={"name": "演示项目"})
    assert r.status_code == 200
    pid = r.json()["project_id"]
    assert client.get("/api/v1/projects").json()[0]["project_id"] == pid

    # 2. upload + parse a document
    r = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("APN.md", SAMPLE_DOC.encode("utf-8"), "text/markdown")},
    )
    assert r.status_code == 200
    doc = r.json()
    did = doc["document_id"]
    assert doc["char_count"] > 0
    assert "APN 配置说明" in doc["sections"]

    docs = client.get(f"/api/v1/projects/{pid}/documents").json()
    assert len(docs) == 1 and docs[0]["document_id"] == did

    # 3. generate suite
    r = client.post(
        f"/api/v1/projects/{pid}/suites:generate",
        json={"document_ids": [did], "types": ["factoid", "procedural"], "per_type": 1},
    )
    assert r.status_code == 200
    suite = r.json()
    sid = suite["suite_id"]
    assert suite["cases"], "应至少出题"
    assert suite["generation_usage"]["total_tokens"] > 0
    assert {c["question_type"] for c in suite["cases"]} <= {"factoid", "procedural"}

    # fetch suite back
    assert client.get(f"/api/v1/suites/{sid}").json()["suite_id"] == sid

    # 4. upload answers (leave one blank to exercise MISSING)
    answers = []
    for i, c in enumerate(suite["cases"]):
        answers.append(
            {
                "case_id": c["id"],
                "answer": "" if i == 0 else c["expected_answer"] + " 额外补充。",
                "latency_ms": 120.0 + i * 10,
                "total_tokens": 300 + i,
            }
        )
    r = client.post(
        f"/api/v1/suites/{sid}/responses",
        json={"agent_name": "mock-copilot", "answers": answers},
    )
    assert r.status_code == 200
    assert r.json()["count"] == len(answers)

    # 5. judge
    r = client.post(f"/api/v1/suites/{sid}/judge", json={})
    assert r.status_code == 200
    payload = r.json()
    rid = payload["run_id"]
    m = payload["metrics"]
    assert m["total_cases"] == len(suite["cases"])
    assert m["answered_cases"] == len(suite["cases"]) - 1
    assert 0.0 <= m["overall_accuracy"] <= 1.0
    assert m["by_type"], "应有按类型分桶"
    assert m["latency"]["mean_ms"] is not None
    assert m["agent_tokens"] > 0
    assert m["framework_judge_tokens"] > 0

    # 6. run metrics endpoint
    assert client.get(f"/api/v1/runs/{rid}/metrics").json()["run_id"] == rid

    # 7. reports
    html = client.get(f"/api/v1/runs/{rid}/report?format=html")
    assert html.status_code == 200 and "<table" in html.text
    md = client.get(f"/api/v1/runs/{rid}/report?format=md")
    assert md.status_code == 200 and "运行态测试报告" in md.text


def test_judge_without_answers_400(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    did = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("d.md", SAMPLE_DOC.encode("utf-8"), "text/markdown")},
    ).json()["document_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:generate",
        json={"document_ids": [did], "types": ["factoid"], "per_type": 1},
    ).json()["suite_id"]
    # no responses uploaded
    r = client.post(f"/api/v1/suites/{sid}/judge", json={})
    assert r.status_code == 400


def test_binary_document_rejected(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("manual.pdf", b"%PDF-1.4 binary", "application/pdf")},
    )
    assert r.status_code == 415


def test_chm_document_parsed(client, monkeypatch):
    # Avoid depending on a real .chm + hh.exe/7z: stub the converter and assert
    # the .chm upload flows through parse -> sections like any text document.
    from runtime_eval.eval_api import parser

    monkeypatch.setattr(
        parser,
        "chm_to_markdown",
        lambda name, data: "# 标题A\n正文A\n\n## 标题B\n正文B\n",
    )
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("help.chm", b"ITSF-fake-chm-bytes", "application/octet-stream")},
    )
    assert r.status_code == 200
    doc = r.json()
    assert doc["char_count"] > 0
    assert "标题A" in doc["sections"] and "标题B" in doc["sections"]


def test_chm_without_converter_returns_415(client, monkeypatch):
    # When the mining converter is unavailable, .chm degrades to a clean 415.
    from runtime_eval.eval_api import parser

    monkeypatch.setattr(parser, "_archive_to_markdown", lambda: None)
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("help.chm", b"ITSF-fake-chm-bytes", "application/octet-stream")},
    )
    assert r.status_code == 415


SAMPLE_SUITE_YAML = """\
questions:
  - id: q001
    question: "默认 APN 是什么？"
    question_type: factoid
    expected_answer: "默认 APN 为 cmnet。"
    expected_entities: ["APN", "cmnet"]
    expected_evidence_contains: ["默认 APN 为 cmnet"]
    source_section: "APN 配置说明"
    difficulty: easy
    notes: "事实检索"
  - id: q002
    question: "连接有什么限制？"
    question_type: constraint
    expected_answer: "连接超时阈值为 30 秒。"
    expected_evidence_contains: ["连接超时阈值为 30 秒"]
    source_section: "APN 配置说明"
    difficulty: medium
"""


def test_import_suite_yaml_then_judge(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    )
    assert r.status_code == 200
    suite = r.json()
    assert suite["backend"] == "imported"
    assert len(suite["cases"]) == 2
    types = {c["question_type"] for c in suite["cases"]}
    assert "constraint" in types  # the new enum value round-trips
    # key_points merged from expected_evidence_contains + expected_entities
    c0 = next(c for c in suite["cases"] if c["id"] == "q001")
    assert any("cmnet" in kp for kp in c0["key_points"])
    assert c0["source"]["section"] == "APN 配置说明"

    # an imported suite is judgeable like any generated one
    sid = suite["suite_id"]
    answers = [
        {"case_id": c["id"], "answer": c["expected_answer"], "latency_ms": 100.0, "total_tokens": 40}
        for c in suite["cases"]
    ]
    client.post(f"/api/v1/suites/{sid}/responses", json={"agent_name": "a", "answers": answers})
    j = client.post(f"/api/v1/suites/{sid}/judge", json={})
    assert j.status_code == 200
    assert j.json()["metrics"]["total_cases"] == 2


def test_import_suite_unknown_type_400(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    bad = "questions:\n  - question: x\n    question_type: nonsense\n    expected_answer: y\n"
    r = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("bad.yaml", bad.encode("utf-8"), "application/x-yaml")},
    )
    assert r.status_code == 400


def test_retrieval_evaluate_flow(client):
    # Import a suite carrying gold evidence, upload retrieved evidence, evaluate
    # the retrieval layer, and assert the IR metrics come back well-formed.
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    suite = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()
    sid = suite["suite_id"]
    # imported suite exposes structured retrieval gold labels
    c0 = next(c for c in suite["cases"] if c["id"] == "q001")
    assert "默认 APN 为 cmnet" in c0["expected_evidence"]
    assert "cmnet" in c0["expected_entities"]

    # upload retrieved evidence: q001 fully covered at rank 1, q002 covered at rank 2
    items = {
        "q001": [
            {"rank": 1, "text": "默认 APN 为 cmnet，连接超时阈值为 30 秒。", "source": "APN 配置说明"},
            {"rank": 2, "text": "无关内容。"},
        ],
        "q002": [
            {"rank": 1, "text": "完全不相关的一段话。"},
            {"rank": 2, "text": "连接超时阈值为 30 秒。"},
        ],
    }
    r = client.post(
        f"/api/v1/suites/{sid}/retrieval",
        json={"agent_name": "my-retriever", "items": items},
    )
    assert r.status_code == 200 and r.json()["cases"] == 2

    # round-trip the stored retrieval set
    got = client.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert got["agent_name"] == "my-retriever"
    assert len(got["items"]["q001"]) == 2

    # evaluate
    r = client.post(f"/api/v1/suites/{sid}/retrieval:evaluate", json={"k_values": [1, 3]})
    assert r.status_code == 200
    payload = r.json()
    rid = payload["run_id"]
    m = payload["metrics"]
    assert m["total_cases"] == 2 and m["judged_cases"] == 2
    assert m["k_values"] == [1, 3]
    # q001 relevant@1, q002 relevant@2 -> HitRate@1 = 0.5, HitRate@3 = 1.0
    assert m["hit_rate"]["1"] == 0.5
    assert m["hit_rate"]["3"] == 1.0
    # both gold sets fully covered within rank 3 -> Recall@3 == ContextRecall == 1.0
    assert m["recall"]["3"] == 1.0
    assert m["context_recall"] == 1.0
    assert 0.0 <= m["ndcg"]["3"] <= 1.0
    assert m["by_type"], "应有按类型分桶"

    # metrics + reports endpoints
    assert client.get(f"/api/v1/retrieval-runs/{rid}/metrics").json()["run_id"] == rid
    html = client.get(f"/api/v1/retrieval-runs/{rid}/report?format=html")
    assert html.status_code == 200 and "检索层测试报告" in html.text
    md = client.get(f"/api/v1/retrieval-runs/{rid}/report?format=md")
    assert md.status_code == 200 and "Context Recall" in md.text


def test_retrieval_evaluate_without_upload_400(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]
    r = client.post(f"/api/v1/suites/{sid}/retrieval:evaluate", json={})
    assert r.status_code == 400


def test_retrieval_import_file(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]
    retrieval_json = (
        '{"agent_name": "file-retriever", "items": '
        '{"q001": ["默认 APN 为 cmnet。", "无关"], '
        '"q002": [{"rank": 1, "text": "连接超时阈值为 30 秒。"}]}}'
    )
    r = client.post(
        f"/api/v1/suites/{sid}/retrieval:import",
        files={"file": ("ret.json", retrieval_json.encode("utf-8"), "application/json")},
    )
    assert r.status_code == 200
    assert r.json()["agent_name"] == "file-retriever"
    assert r.json()["cases"] == 2


def _live_parts(tmp_path):
    """Build a store + mock-backed LLMClient + config for direct orchestrator use."""
    llm_config = LLMConfig(provider="mock")
    llm_app = create_llm_app(config=llm_config, provider=MockProvider(llm_config))
    llm_tc = TestClient(llm_app)

    def poster(path: str, payload: dict) -> dict:
        if path == "/health":
            return llm_tc.get(path).json()
        return llm_tc.post(path, json=payload).json()

    config = ApiConfig(workspace_dir=tmp_path / "workspace")
    return Store(config), LLMClient(poster=poster), config


def test_evaluate_live_retrieval_precision_only(tmp_path):
    from runtime_eval.eval_api import orchestrator
    from runtime_eval.eval_api.db_source import LiveCase, LiveItem
    from runtime_eval.eval_api.report import render_retrieval_markdown
    from runtime_eval.eval_api.retrieval_metrics import compute_retrieval_metrics

    store, client, config = _live_parts(tmp_path)
    cases = [
        LiveCase(
            query_id="q-1",
            question="默认 APN 是什么以及超时阈值",
            domain="cloud_core_network",
            intent="general",
            duration_ms=1200,
            items=[
                LiveItem(rank=1, text="默认 APN 为 cmnet，连接超时阈值为 30 秒。", source_path="a/APN.md"),
                LiveItem(rank=2, text="一段与提问完全无关的内容。", source_path="b/x.md"),
            ],
        ),
        LiveCase(
            query_id="q-2",
            question="业务感知的规则如何获取",
            domain="cloud_core_network",
            intent="general",
            duration_ms=900,
            items=[
                LiveItem(rank=1, text="规则规划与获取流程说明业务感知的规则如何获取。", source_path="c/rule.md"),
            ],
        ),
    ]

    run, suite = orchestrator.evaluate_live_retrieval(
        store, client, config, cases, project_id="proj_x", k_values=[1, 3]
    )
    m = compute_retrieval_metrics(run)

    # precision-only: no gold -> has_gold False, recall family is N/A (0.0 numerically)
    assert m.has_gold is False
    assert m.total_cases == 2 and m.judged_cases == 2
    # both cases have a relevant item at rank 1 under the question-overlap mock
    assert m.hit_rate[1] == 1.0
    assert m.mrr[1] == 1.0
    assert 0.0 < m.ndcg[1] <= 1.0
    # the synthetic suite + run round-trip through the store
    assert store.get_suite(suite.suite_id) is not None
    assert store.get_retrieval_run(run.run_id) is not None
    # report marks recall N/A when there is no gold
    md = render_retrieval_markdown(run, suite)
    assert "N/A" in md


def test_evaluate_live_retrieval_empty_400(tmp_path):
    from runtime_eval.eval_api import orchestrator

    store, client, config = _live_parts(tmp_path)
    with pytest.raises(ValueError):
        orchestrator.evaluate_live_retrieval(store, client, config, [])


def test_per_question_retrieval_flow(client):
    # The per-question (逐题) flow: seed retrieved evidence per case (the「检索数据库」
    # pull needs a live DB, so we seed via the manual endpoint instead), then judge
    # each case individually, watch progress, and aggregate the final report.
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    suite = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()
    sid = suite["suite_id"]

    items = {
        "q001": [
            {"rank": 1, "text": "默认 APN 为 cmnet，连接超时阈值为 30 秒。", "source": "APN 配置说明"},
            {"rank": 2, "text": "无关内容。"},
        ],
        "q002": [
            {"rank": 1, "text": "完全不相关的一段话。"},
            {"rank": 2, "text": "连接超时阈值为 30 秒。"},
        ],
    }
    client.post(f"/api/v1/suites/{sid}/retrieval", json={"agent_name": "serving", "items": items})

    # before judging: progress shows pulled but not judged, report blocked
    prog = client.get(f"/api/v1/suites/{sid}/retrieval/progress").json()
    assert prog["total"] == 2 and prog["pulled"] == 2 and prog["judged"] == 0
    assert prog["all_judged"] is False
    assert client.post(f"/api/v1/suites/{sid}/retrieval:report").status_code == 400

    # judge each case individually
    for cid in ("q001", "q002"):
        r = client.post(f"/api/v1/suites/{sid}/cases/{cid}/retrieval:judge")
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["case_id"] == cid
        assert res["retrieved_count"] == 2

    # progress now complete -> report unblocked
    prog = client.get(f"/api/v1/suites/{sid}/retrieval/progress").json()
    assert prog["judged"] == 2 and prog["all_judged"] is True
    # progress carries per-case results + items for UI restore
    q1 = next(c for c in prog["cases"] if c["case_id"] == "q001")
    assert q1["judged"] is True and q1["pulled_count"] == 2 and q1["result"] is not None

    rep = client.post(f"/api/v1/suites/{sid}/retrieval:report")
    assert rep.status_code == 200
    m = rep.json()["metrics"]
    assert m["total_cases"] == 2 and m["judged_cases"] == 2
    rid = rep.json()["run_id"]
    assert rid == f"rrun_{sid}"
    assert client.get(f"/api/v1/retrieval-runs/{rid}/metrics").json()["run_id"] == rid


def test_judge_case_without_pull_400(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]
    # no evidence pulled/seeded for this case
    r = client.post(f"/api/v1/suites/{sid}/cases/q001/retrieval:judge")
    assert r.status_code == 400


def test_pull_case_without_db_503(client):
    # serving DB is unconfigured in the test ApiConfig -> the per-question pull
    # degrades to a clean 503 instead of crashing.
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn_eval.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]
    r = client.post(f"/api/v1/suites/{sid}/cases/q001/retrieval:pull", json={})
    assert r.status_code == 503


# --- Phase 1：L1 批量化（草稿拉取 / 黄金标注 / 批量评估） ---


def _live_cases_for_draft():
    from runtime_eval.eval_api.db_source import LiveCase, LiveItem

    return [
        LiveCase(
            query_id="ql-1",
            question="默认 APN 是什么？",
            domain="cloud_core_network",
            intent="general",
            items=[
                LiveItem(rank=1, text="默认 APN 为 cmnet，连接超时阈值为 30 秒。",
                         source_path="APN 配置说明"),
                LiveItem(rank=2, text="无关内容。"),
            ],
        ),
        LiveCase(
            query_id="ql-2",
            question="连接有什么限制？",
            domain="cloud_core_network",
            intent="general",
            items=[LiveItem(rank=1, text="连接超时阈值为 30 秒。")],
        ),
    ]


def test_pull_draft_suite_and_gold_backfill(client, monkeypatch):
    from runtime_eval.eval_api import app as app_module

    monkeypatch.setattr(app_module, "pull_live_cases", lambda *a, **k: _live_cases_for_draft())
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]

    # 首次拉取：黄金库为空 → 0 命中，2 条待标注
    r = client.post(f"/api/v1/projects/{pid}/retrieval/live:pull", json={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    sid = body["suite_id"]
    assert body["pulled_cases"] == 2
    assert body["gold_hits"] == 0 and body["pending_annotation"] == 2

    # 标注其中一条 → 落入黄金库
    r = client.put(
        f"/api/v1/suites/{sid}/cases/ql-1/gold",
        json={
            "expected_answer": "默认 APN 为 cmnet。",
            "expected_evidence": ["默认 APN 为 cmnet"],
            "expected_entities": ["cmnet"],
            "source_doc": "APN.md",
            "difficulty": "easy",
        },
    )
    assert r.status_code == 200 and r.json()["saved_to_library"] is True

    # 进度：1 已标注 / 1 待标注
    prog = client.get(f"/api/v1/suites/{sid}/gold/progress").json()
    assert prog["annotated"] == 1 and prog["pending"] == 1

    # 黄金库列表含该条
    lib = client.get(f"/api/v1/projects/{pid}/gold").json()
    assert len(lib["records"]) == 1
    assert lib["records"][0]["expected_entities"] == ["cmnet"]

    # 二次拉取相同问题：黄金库命中 → 自动回填
    r2 = client.post(f"/api/v1/projects/{pid}/retrieval/live:pull", json={"limit": 10})
    body2 = r2.json()
    assert body2["gold_hits"] == 1
    c = next(c for c in body2["suite"]["cases"] if c["id"] == "ql-1")
    assert c["expected_evidence"] == ["默认 APN 为 cmnet"]


def test_batch_evaluate_only_annotated(client, monkeypatch):
    from runtime_eval.eval_api import app as app_module

    monkeypatch.setattr(app_module, "pull_live_cases", lambda *a, **k: _live_cases_for_draft())
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/retrieval/live:pull", json={"limit": 10}
    ).json()["suite_id"]

    # 只标注 ql-1
    client.put(
        f"/api/v1/suites/{sid}/cases/ql-1/gold",
        json={"expected_evidence": ["默认 APN 为 cmnet"], "expected_entities": ["cmnet"]},
    )

    # only_annotated=True → 仅评估 1 条
    r = client.post(
        f"/api/v1/suites/{sid}/retrieval:evaluate",
        json={"k_values": [1, 3], "only_annotated": True},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["evaluated_cases"] == 1
    assert payload["metrics"]["total_cases"] == 1


def test_batch_evaluate_only_annotated_none_400(client, monkeypatch):
    from runtime_eval.eval_api import app as app_module

    monkeypatch.setattr(app_module, "pull_live_cases", lambda *a, **k: _live_cases_for_draft())
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/retrieval/live:pull", json={"limit": 10}
    ).json()["suite_id"]
    # 无任何标注 → only_annotated 应 400
    r = client.post(
        f"/api/v1/suites/{sid}/retrieval:evaluate", json={"only_annotated": True}
    )
    assert r.status_code == 400


def test_serves_eval_web_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "通用知识库测试框架" in r.text
    # 重构后的以项目为中心导航：看板 / 项目 / 评估工作台 / 对话
    assert 'data-page="projects"' in r.text
    assert 'data-page="work"' in r.text
    assert "评估工作台" in r.text
    # 旧的并行入口已下线
    assert 'data-page="flow"' not in r.text
    assert "评估流程" not in r.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    # per-question flow wired: pull / judge / progress / final report
    assert "retrieval:pull" in app_js.text
    assert "retrieval:judge" in app_js.text
    assert "retrieval/progress" in app_js.text
    assert "retrieval:report" in app_js.text
    assert "suites:generate" in app_js.text
    # L1 批量化前端接线：草稿拉取 / 黄金标注 / 批量评估 / 标注进度
    assert "retrieval/live:pull" in app_js.text
    assert "/gold" in app_js.text
    assert "gold/progress" in app_js.text
    # 多页前端骨架 + 对话页接线
    styles = client.get("/static/styles.css")
    assert styles.status_code == 200
    assert "/api/v1/chat" in app_js.text
    # Claude 自动跑批前端接线（SSE 实时进度：EventSource 订阅 agent:run/stream）
    assert "agent:run/stream" in app_js.text
    assert "EventSource" in app_js.text
    # Phase 0 评测档案：测试集/评估档案从服务端读（localStorage 退为离线兜底）
    assert "syncSuitesFromServer" in app_js.text
    assert "syncRunsFromServer" in app_js.text
    assert "/runs" in app_js.text
    # Phase 1 黄金集库可编辑页：CRUD + 确认 + 批量确认接线
    assert "createGold" in app_js.text
    assert "editGold" in app_js.text
    assert "deleteGold" in app_js.text
    assert "confirmGold" in app_js.text
    assert "gold:confirm-all" in app_js.text
    assert "goldFilter" in app_js.text
    # 工作台步骤条：复用 /state 解锁逻辑 + 软门禁
    assert "renderWork" in app_js.text       # 工作台外壳
    assert "flowRefresh" in app_js.text
    assert "flowReady" in app_js.text        # 软门禁（原 flowUnlocked 重命名）
    assert "stepper" in app_js.text
    assert "/state" in app_js.text          # 进入工作台调 /suites/{sid}/state 定位步骤
    assert "flowMarkStale" in app_js.text    # D4 上游改动标记下游需重跑
    # 向导样式
    assert ".stepper-item" in styles.text
    # Phase 3 取证与评估透明：③ serving 批量取证走 SSE；④ 出报告前展示评估标准快照
    assert "retrieval:pull/stream" in app_js.text   # ③ SSE 批量取证
    assert "pullAllCasesStream" in app_js.text
    assert "loadCriteriaCard" in app_js.text         # ④ 评估标准快照
    assert "/api/v1/eval-criteria" in app_js.text
    assert ".crit-chip" in styles.text
    # 报告历史：收进工作台第④步（评分与报告），旧独立「评估报告」导航已下线
    assert 'data-page="reports"' not in r.text        # 旧「评估报告」导航已下线
    assert "检索评估与报告" not in r.text             # 旧「检索评估」导航已下线
    assert "reportUrl" in app_js.text                 # 历史条目可打开 HTML/MD 报告
    assert "runScoreLine" in app_js.text              # 报告历史综合分渲染
    assert "/projects/${pid}/runs" in app_js.text     # 报告历史读服务端档案
    # 工作台 4 步：准备题目 / 标准答案 / 取证据 / 评分与报告
    assert "准备题目" in app_js.text
    assert "标准答案" in app_js.text
    assert "取证据" in app_js.text
    assert "评分与报告" in app_js.text
    # 步骤③④ 拆成独立步骤函数；步骤① 三选一来源切换器
    assert "workStepEvidence" in app_js.text
    assert "workStepReport" in app_js.text
    assert "setDocSource" in app_js.text
    # 旧子标签外壳 + 步骤② 重复的批量评估按钮已下线
    assert "workEvalTab" not in app_js.text
    assert "renderEvalTab" not in app_js.text
    assert "kb_evaltab" not in app_js.text
    assert "batchEvaluateAnnotated" not in app_js.text


# --- 对话页：知识库试问（检索预览） ---


def test_chat_preview_matched(client, monkeypatch):
    from runtime_eval.eval_api import app as app_module
    from runtime_eval.eval_api.db_source import LiveCase, LiveItem

    live = LiveCase(
        query_id="ql-9",
        question="默认 APN 是什么？",
        domain="cloud_core_network",
        intent="general",
        duration_ms=42,
        queried_at="2026-06-01T10:00:00",
        items=[LiveItem(rank=1, text="默认 APN 为 cmnet。", source_path="APN.md")],
    )
    monkeypatch.setattr(app_module, "pull_latest_for_question", lambda *a, **k: live)

    r = client.post("/api/v1/chat", json={"question": "默认 APN 是什么？"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["matched_question"] == "默认 APN 是什么？"
    assert body["items"][0]["rank"] == 1
    assert body["items"][0]["source"] == "APN.md"


def test_chat_preview_no_match_returns_candidates(client, monkeypatch):
    from runtime_eval.eval_api import app as app_module

    monkeypatch.setattr(app_module, "pull_latest_for_question", lambda *a, **k: None)
    monkeypatch.setattr(
        app_module,
        "search_similar_questions",
        lambda *a, **k: [{"query_id": "c1", "query_text": "类似问题", "similarity": 0.8, "domain": "d", "queried_at": ""}],
    )

    r = client.post("/api/v1/chat", json={"question": "完全没出现过的问题"})
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is False
    assert body["candidates"][0]["query_id"] == "c1"


def test_chat_preview_empty_question_400(client):
    r = client.post("/api/v1/chat", json={"question": "  "})
    assert r.status_code == 400


# --- Claude 自动跑批（agent:run）：claude -p + MCP 自动检索并作答 ---


def _agent_client(tmp_path, agent_out: dict):
    """构造一个会拦截 /run-agent 返回 canned agent 结果的 eval-api 客户端。

    生成测试集仍走 mock eval-llm；只有 /run-agent 被替换成给定的固定输出，
    从而端到端验证 orchestrator.run_agent_suite + agent:run 端点的落库逻辑。
    """
    llm_config = LLMConfig(provider="mock")
    llm_app = create_llm_app(config=llm_config, provider=MockProvider(llm_config))
    llm_tc = TestClient(llm_app)

    def poster(path: str, payload: dict) -> dict:
        if path == "/run-agent":
            return dict(agent_out)
        if path == "/health":
            return llm_tc.get(path).json()
        return llm_tc.post(path, json=payload).json()

    api_config = ApiConfig(workspace_dir=tmp_path / "workspace", per_type=1)
    store = Store(api_config)
    app = create_app(config=api_config, store=store, client=LLMClient(poster=poster))
    return TestClient(app)


AGENT_OUT = {
    "answer": "默认 APN 为 cmnet，连接超时阈值为 30 秒。",
    "retrieved_items": [
        {"rank": 1, "text": "默认 APN 为 cmnet，连接超时阈值为 30 秒。", "source": "APN.md"},
        {"rank": 2, "text": "若连接失败，先检查 APN 拼写与超时配置。", "source": "APN.md"},
    ],
    "tool_calls": [{"name": "kb_search", "input": {"query": "APN"}, "query": "APN", "items": []}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    "num_turns": 2,
    "is_error": False,
    "error": "",
}


def test_agent_run_suite_populates_answers_and_retrieval(tmp_path):
    tc = _agent_client(tmp_path, AGENT_OUT)
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = tc.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]

    r = tc.post(f"/api/v1/suites/{sid}/agent:run", json={"agent_name": "claude-agent"})
    assert r.status_code == 200
    s = r.json()
    assert s["total"] == 2
    assert s["answered"] == 2
    assert s["retrieved_cases"] == 2
    assert s["agent_tokens"] == 240  # 120 * 2 题
    assert s["errors"] == []

    # 回答落到了 ResponseSet（应用层可直接评测）
    resp = tc.get(f"/api/v1/suites/{sid}/responses").json()
    assert resp["agent_name"] == "claude-agent"
    assert len(resp["responses"]) == 2
    assert resp["responses"][0]["answer"]
    assert resp["responses"][0]["raw"]["num_turns"] == 2

    # 检索片段落到了 RetrievalSet（检索层可直接评测）
    ret = tc.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert len(ret["items"]["q001"]) == 2
    assert ret["items"]["q001"][0]["source"] == "APN.md"

    # 跑批后应用层「全部评估」可直接出指标
    j = tc.post(f"/api/v1/suites/{sid}/judge", json={})
    assert j.status_code == 200
    assert j.json()["metrics"]["answered_cases"] == 2


def test_agent_run_empty_suite_400(tmp_path):
    tc = _agent_client(tmp_path, AGENT_OUT)
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    # 用 generate 出题需要文档；这里造一个空 suite：import 一个无题文件会 400，
    # 改为直接对不存在的 suite 调用以验证 404。
    r = tc.post("/api/v1/suites/suite_nope/agent:run", json={})
    assert r.status_code == 404


def test_agent_run_case_merges_without_clobbering(tmp_path):
    """逐题 agent 端点：只覆盖本题，保留其它题已有的检索/回答（前端逐题驱动）。"""
    tc = _agent_client(tmp_path, AGENT_OUT)
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = tc.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]

    # 先给 q002 手动种一条检索（模拟「检索数据库」已确认过的题）
    tc.post(
        f"/api/v1/suites/{sid}/retrieval",
        json={"agent_name": "serving", "items": {"q002": [{"rank": 1, "text": "连接超时阈值为 30 秒。"}]}},
    )

    # 只对 q001 跑 agent
    r = tc.post(f"/api/v1/suites/{sid}/cases/q001/agent:run", json={"agent_name": "claude-agent"})
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == "q001"
    assert body["answered"] is True
    assert body["retrieved_count"] == 2
    assert body["items"][0]["source"] == "APN.md"
    assert body["num_turns"] == 2 and body["tokens"] == 120

    # q001 检索写入，且 q002 之前手动种的检索没有被清掉（合并而非覆盖整集）
    ret = tc.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert len(ret["items"]["q001"]) == 2
    assert ret["items"]["q002"][0]["text"] == "连接超时阈值为 30 秒。"

    # q001 回答写入 ResponseSet，仅此一条
    resp = tc.get(f"/api/v1/suites/{sid}/responses").json()
    assert [x["case_id"] for x in resp["responses"]] == ["q001"]
    assert resp["responses"][0]["answer"]

    # 再对 q002 跑 → 两题都齐
    tc.post(f"/api/v1/suites/{sid}/cases/q002/agent:run", json={"agent_name": "claude-agent"})
    resp = tc.get(f"/api/v1/suites/{sid}/responses").json()
    assert {x["case_id"] for x in resp["responses"]} == {"q001", "q002"}


def test_agent_run_case_404(tmp_path):
    tc = _agent_client(tmp_path, AGENT_OUT)
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = tc.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]
    assert tc.post(f"/api/v1/suites/{sid}/cases/nope/agent:run", json={}).status_code == 404
    assert tc.post("/api/v1/suites/suite_nope/cases/q001/agent:run", json={}).status_code == 404


def _parse_sse(text: str) -> list[dict]:
    """Parse an SSE body (``data: <json>\\n\\n`` blocks) into a list of events."""
    events: list[dict] = []
    for block in text.split("\n\n"):
        line = block.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def _import_apn_suite(tc) -> str:
    pid = tc.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    return tc.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("apn.yaml", SAMPLE_SUITE_YAML.encode("utf-8"), "application/x-yaml")},
    ).json()["suite_id"]


def test_agent_run_stream_emits_progress_and_persists(tmp_path):
    """SSE 跑批：按序推 start→case_start/case_done→done，并把回答/检索落库。"""
    tc = _agent_client(tmp_path, AGENT_OUT)
    sid = _import_apn_suite(tc)

    r = tc.get(f"/api/v1/suites/{sid}/agent:run/stream?agent_name=claude-agent")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = _parse_sse(r.text)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"

    start = events[0]
    assert start["total"] == 2 and start["todo"] == 2 and start["skipped"] == 0
    case_done = [e for e in events if e["event"] == "case_done"]
    assert len(case_done) == 2
    assert case_done[0]["retrieved_count"] == 2
    done = events[-1]
    assert done["ok"] == 2 and done["ret"] == 2 and done["err"] == 0

    # 回答 + 检索都已落库（与逐题端点同一条 merge 路径）
    assert len(tc.get(f"/api/v1/suites/{sid}/responses").json()["responses"]) == 2
    assert len(tc.get(f"/api/v1/suites/{sid}/retrieval").json()["items"]) == 2


def test_agent_run_stream_only_missing_skips_done(tmp_path):
    """only_missing=true：后端判定已检索/已回答的题并跳过（真正生效）。"""
    tc = _agent_client(tmp_path, AGENT_OUT)
    sid = _import_apn_suite(tc)

    # 先给 q002 种一条检索 → 应被 only_missing 跳过
    tc.post(
        f"/api/v1/suites/{sid}/retrieval",
        json={"agent_name": "serving", "items": {"q002": [{"rank": 1, "text": "超时 30 秒。"}]}},
    )

    r = tc.get(f"/api/v1/suites/{sid}/agent:run/stream?only_missing=true")
    assert r.status_code == 200
    events = _parse_sse(r.text)

    start = events[0]
    assert start["todo"] == 1 and start["skipped"] == 1
    case_starts = [e["case_id"] for e in events if e["event"] == "case_start"]
    assert case_starts == ["q001"]
    # q002 手动种的检索没被动过
    ret = tc.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert ret["items"]["q002"][0]["text"] == "超时 30 秒。"


def test_agent_run_stream_404_when_suite_missing(tmp_path):
    tc = _agent_client(tmp_path, AGENT_OUT)
    assert tc.get("/api/v1/suites/suite_nope/agent:run/stream").status_code == 404


# --- Phase 3 · serving 批量取证（SSE） + 评估标准快照 ---------------------


def _live_for(question: str):
    from runtime_eval.eval_api.db_source import LiveCase, LiveItem

    return LiveCase(
        query_id="q-live",
        question=question,
        domain="cloud_core_network",
        intent="general",
        duration_ms=12,
        queried_at="2026-06-15T09:00:00",
        items=[
            LiveItem(rank=1, text="片段一。", source_path="A.md"),
            LiveItem(rank=2, text="片段二。", source_path="B.md"),
        ],
    )


def test_retrieval_pull_stream_emits_and_persists(client, monkeypatch):
    """serving 批量取证 SSE：按序推 start→case_done→done，命中片段落库。"""
    from runtime_eval.eval_api import app as app_module

    sid = _import_apn_suite(client)
    monkeypatch.setattr(
        app_module, "pull_latest_for_question", lambda cfg, q, **k: _live_for(q)
    )

    r = client.get(f"/api/v1/suites/{sid}/retrieval:pull/stream")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = _parse_sse(r.text)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert events[0]["total"] == 2 and events[0]["todo"] == 2

    case_done = [e for e in events if e["event"] == "case_done"]
    assert len(case_done) == 2
    assert all(e["matched"] for e in case_done)
    assert case_done[0]["retrieved_count"] == 2
    assert events[-1]["ok"] == 2 and events[-1]["miss"] == 0 and events[-1]["err"] == 0

    # 片段已落库到检索集
    ret = client.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert len(ret["items"]) == 2


def test_retrieval_pull_stream_miss_when_no_match(client, monkeypatch):
    """知识库里查不到 → 该题 matched=false，不落库，done.miss 累加。"""
    from runtime_eval.eval_api import app as app_module

    sid = _import_apn_suite(client)
    monkeypatch.setattr(app_module, "pull_latest_for_question", lambda *a, **k: None)

    events = _parse_sse(client.get(f"/api/v1/suites/{sid}/retrieval:pull/stream").text)
    case_done = [e for e in events if e["event"] == "case_done"]
    assert len(case_done) == 2 and not any(e["matched"] for e in case_done)
    assert events[-1]["ok"] == 0 and events[-1]["miss"] == 2

    ret = client.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert ret["items"] == {}


def test_retrieval_pull_stream_only_missing_skips(client, monkeypatch):
    """only_missing=true：已有检索片段的题被跳过，已种片段不被动。"""
    from runtime_eval.eval_api import app as app_module

    sid = _import_apn_suite(client)
    client.post(
        f"/api/v1/suites/{sid}/retrieval",
        json={"agent_name": "serving", "items": {"q002": [{"rank": 1, "text": "旧片段。"}]}},
    )
    monkeypatch.setattr(
        app_module, "pull_latest_for_question", lambda cfg, q, **k: _live_for(q)
    )

    events = _parse_sse(
        client.get(f"/api/v1/suites/{sid}/retrieval:pull/stream?only_missing=true").text
    )
    assert events[0]["todo"] == 1 and events[0]["skipped"] == 1
    starts = [e["case_id"] for e in events if e["event"] == "case_start"]
    assert starts == ["q001"]

    ret = client.get(f"/api/v1/suites/{sid}/retrieval").json()
    assert ret["items"]["q002"][0]["text"] == "旧片段。"


def test_retrieval_pull_stream_404_when_suite_missing(client):
    assert client.get("/api/v1/suites/suite_nope/retrieval:pull/stream").status_code == 404


def test_eval_criteria_snapshot(client):
    """评估标准快照来自 config：K 值 / 通过线 / 权重 / 难度系数。"""
    c = client.get("/api/v1/eval-criteria").json()
    assert c["k_values"] == [1, 3, 5, 10]
    assert c["score_k"] == 10
    assert c["weights"]["find"] == 0.40
    assert c["weights"]["rank"] == 0.25
    assert c["weights"]["quality"] == 0.35
    assert c["pass"]["recall"] == 0.5
    assert c["pass"]["rank"] == 3
    assert c["pass"]["context_recall"] == 0.8
    assert c["difficulty_weights"]["hard"] == 2.0


# --- 出题提示词预览 / 出题报错透传 ----------------------------------------


def test_generate_preview_returns_prompt(client):
    """预览端点应逐篇返回将发给大模型的 system/user 提示词，不真正出题。"""
    pid = client.post("/api/v1/projects", json={"name": "预览项目"}).json()["project_id"]
    did = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("APN.md", SAMPLE_DOC.encode("utf-8"), "text/markdown")},
    ).json()["document_id"]

    r = client.post(
        f"/api/v1/projects/{pid}/suites:generate/preview",
        json={"document_ids": [did], "types": ["factoid"], "per_type": 2},
    )
    assert r.status_code == 200
    prompts = r.json()["prompts"]
    assert len(prompts) == 1
    p = prompts[0]
    assert p["doc_name"] == "APN.md"
    assert p["system"] and p["user"]
    assert p["types"] == ["factoid"]
    assert p["per_type"] == 2
    # 文档正文应当出现在 user 提示词里，方便排查“到底喂了什么给模型”。
    assert "cmnet" in p["user"]


def test_generate_preview_404_when_project_missing(client):
    r = client.post(
        "/api/v1/projects/proj_nope/suites:generate/preview",
        json={"document_ids": ["doc_x"], "types": ["factoid"], "per_type": 1},
    )
    assert r.status_code == 404


def test_generate_cases_surfaces_llm_error():
    """底层模型报错时，/generate-cases 应回 502 且带可读详情，而非泛化 500。"""

    class _BoomProvider:
        name = "boom"

        def __init__(self, config=None):
            self.config = config

        def chat(self, **_kwargs):
            raise RuntimeError("claude -p 调用超时：900s 未返回")

    llm_config = LLMConfig(provider="mock")
    llm_app = create_llm_app(config=llm_config, provider=_BoomProvider(llm_config))
    tc = TestClient(llm_app, raise_server_exceptions=False)

    r = tc.post(
        "/generate-cases",
        json={"document_text": SAMPLE_DOC, "doc_ref": "APN.md", "per_type": 1},
    )
    assert r.status_code == 502
    assert "claude -p 调用超时" in r.json()["detail"]


# --- Phase 0 · 服务端档案化：列表 / 恢复 / run_summaries ------------------


def _project_with_suite(client) -> tuple[str, str]:
    """建项目 + 传文档 + 出题，返回 (pid, sid)。"""
    pid = client.post("/api/v1/projects", json={"name": "档案项目"}).json()["project_id"]
    did = client.post(
        f"/api/v1/projects/{pid}/documents",
        files={"file": ("APN.md", SAMPLE_DOC.encode("utf-8"), "text/markdown")},
    ).json()["document_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:generate",
        json={"document_ids": [did], "types": ["factoid"], "per_type": 1},
    ).json()["suite_id"]
    return pid, sid


def test_list_project_suites(client):
    pid, sid = _project_with_suite(client)
    suites = client.get(f"/api/v1/projects/{pid}/suites").json()
    assert len(suites) == 1
    assert suites[0]["suite_id"] == sid
    assert suites[0]["case_count"] >= 1
    # 未知项目 404
    assert client.get("/api/v1/projects/proj_nope/suites").status_code == 404


def test_judge_writes_run_summary_and_lists(client):
    pid, sid = _project_with_suite(client)
    suite = client.get(f"/api/v1/suites/{sid}").json()
    answers = [
        {"case_id": c["id"], "answer": c["expected_answer"], "total_tokens": 50}
        for c in suite["cases"]
    ]
    client.post(f"/api/v1/suites/{sid}/responses", json={"agent_name": "m", "answers": answers})
    rid = client.post(f"/api/v1/suites/{sid}/judge", json={}).json()["run_id"]

    # 该测试集 run 列表
    suite_runs = client.get(f"/api/v1/suites/{sid}/runs").json()
    assert len(suite_runs) == 1
    s = suite_runs[0]
    assert s["run_id"] == rid and s["layer"] == "response" and s["status"] == "done"
    assert s["metrics"]["total_cases"] == len(suite["cases"])

    # 项目级 run 列表也能看到
    proj_runs = client.get(f"/api/v1/projects/{pid}/runs").json()
    assert any(r["run_id"] == rid for r in proj_runs)


def test_report_history_links_open(client):
    """Phase 4 报告历史页：列出的 run 能按层打开 HTML / Markdown 报告。"""
    pid, sid = _project_with_suite(client)
    suite = client.get(f"/api/v1/suites/{sid}").json()
    answers = [
        {"case_id": c["id"], "answer": c["expected_answer"], "total_tokens": 10}
        for c in suite["cases"]
    ]
    client.post(f"/api/v1/suites/{sid}/responses", json={"agent_name": "m", "answers": answers})
    rid = client.post(f"/api/v1/suites/{sid}/judge", json={}).json()["run_id"]

    # 应用层报告（report-history 用 /runs/{rid}/report）
    html = client.get(f"/api/v1/runs/{rid}/report?format=html")
    assert html.status_code == 200 and "text/html" in html.headers["content-type"]
    md = client.get(f"/api/v1/runs/{rid}/report?format=md")
    assert md.status_code == 200
    # 未知 run → 404
    assert client.get("/api/v1/runs/run_nope/report").status_code == 404


def test_suite_state_steps(client):
    pid, sid = _project_with_suite(client)
    st = client.get(f"/api/v1/suites/{sid}/state").json()
    assert st["doc_count"] == 1
    assert st["case_count"] >= 1
    assert st["steps"]["documents"] is True
    assert st["steps"]["questions"] is True
    # 生成出题已带标准答案 → gold 步算完成；尚无证据 → evidence 未完成
    assert st["steps"]["evidence"] is False
    assert st["current_step"] in {"gold", "evidence"}
    assert st["latest_run"] is None
    # 未知测试集 404
    assert client.get("/api/v1/suites/suite_nope/state").status_code == 404


# --- Phase 1 · 黄金集资产化：草稿/确认 + CRUD + 只用 confirmed 打分 --------


def test_generate_produces_draft_gold(client):
    """LLM 出题应顺带把每题写进黄金库（草稿态，来源 llm_generated）。"""
    pid, sid = _project_with_suite(client)
    suite = client.get(f"/api/v1/suites/{sid}").json()

    # 全量黄金库：每道题都落了一条
    lib = client.get(f"/api/v1/projects/{pid}/gold").json()["records"]
    assert len(lib) == len(suite["cases"]) >= 1
    assert all(g["source_kind"] == "llm_generated" for g in lib)
    assert all(g["status"] == "draft" for g in lib)

    # status 过滤：draft 全在，confirmed 为空
    drafts = client.get(f"/api/v1/projects/{pid}/gold?status=draft").json()["records"]
    assert len(drafts) == len(lib)
    confirmed = client.get(f"/api/v1/projects/{pid}/gold?status=confirmed").json()["records"]
    assert confirmed == []


def test_gold_crud_roundtrip(client):
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]

    # 新建（默认 confirmed / manual）
    r = client.post(
        f"/api/v1/projects/{pid}/gold",
        json={
            "question": "默认 APN 是什么？",
            "question_type": "factoid",
            "expected_answer": "默认 APN 为 cmnet。",
            "expected_evidence": ["默认 APN 为 cmnet"],
            "expected_entities": ["cmnet"],
            "source_doc": "APN.md",
        },
    )
    assert r.status_code == 200
    g = r.json()
    fp = g["fingerprint"]
    assert g["status"] == "confirmed" and g["source_kind"] == "manual"

    # 同问题再建 → 409
    assert client.post(
        f"/api/v1/projects/{pid}/gold", json={"question": "默认 APN 是什么？"}
    ).status_code == 409

    # 读
    assert client.get(f"/api/v1/gold/{fp}").json()["expected_entities"] == ["cmnet"]

    # 改（patch 部分字段；状态不动）
    r = client.put(
        f"/api/v1/gold/{fp}",
        json={"expected_answer": "默认 APN 为 cmnet，超时 30 秒。", "difficulty": "hard"},
    )
    assert r.status_code == 200
    g2 = r.json()
    assert g2["expected_answer"].endswith("超时 30 秒。")
    assert g2["difficulty"] == "hard"
    assert g2["expected_entities"] == ["cmnet"]  # 未传 → 保留
    assert g2["updated_at"] >= g["created_at"]

    # 删
    assert client.delete(f"/api/v1/gold/{fp}").json()["deleted"] is True
    assert client.get(f"/api/v1/gold/{fp}").status_code == 404
    assert client.delete(f"/api/v1/gold/{fp}").status_code == 404


def test_confirm_single_draft(client):
    pid, sid = _project_with_suite(client)
    draft = client.get(f"/api/v1/projects/{pid}/gold?status=draft").json()["records"][0]
    fp = draft["fingerprint"]
    r = client.post(f"/api/v1/gold/{fp}:confirm")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert client.post("/api/v1/gold/fp_nope:confirm").status_code == 404


def test_only_annotated_includes_cases_with_facts(client, monkeypatch):
    """黄金门禁退役后：有黄金事实的用例即纳入 only_annotated 批量评估，
    不再要求「已确认」状态（草稿也算）。"""
    from runtime_eval.eval_api import app as app_module

    monkeypatch.setattr(app_module, "pull_live_cases", lambda *a, **k: _live_cases_for_draft())
    pid = client.post("/api/v1/projects", json={"name": "p"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/retrieval/live:pull", json={"limit": 10}
    ).json()["suite_id"]

    # 给 ql-1 标注黄金事实，再降级为草稿——退役后草稿也参与打分
    client.put(
        f"/api/v1/suites/{sid}/cases/ql-1/gold",
        json={"expected_evidence": ["默认 APN 为 cmnet"], "expected_entities": ["cmnet"]},
    )
    fp = client.get(f"/api/v1/projects/{pid}/gold").json()["records"][0]["fingerprint"]
    client.put(f"/api/v1/gold/{fp}", json={"status": "draft"})

    # 仅 ql-1 有黄金事实（草稿也算）→ 只评这 1 条
    r = client.post(
        f"/api/v1/suites/{sid}/retrieval:evaluate",
        json={"k_values": [1, 3], "only_annotated": True},
    )
    assert r.status_code == 200
    assert r.json()["evaluated_cases"] == 1


# --- L4 业务价值层：对照组增量（uplift）编排 ---


class _FakeUpliftClient:
    """duck-typed LLMClient：闭卷路给低分、用库路给高分，便于验证增量为正。"""

    def __init__(self):
        self.routes_seen: list[str] = []

    def run_agent(self, *, question, system=None, route="kb"):
        self.routes_seen.append(route)
        return {
            "answer": f"[{route}] {question}",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "num_turns": 1,
            "tool_calls": [],
            "retrieved_items": [],
        }

    def judge(self, *, question, question_type, expected_answer, key_points, agent_answer):
        score = 0.9 if agent_answer.startswith("[kb]") else 0.2
        return {
            "score": score,
            "verdict": "correct" if score >= 0.8 else "incorrect",
            "rationale": "",
            "covered_points": [],
            "missed_points": [],
            "usage": {},
        }


def _uplift_suite():
    from runtime_eval.shared.models import QuestionType, SourceRef, TestCase, TestSuite

    return TestSuite(
        suite_id="s_up",
        project_id="p_up",
        cases=[
            TestCase(
                id=f"c{i}",
                question=f"q{i}",
                question_type=QuestionType.FACTOID,
                expected_answer="x",
                source=SourceRef(doc="d"),
                difficulty="normal",
            )
            for i in range(3)
        ],
    )


def test_run_value_uplift_orchestration(tmp_path):
    from runtime_eval.eval_api import orchestrator

    config = ApiConfig(workspace_dir=tmp_path / "workspace")
    store = Store(config)
    suite = _uplift_suite()
    store.save_suite(suite)

    fake = _FakeUpliftClient()
    summary = orchestrator.run_value_uplift(store, fake, config, suite)

    # 两路都跑过：闭卷 + 用库
    assert "closed_book" in fake.routes_seen and "kb" in fake.routes_seen
    assert summary.layer == "value" and summary.kind == "uplift"
    assert summary.run_id == "uplift_s_up"

    # 用库全高分、闭卷全低分 → 净增量为正、胜率满、不可替代满
    h = summary.metrics["headline"]
    assert h["net_uplift"] > 0.5
    assert h["win_rate"] == 1.0
    assert summary.metrics["n"] == 3

    # 两路 EvalRun 用稳定 id 落库，供逐题对照详情读取
    assert store.get_run("run_cb_s_up") is not None
    assert store.get_run("run_kb_s_up") is not None
    # 快照可回读
    assert store.get_run_summary("uplift_s_up") is not None
    assert store.latest_run_summary("s_up", layer="value") is not None


# --- 评估工作台改造（概念统一 / 黄金退役 / 删项目 / 导入命名 / 编辑）---------

_IMPORT_YAML = (
    "- id: c1\n"
    "  question: 啥是A\n"
    "  question_type: factoid\n"
    "  expected_answer: A是A\n"
    "  source: {doc: d}\n"
)


def test_import_suite_with_custom_name(client):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "2024客服题"},
        files={"file": ("cases.yaml", _IMPORT_YAML, "text/yaml")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "2024客服题"


def test_import_suite_name_defaults_to_filename(client):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        files={"file": ("cases.yaml", _IMPORT_YAML, "text/yaml")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "cases"


def test_put_suite_replaces_cases(client):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "S"},
        files={"file": ("c.yaml", _IMPORT_YAML, "text/yaml")},
    ).json()["suite_id"]

    new_cases = [
        {"id": "c1", "question": "改过的q", "question_type": "factoid",
         "expected_answer": "改过的a", "key_points": ["要点1"],
         "source": {"doc": "d"}, "difficulty": "hard"},
        {"id": "c2", "question": "新增题", "question_type": "factoid",
         "expected_answer": "a2", "source": {"doc": "d"}},
    ]
    r = client.put(f"/api/v1/suites/{sid}", json={"cases": new_cases})
    assert r.status_code == 200
    body = r.json()
    assert len(body["cases"]) == 2
    assert body["cases"][0]["question"] == "改过的q"
    assert body["cases"][0]["difficulty"] == "hard"
    assert body["cases"][1]["id"] == "c2"


def test_put_suite_rejects_empty(client):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "S"},
        files={"file": ("c.yaml", _IMPORT_YAML, "text/yaml")},
    ).json()["suite_id"]
    assert client.put(f"/api/v1/suites/{sid}", json={"cases": []}).status_code == 400


def test_delete_project_endpoint_cascade(client):
    pid = client.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    sid = client.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "S"},
        files={"file": ("c.yaml", _IMPORT_YAML, "text/yaml")},
    ).json()["suite_id"]
    assert client.get(f"/api/v1/suites/{sid}").status_code == 200

    assert client.delete(f"/api/v1/projects/{pid}").status_code == 200
    assert all(p["project_id"] != pid for p in client.get("/api/v1/projects").json())
    assert client.get(f"/api/v1/projects/{pid}/suites").status_code == 404
    assert client.delete(f"/api/v1/projects/{pid}").status_code == 404


def test_confirmed_gold_facts_ignores_gate(tmp_path):
    from runtime_eval.eval_api import orchestrator
    from runtime_eval.eval_api.config import ApiConfig
    from runtime_eval.eval_api.store import Store
    from runtime_eval.shared.models import (
        GoldRecord, QuestionType, SourceRef, TestCase,
    )

    store = Store(ApiConfig(workspace_dir=tmp_path / "ws"))
    case = TestCase(
        id="c1", question="啥是A", question_type=QuestionType.FACTOID,
        expected_answer="A是A", expected_evidence=["事实X"],
        source=SourceRef(doc="d"),
    )
    # 存一条「草稿」黄金：旧逻辑会因未确认而返回空，退役后应忽略门禁
    store.save_gold(GoldRecord(
        fingerprint=GoldRecord.make_fingerprint(case.question),
        question=case.question, status="draft",
    ))
    assert orchestrator.confirmed_gold_facts(store, case) == ["事实X"]
