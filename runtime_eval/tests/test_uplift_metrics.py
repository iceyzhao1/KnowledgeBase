"""L4 业务价值层：对照组增量指标（uplift）单测。

纯函数 ``compute_uplift`` 接收两个 EvalRun（基线 A=闭卷 / 处理 C=用库）+ TestSuite，
按 case_id 对齐后逐题相减，算出五个诊断桶 + headline 四件套 + 分层增量。
这里手搓 EvalRun 覆盖五桶边界与难度加权，不碰任何 I/O。
"""

from __future__ import annotations

from runtime_eval.eval_api.uplift_metrics import compute_uplift
from runtime_eval.shared.models import (
    CaseResult,
    EvalRun,
    QuestionType,
    SourceRef,
    TestCase,
    TestSuite,
    Verdict,
)


def _case_result(case_id: str, score: float, qtype: QuestionType = QuestionType.FACTOID) -> CaseResult:
    verdict = Verdict.CORRECT if score >= 0.8 else Verdict.PARTIAL if score > 0 else Verdict.INCORRECT
    return CaseResult(case_id=case_id, question_type=qtype, verdict=verdict, score=score)


def _run(run_id: str, scores: dict[str, float], qtypes: dict[str, QuestionType] | None = None) -> EvalRun:
    qtypes = qtypes or {}
    return EvalRun(
        run_id=run_id,
        suite_id="s1",
        results=[
            _case_result(cid, sc, qtypes.get(cid, QuestionType.FACTOID))
            for cid, sc in scores.items()
        ],
    )


def _suite(difficulties: dict[str, str], qtypes: dict[str, QuestionType] | None = None) -> TestSuite:
    qtypes = qtypes or {}
    return TestSuite(
        suite_id="s1",
        cases=[
            TestCase(
                id=cid,
                question=f"q-{cid}",
                question_type=qtypes.get(cid, QuestionType.FACTOID),
                expected_answer="x",
                source=SourceRef(doc="d"),
                difficulty=diff,
            )
            for cid, diff in difficulties.items()
        ],
    )


def test_five_buckets():
    # 默认 tau_low=0.4, tau_high=0.6, delta=0.1
    baseline = _run("A", {
        "reg": 0.90,   # 帮倒忙：sc < sa - delta
        "exc": 0.20,   # 不可替代：sa<0.4 且 sc>=0.6
        "both": 0.10,  # 双输：两路都 <0.4
        "boost": 0.50, # 锦上添花：sc > sa + delta（且不属前三桶）
        "tie": 0.70,   # 平局：差距在 ±delta 内
    })
    treatment = _run("C", {
        "reg": 0.50,
        "exc": 0.85,
        "both": 0.15,
        "boost": 0.75,
        "tie": 0.72,
    })
    suite = _suite({"reg": "normal", "exc": "normal", "both": "normal", "boost": "normal", "tie": "normal"})

    rep = compute_uplift(baseline, treatment, suite)
    by_id = {c.case_id: c.bucket for c in rep.cases}
    assert by_id == {
        "reg": "regression",
        "exc": "exclusive",
        "both": "both_fail",
        "boost": "boost",
        "tie": "tie",
    }
    assert rep.n == 5


def test_bucket_precedence_regression_over_both_fail():
    # 两路都低，但 sc 还比 sa 跌了 > delta —— 应优先判「帮倒忙」而非「双输」
    baseline = _run("A", {"x": 0.35})
    treatment = _run("C", {"x": 0.10})
    suite = _suite({"x": "normal"})
    rep = compute_uplift(baseline, treatment, suite)
    assert rep.cases[0].bucket == "regression"


def test_headline_aggregates():
    baseline = _run("A", {"a": 0.2, "b": 0.5, "c": 0.9})
    treatment = _run("C", {"a": 0.8, "b": 0.55, "c": 0.6})  # a:+0.6赢/不可替代, b:平, c:-0.3帮倒忙
    suite = _suite({"a": "normal", "b": "normal", "c": "normal"})
    rep = compute_uplift(baseline, treatment, suite)

    assert abs(rep.win_rate - 1 / 3) < 1e-3          # 只有 a 赢（>+delta）
    assert abs(rep.regression_rate - 1 / 3) < 1e-3   # 只有 c 跌（<-delta）
    assert abs(rep.exclusivity - 1 / 3) < 1e-3       # 只有 a 不可替代
    # 等权净增量 = (0.6 + 0.05 - 0.3)/3 = 0.35/3
    assert abs(rep.net_uplift - (0.35 / 3)) < 1e-3


