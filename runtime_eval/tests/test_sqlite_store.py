"""Tests for the SQLite store backend + create_store factory.

Verifies the SqliteStore round-trips every artifact model, resolves
id->project, upserts on re-save, and that create_store / the ``Store`` alias
both resolve to SQLite (the JSON backend was removed).
"""

from __future__ import annotations

from runtime_eval.eval_api.config import ApiConfig
from runtime_eval.eval_api.sqlite_store import SqliteStore
from runtime_eval.eval_api.store import Store, create_store
from runtime_eval.shared.models import (
    CaseResult,
    Document,
    EvalRun,
    GoldRecord,
    Project,
    QuestionType,
    ResponseSet,
    AgentResponse,
    RetrievalCaseResult,
    RetrievalRun,
    RetrievalSet,
    RetrievedItem,
    RunSummary,
    SourceRef,
    TestCase,
    TestSuite,
    Verdict,
)

QT = QuestionType.FACTOID


def _store(tmp_path) -> SqliteStore:
    return SqliteStore(ApiConfig(workspace_dir=tmp_path / "ws"))


def _suite(suite_id="s1", project_id="p1") -> TestSuite:
    return TestSuite(
        suite_id=suite_id,
        project_id=project_id,
        cases=[
            TestCase(
                id="c1", question="默认 APN 是？", question_type=QT,
                expected_answer="cmnet", key_points=["cmnet"],
                expected_evidence=["默认 APN 为 cmnet"],
                source=SourceRef(doc="APN.md"), difficulty="easy",
            )
        ],
    )


def test_roundtrip_all_models(tmp_path):
    st = _store(tmp_path)

    st.save_project(Project(project_id="p1", name="演示"))
    assert st.get_project("p1").name == "演示"
    assert [p.project_id for p in st.list_projects()] == ["p1"]

    doc = Document(document_id="d1", project_id="p1", name="APN.md",
                   rel_path="APN.md", char_count=10, text="默认 APN 为 cmnet")
    st.save_document(doc)
    assert st.get_document("p1", "d1").text == "默认 APN 为 cmnet"
    assert st.get_document("p2", "d1") is None  # wrong project
    assert [d.document_id for d in st.list_documents("p1")] == ["d1"]

    st.save_suite(_suite())
    got = st.get_suite("s1")
    assert got is not None and got.cases[0].expected_evidence == ["默认 APN 为 cmnet"]

    rs = ResponseSet(suite_id="s1", responses=[AgentResponse(case_id="c1", answer="cmnet")])
    st.save_responses(rs)
    assert st.get_responses("s1").by_case("c1").answer == "cmnet"

    run = EvalRun(run_id="run1", suite_id="s1", project_id="p1",
                  results=[CaseResult(case_id="c1", question_type=QT,
                                      verdict=Verdict.CORRECT, score=1.0)])
    st.save_run(run)
    assert st.get_run("run1").results[0].score == 1.0

    rset = RetrievalSet(suite_id="s1",
                        items={"c1": [RetrievedItem(rank=1, text="默认 APN 为 cmnet")]})
    st.save_retrieval_set(rset)
    assert st.get_retrieval_set("s1").for_case("c1")[0].rank == 1

    rrun = RetrievalRun(run_id="rrun1", suite_id="s1", project_id="p1",
                        results=[RetrievalCaseResult(case_id="c1", question_type=QT,
                                                     retrieved_count=1, gold_count=1,
                                                     item_grades=[3], gold_covered_at=[1])])
    st.save_retrieval_run(rrun)
    assert st.get_retrieval_run("rrun1").results[0].item_grades == [3]


def test_id_resolution_and_missing(tmp_path):
    st = _store(tmp_path)
    st.save_suite(_suite())
    st.save_run(EvalRun(run_id="run1", suite_id="s1", project_id="p1"))
    st.save_retrieval_run(RetrievalRun(run_id="rrun1", suite_id="s1", project_id="p1"))

    assert st.project_of_suite("s1") == "p1"
    assert st.project_of_run("run1") == "p1"
    assert st.project_of_retrieval_run("rrun1") == "p1"
    assert st.project_of_suite("nope") is None

    # missing reads degrade gracefully
    assert st.get_suite("nope") is None
    assert st.get_run("nope") is None
    assert st.get_responses("nope").responses == []
    assert st.get_retrieval_set("nope").items == {}


def test_upsert_overwrites(tmp_path):
    st = _store(tmp_path)
    st.save_suite(_suite())
    s = _suite()
    s.backend = "rewritten"
    st.save_suite(s)  # same suite_id -> update, not duplicate
    assert st.get_suite("s1").backend == "rewritten"
    # responses keyed by suite_id also upsert
    st.save_responses(ResponseSet(suite_id="s1",
                                  responses=[AgentResponse(case_id="c1", answer="a")]))
    st.save_responses(ResponseSet(suite_id="s1",
                                  responses=[AgentResponse(case_id="c1", answer="b")]))
    assert st.get_responses("s1").by_case("c1").answer == "b"


def test_persists_across_instances(tmp_path):
    cfg = ApiConfig(workspace_dir=tmp_path / "ws")
    SqliteStore(cfg).save_suite(_suite())
    # a fresh store on the same db file sees the data
    assert SqliteStore(cfg).get_suite("s1") is not None


