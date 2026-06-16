"""Tests for the composite S_q / PASS / 诊断桶 / 难度加权 scoring layer.

These cover the design doc (2026-06-03-retrieval-scoring-system-design) additions
on top of the raw IR metrics, including graceful degradation when no gold facts
are present (live-log path).
"""

from __future__ import annotations

from runtime_eval.eval_api.retrieval_metrics import (
    ScoringParams,
    compute_retrieval_metrics,
)
from runtime_eval.shared.models import (
    QuestionType,
    RetrievalCaseResult,
    RetrievalRun,
)

QT = QuestionType.all()[0]


def _run(results, ks=(1, 3, 5, 10)):
    return RetrievalRun(run_id="r", suite_id="s", k_values=list(ks), results=results)


def test_composite_score_matches_weighted_formula():
    # 2 relevant items (rank 0,2), 5 gold facts, 4 covered.
    r = RetrievalCaseResult(
        case_id="q1",
        question_type=QT,
        difficulty="hard",
        retrieved_count=10,
        gold_count=5,
        item_grades=[3, 0, 3, 0, 0, 0, 0, 0, 0, 0],
        gold_covered_at=[1, 1, 3, 3, 0],
    )
    m = compute_retrieval_metrics(_run([r]), ScoringParams())
    c = m.cases[0]
    # sub-metrics @10
    assert c.recall_k == 0.8
    assert c.mrr_k == 1.0
    assert c.context_recall == 0.8
    assert c.precision_k == 0.2  # 2 relevant / 10
    # S_q = 0.40*recall + 0.25*(0.5*mrr+0.5*ndcg) + 0.35*(0.4*prec + 0.6*ctxR)
    expected = (
        0.40 * c.recall_k
        + 0.25 * (0.5 * c.mrr_k + 0.5 * c.ndcg_k)
        + 0.35 * (0.4 * c.precision_k + 0.6 * c.context_recall)
    )
    assert c.s_q == round(expected, 4)
    assert c.passed is True  # recall .8>=.5, rank_first 0<=3, ctxR .8>=.8


def test_difficulty_weighted_kb_score():
    # easy (w=1.0) perfect; hard (w=2.0) weaker -> KB score skews toward hard.
    easy = RetrievalCaseResult(
        case_id="e", question_type=QT, difficulty="easy",
        retrieved_count=2, gold_count=2,
        item_grades=[3, 3], gold_covered_at=[1, 2],
    )
    hard = RetrievalCaseResult(
        case_id="h", question_type=QT, difficulty="hard",
        retrieved_count=4, gold_count=4,
        item_grades=[0, 3, 0, 0], gold_covered_at=[2, 0, 0, 0],
    )
    m = compute_retrieval_metrics(_run([easy, hard]), ScoringParams())
    se = next(c.s_q for c in m.cases if c.case_id == "e")
    sh = next(c.s_q for c in m.cases if c.case_id == "h")
    assert m.gold_cases == 2
    # weighted: (1.0*se + 2.0*sh) / 3.0
    assert m.kb_score == round((1.0 * se + 2.0 * sh) / 3.0, 4)
    diffs = {d.difficulty: d for d in m.by_difficulty}
    assert diffs["easy"].kb_score == se
    assert diffs["hard"].kb_score == sh


def test_diagnosis_buckets():
    rs = [
        # healthy: relevant up front, precision ok, full coverage
        RetrievalCaseResult(case_id="ok", question_type=QT, retrieved_count=3,
                            gold_count=2, item_grades=[3, 3, 0],
                            gold_covered_at=[1, 2]),
        # empty: nothing retrieved
        RetrievalCaseResult(case_id="emp", question_type=QT, retrieved_count=0,
                            gold_count=2, item_grades=[], gold_covered_at=[0, 0]),
        # miss: items but nothing relevant
        RetrievalCaseResult(case_id="mis", question_type=QT, retrieved_count=3,
                            gold_count=2, item_grades=[0, 0, 0],
                            gold_covered_at=[0, 0]),
        # buried: first relevant at rank 5 (>3)
        RetrievalCaseResult(case_id="bur", question_type=QT, retrieved_count=6,
                            gold_count=1, item_grades=[0, 0, 0, 0, 0, 3],
                            gold_covered_at=[6]),
    ]
    m = compute_retrieval_metrics(_run([rs[0]]), ScoringParams())
    assert m.cases[0].bucket == "healthy"
    by_id = {c.case_id: c.bucket for c in compute_retrieval_metrics(_run(rs)).cases}
    assert by_id["emp"] == "empty"
    assert by_id["mis"] == "miss"
    assert by_id["bur"] == "buried"


def test_unanswerable_bucket_needs_gold():
    # relevant retrieved & ranks well, but only 1/3 gold facts covered -> unanswerable
    r = RetrievalCaseResult(
        case_id="u", question_type=QT, retrieved_count=3, gold_count=3,
        item_grades=[3, 3, 3], gold_covered_at=[1, 0, 0],
    )
    m = compute_retrieval_metrics(_run([r]), ScoringParams())
    assert m.cases[0].context_recall < 0.8
    assert m.cases[0].bucket == "unanswerable"


def test_no_gold_degrades_composite_but_keeps_buckets():
    rs = [
        RetrievalCaseResult(case_id="a", question_type=QT, retrieved_count=5,
                            gold_count=0, item_grades=[3, 2, 0, 0, 0]),
        RetrievalCaseResult(case_id="c", question_type=QT, retrieved_count=5,
                            gold_count=0, item_grades=[0, 0, 0, 0, 0]),
    ]
    m = compute_retrieval_metrics(_run(rs), ScoringParams())
    assert m.has_gold is False
    assert m.kb_score is None and m.pass_rate is None
    assert m.gold_cases == 0
    for c in m.cases:
        assert c.s_q is None and c.passed is None
    # buckets still populated from grades alone
    assert m.buckets.get("miss") == 1
    # "a" is not noisy (precision 2/5=0.4>=0.3) -> healthy
    assert m.buckets.get("healthy") == 1


def test_score_k_falls_back_when_not_in_k_values():
    r = RetrievalCaseResult(case_id="x", question_type=QT, retrieved_count=2,
                            gold_count=1, item_grades=[3, 0], gold_covered_at=[1])
    # score_k=10 not present in k_values [1,3] -> should fall back to max (3)
    m = compute_retrieval_metrics(_run([r], ks=(1, 3)), ScoringParams(score_k=10))
    assert m.score_k == 3
    assert m.cases[0].s_q is not None