def test_difficulty_weighting():
    # easy 题大涨、hard 题小跌；难度加权后 hard 权重大，净增量应低于等权
    baseline = _run("A", {"e": 0.2, "h": 0.6})
    treatment = _run("C", {"e": 1.0, "h": 0.5})  # easy +0.8, hard -0.1
    suite = _suite({"e": "easy", "h": "hard"})
    rep = compute_uplift(baseline, treatment, suite)
    # 加权 = (1.0*0.8 + 2.0*(-0.1)) / (1.0+2.0) = 0.6/3 = 0.2
    assert abs(rep.net_uplift - 0.2) < 1e-9


def test_value_pass_gate():
    # 净增量达标且负增量率低 → PASS
    good_base = _run("A", {"a": 0.2, "b": 0.3})
    good_treat = _run("C", {"a": 0.8, "b": 0.9})
    suite = _suite({"a": "normal", "b": "normal"})
    assert compute_uplift(good_base, good_treat, suite).value_pass is True

    # 负增量率过高 → 不 PASS（即便平均涨）
    base = _run("A", {"a": 0.2, "b": 0.9, "c": 0.9})
    treat = _run("C", {"a": 0.95, "b": 0.5, "c": 0.5})  # 2/3 帮倒忙
    rep = compute_uplift(base, treat, suite=_suite({"a": "n", "b": "n", "c": "n"}))
    assert rep.regression_rate > 0.05
    assert rep.value_pass is False


def test_alignment_only_common_cases():
    baseline = _run("A", {"a": 0.5, "b": 0.5, "only_a": 0.5})
    treatment = _run("C", {"a": 0.9, "b": 0.9, "only_c": 0.9})
    suite = _suite({"a": "normal", "b": "normal"})
    rep = compute_uplift(baseline, treatment, suite)
    assert rep.n == 2
    assert {c.case_id for c in rep.cases} == {"a", "b"}


def test_by_type_and_difficulty_breakdown():
    qt = {"a": QuestionType.FACTOID, "b": QuestionType.CONCEPTUAL}
    baseline = _run("A", {"a": 0.2, "b": 0.4}, qtypes=qt)
    treatment = _run("C", {"a": 0.6, "b": 0.5}, qtypes=qt)
    suite = _suite({"a": "easy", "b": "hard"}, qtypes=qt)
    rep = compute_uplift(baseline, treatment, suite)

    by_type = {b.key: b for b in rep.by_type}
    assert abs(by_type["factoid"].mean_uplift - 0.4) < 1e-9
    assert abs(by_type["conceptual"].mean_uplift - 0.1) < 1e-9

    by_diff = {b.key: b for b in rep.by_difficulty}
    assert abs(by_diff["easy"].mean_uplift - 0.4) < 1e-9
    assert abs(by_diff["hard"].mean_uplift - 0.1) < 1e-9


def test_to_metrics_dict_roundtrip():
    baseline = _run("run_cb_s1", {"a": 0.3})
    treatment = _run("run_kb_s1", {"a": 0.8})
    rep = compute_uplift(baseline, treatment, _suite({"a": "normal"}))
    d = rep.to_metrics_dict()
    assert d["baseline_run_id"] == "run_cb_s1"
    assert d["treatment_run_id"] == "run_kb_s1"
    assert d["headline"]["net_uplift"] == rep.net_uplift
    assert len(d["cases"]) == 1


def test_render_uplift_markdown():
    from runtime_eval.eval_api.report import render_uplift_markdown

    baseline = _run("A", {"a": 0.2, "b": 0.9})
    treatment = _run("C", {"a": 0.85, "b": 0.6})
    rep = compute_uplift(baseline, treatment, _suite({"a": "easy", "b": "hard"}))
    md = render_uplift_markdown(rep.to_metrics_dict(), suite_id="s1")

    assert "增量价值报告（L4）· s1" in md
    assert "净增量 net_uplift" in md
    assert "不可替代" in md  # 诊断桶中文标签
    assert "逐题对照" in md
    # a 题：闭卷 0.2 → 用库 0.85，应在逐题表里出现正增量
    assert "+0.65" in md