def test_gold_roundtrip_and_fingerprint(tmp_path):
    st = _store(tmp_path)
    q = "默认 APN 是什么？"
    fp = GoldRecord.make_fingerprint(q)
    # 归一化：大小写/空白不影响指纹
    assert fp == GoldRecord.make_fingerprint("  默认 APN 是什么？  ")

    gold = GoldRecord(
        fingerprint=fp, project_id="p1", question=q, question_type=QT,
        expected_answer="cmnet", expected_evidence=["默认 APN 为 cmnet"],
        expected_entities=["cmnet"], source=SourceRef(doc="APN.md"),
        difficulty="easy",
    )
    st.save_gold(gold)
    assert st.get_gold(fp).expected_answer == "cmnet"
    assert st.match_gold(q).expected_entities == ["cmnet"]
    assert st.match_gold("从未标注过的问题") is None
    assert [g.fingerprint for g in st.list_gold("p1")] == [fp]
    assert st.list_gold("p2") == []

    # 同指纹再存 → 更新而非重复
    gold.expected_answer = "改写"
    st.save_gold(gold)
    assert st.get_gold(fp).expected_answer == "改写"
    assert len(st.list_gold()) == 1

    # apply_to_case 回填黄金字段到用例
    case = TestCase(id="cX", question=q, question_type=QT,
                    expected_answer="", source=SourceRef(doc="x"))
    gold.apply_to_case(case)
    assert case.expected_answer == "改写" and case.difficulty == "easy"


def test_gold_status_and_delete(tmp_path):
    st = _store(tmp_path)
    q = "默认 APN 是什么？"
    fp = GoldRecord.make_fingerprint(q)

    # 旧数据兜底：不写 status/source_kind 时默认 confirmed / manual
    gold = GoldRecord(fingerprint=fp, project_id="p1", question=q, question_type=QT)
    assert gold.status == "confirmed" and gold.source_kind == "manual"

    # 草稿态可往返存取，且按 status 可筛
    draft = GoldRecord(
        fingerprint=GoldRecord.make_fingerprint("另一个问题"),
        project_id="p1", question="另一个问题", question_type=QT,
        status="draft", source_kind="llm_generated",
    )
    st.save_gold(gold)
    st.save_gold(draft)
    statuses = {g.fingerprint: g.status for g in st.list_gold("p1")}
    assert statuses[fp] == "confirmed" and statuses[draft.fingerprint] == "draft"

    # 删除：命中返回 True，再删返回 False
    assert st.delete_gold(fp) is True
    assert st.get_gold(fp) is None
    assert st.delete_gold(fp) is False
    assert [g.fingerprint for g in st.list_gold("p1")] == [draft.fingerprint]


def test_list_suites(tmp_path):
    st = _store(tmp_path)
    st.save_suite(_suite(suite_id="s1", project_id="p1"))
    st.save_suite(_suite(suite_id="s2", project_id="p1"))
    st.save_suite(_suite(suite_id="s3", project_id="p2"))
    assert {s.suite_id for s in st.list_suites("p1")} == {"s1", "s2"}
    assert [s.suite_id for s in st.list_suites("p2")] == ["s3"]
    assert len(st.list_suites()) == 3  # 全量


def test_run_summary_roundtrip_and_filters(tmp_path):
    st = _store(tmp_path)
    st.save_suite(_suite(suite_id="s1", project_id="p1"))

    # 逐题：先 partial、再覆盖为 done（同一 run_id upsert）
    st.save_run_summary(RunSummary(run_id="rrun_s1", suite_id="s1", project_id="p1",
                                   layer="retrieval", kind="perq", status="partial",
                                   k=5, metrics={"kb_score": 0.4}))
    st.save_run_summary(RunSummary(run_id="rrun_s1", suite_id="s1", project_id="p1",
                                   layer="retrieval", kind="perq", status="done",
                                   k=5, metrics={"kb_score": 0.8}))
    got = st.get_run_summary("rrun_s1")
    assert got.status == "done" and got.metrics["kb_score"] == 0.8

    # 另一条生成层 run
    st.save_run_summary(RunSummary(run_id="run_resp", suite_id="s1", project_id="p1",
                                   layer="response", kind="batch", status="done",
                                   metrics={"overall_accuracy": 1.0}))

    by_suite = st.list_run_summaries(suite_id="s1")
    assert len(by_suite) == 2
    by_proj = st.list_run_summaries(project_id="p1")
    assert len(by_proj) == 2
    assert len(st.list_run_summaries()) == 2  # 全量

    # latest_run_summary 可按 layer 过滤
    assert st.latest_run_summary("s1", layer="retrieval").run_id == "rrun_s1"
    assert st.latest_run_summary("s1", layer="response").run_id == "run_resp"
    assert st.latest_run_summary("nope") is None


def test_save_run_summary_infers_project(tmp_path):
    st = _store(tmp_path)
    st.save_suite(_suite(suite_id="s1", project_id="p1"))
    # 不显式给 project_id → 从 suite 推断
    saved = st.save_run_summary(RunSummary(run_id="r1", suite_id="s1",
                                           layer="response", metrics={}))
    assert saved.project_id == "p1"
    assert st.get_run_summary("r1").project_id == "p1"


def test_create_store_factory(tmp_path):
    # SQLite 是唯一后端：工厂与 ``Store`` 别名都解析为 SqliteStore。
    cfg = ApiConfig(workspace_dir=tmp_path / "ws")
    assert isinstance(create_store(cfg), SqliteStore)
    assert Store is SqliteStore
    assert isinstance(Store(ApiConfig(workspace_dir=tmp_path / "ws2")), SqliteStore)
