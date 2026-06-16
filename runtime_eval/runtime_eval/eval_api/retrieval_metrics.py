"""Aggregate a RetrievalRun into report-ready IR (检索层) metrics.

The LLM relevance judge produces, per case, only raw labels:
- ``item_grades``     – a 0–3 relevance grade per retrieved item, in rank order;
- ``gold_covered_at`` – the 1-based rank of the first item covering each gold
  fact (0 if no item covers it).

From those two label lists every aggregate metric below is computed
deterministically, so multiple K can be reported off a single judge pass:

- HitRate@K        命中率：top-K 内是否至少命中一个相关条目
- MRR@K            首个相关条目排名倒数的均值
- NDCG@K           归一化折损累计增益（用 0–3 分级）
- ContextPrecision@K  top-K 中相关条目占比（精确率）
- Recall@K         top-K 覆盖的黄金事实占比（召回率）
- ContextRecall    任意排名下被覆盖的黄金事实占比（整体召回）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import log2
from statistics import mean

from ..shared.models import QuestionType, RetrievalRun


def _dcg(grades: list[int]) -> float:
    return sum((2**g - 1) / log2(i + 2) for i, g in enumerate(grades))


def _hit_rate_at(grades: list[int], k: int) -> float:
    return 1.0 if any(g > 0 for g in grades[:k]) else 0.0


def _mrr_at(grades: list[int], k: int) -> float:
    for i, g in enumerate(grades[:k], start=1):
        if g > 0:
            return 1.0 / i
    return 0.0


def _ndcg_at(grades: list[int], k: int) -> float:
    dcg = _dcg(grades[:k])
    ideal = _dcg(sorted(grades, reverse=True)[:k])
    return dcg / ideal if ideal > 0 else 0.0


def _precision_at(grades: list[int], retrieved_count: int, k: int) -> float:
    relevant = sum(1 for g in grades[:k] if g > 0)
    denom = min(k, retrieved_count)
    return relevant / denom if denom > 0 else 0.0


def _recall_at(covered_at: list[int], gold_count: int, k: int) -> float:
    if gold_count <= 0:
        return 0.0
    hit = sum(1 for r in covered_at if 1 <= r <= k)
    return hit / gold_count


def _context_recall(covered_at: list[int], gold_count: int) -> float:
    if gold_count <= 0:
        return 0.0
    return sum(1 for r in covered_at if r >= 1) / gold_count


def _rank_first(grades: list[int], k: int) -> int | None:
    """0-based rank of the first relevant (grade>0) item within top-K, or None."""
    for i, g in enumerate(grades[:k]):
        if g > 0:
            return i
    return None


@dataclass
class ScoringParams:
    """Weights/thresholds for S_q / PASS / 诊断桶（默认对齐设计文档）。"""

    w_find: float = 0.40
    w_rank: float = 0.25
    w_quality: float = 0.35
    quality_alpha: float = 0.40
    score_k: int = 10
    pass_recall: float = 0.5
    pass_rank: int = 3
    pass_ctx_recall: float = 0.8
    noisy_precision: float = 0.3
    difficulty_weights: dict[str, float] = field(
        default_factory=lambda: {"easy": 1.0, "normal": 1.5, "medium": 1.5, "hard": 2.0}
    )

    @classmethod
    def from_config(cls, config) -> "ScoringParams":
        return cls(
            w_find=config.score_w_find,
            w_rank=config.score_w_rank,
            w_quality=config.score_w_quality,
            quality_alpha=config.score_quality_alpha,
            score_k=config.score_k,
            pass_recall=config.pass_recall,
            pass_rank=config.pass_rank,
            pass_ctx_recall=config.pass_ctx_recall,
            noisy_precision=config.noisy_precision,
            difficulty_weights=dict(config.difficulty_weights),
        )

    def weight_for(self, difficulty: str) -> float:
        return self.difficulty_weights.get((difficulty or "normal").lower(), 1.0)


def _diagnose(
    *,
    retrieved_count: int,
    grades: list[int],
    recall_k: float,
    precision_k: float,
    ctx_recall: float,
    rank_first: int | None,
    has_gold: bool,
    p: ScoringParams,
) -> str:
    """Assign one diagnosis bucket（设计文档第 6 节，无黄金时优雅降级）。

    Buckets degrade gracefully: empty/miss/buried/noisy/healthy come from item
    grades alone; ``unanswerable`` needs gold (ContextRecall) and is skipped when
    no gold is present. ``miss`` falls back to "no relevant item graded" when
    gold is absent.
    """
    if retrieved_count == 0:
        return "empty"
    no_relevant = recall_k == 0.0 if has_gold else not any(g > 0 for g in grades)
    if no_relevant:
        return "miss"
    if rank_first is not None and rank_first > p.pass_rank:
        return "buried"
    if precision_k < p.noisy_precision:
        return "noisy"
    if has_gold and ctx_recall < p.pass_ctx_recall:
        return "unanswerable"
    return "healthy"


@dataclass
class RetrievalCaseScore:
    """Per-case composite score + diagnosis at K=score_k (for明细 + 桶分布)."""

    case_id: str
    question_type: str
    difficulty: str
    recall_k: float
    mrr_k: float
    ndcg_k: float
    precision_k: float
    context_recall: float
    rank_first: int | None
    bucket: str
    s_q: float | None = None  # None when无黄金（score_find/quality 无法定义）
    passed: bool | None = None  # None when无黄金（PASS 依赖 Recall/ContextRecall）


@dataclass
class RetrievalDifficultyMetrics:
    difficulty: str
    total: int = 0
    ndcg: float = 0.0
    recall: float = 0.0
    context_precision: float = 0.0
    kb_score: float | None = None
    pass_rate: float | None = None


@dataclass
class RetrievalTypeMetrics:
    question_type: str
    total: int = 0
    hit_rate: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    mrr: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    context_precision: dict[int, float] = field(default_factory=dict)
    context_recall: float = 0.0


@dataclass
class RetrievalReportMetrics:
    suite_id: str
    run_id: str = ""
    agent_name: str = "retriever"
    backend: str = "eval-llm"
    k_values: list[int] = field(default_factory=list)
    total_cases: int = 0
    judged_cases: int = 0  # cases with at least one retrieved item
    has_gold: bool = False  # any case carries gold facts -> recall-family is meaningful
    hit_rate: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
    mrr: dict[int, float] = field(default_factory=dict)
    ndcg: dict[int, float] = field(default_factory=dict)
    context_precision: dict[int, float] = field(default_factory=dict)
    context_recall: float = 0.0
    by_type: list[RetrievalTypeMetrics] = field(default_factory=list)
    # --- 综合分 / PASS / 诊断（设计文档第 5~7 节）---
    score_k: int = 10
    gold_cases: int = 0  # 带黄金标注的用例数（综合分/PASS 的分母）
    kb_score: float | None = None  # 难度加权 KB 综合分；无黄金时 None
    pass_rate: float | None = None  # 难度加权 KB 通过率；无黄金时 None
    buckets: dict[str, int] = field(default_factory=dict)  # 六桶诊断分布（全量）
    by_difficulty: list[RetrievalDifficultyMetrics] = field(default_factory=list)
    cases: list[RetrievalCaseScore] = field(default_factory=list)


def _avg(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def compute_retrieval_metrics(
    run: RetrievalRun, scoring: ScoringParams | None = None
) -> RetrievalReportMetrics:
    ks = sorted(run.k_values) or [1, 3, 5, 10]
    p = scoring or ScoringParams()
    # 综合分判定的 K 必须真实可取；若 score_k 不在 k_values 中，退到最大可用 K。
    sk = p.score_k if p.score_k in ks else max(ks)
    metrics = RetrievalReportMetrics(
        suite_id=run.suite_id,
        run_id=run.run_id,
        agent_name=run.agent_name,
        backend=run.backend,
        k_values=ks,
        score_k=sk,
        total_cases=len(run.results),
        has_gold=any(r.gold_count > 0 for r in run.results),
    )

    # per-K accumulators (overall) + per-type buckets
    hit = {k: [] for k in ks}
    rec = {k: [] for k in ks}
    mrr = {k: [] for k in ks}
    ndcg = {k: [] for k in ks}
    prec = {k: [] for k in ks}
    ctx_recall: list[float] = []

    type_acc: dict[str, dict] = {}

    def _bucket(t: str) -> dict:
        if t not in type_acc:
            type_acc[t] = {
                "total": 0,
                "hit": {k: [] for k in ks},
                "rec": {k: [] for k in ks},
                "mrr": {k: [] for k in ks},
                "ndcg": {k: [] for k in ks},
                "prec": {k: [] for k in ks},
                "ctx": [],
            }
        return type_acc[t]

    # difficulty buckets + diagnosis counts + difficulty-weighted composite sums
    diff_acc: dict[str, dict] = {}

    def _diff(d: str) -> dict:
        d = (d or "normal").lower()
        if d not in diff_acc:
            diff_acc[d] = {
                "total": 0,
                "ndcg": [],
                "rec": [],
                "prec": [],
                "w_sum": 0.0,  # Σ w_d  (gold cases only)
                "ws_q": 0.0,  # Σ w_d·S_q
                "w_pass": 0.0,  # Σ w_d·[PASS]
            }
        return diff_acc[d]

    bucket_counts: dict[str, int] = {}
    w_sum_total = 0.0  # Σ w_d over gold cases
    ws_q_total = 0.0  # Σ w_d·S_q
    w_pass_total = 0.0  # Σ w_d·[PASS]

    for r in run.results:
        grades = r.item_grades
        covered = r.gold_covered_at
        if not grades:
            # nothing retrieved -> still counts toward recall (all misses) so the
            # metric reflects coverage honestly; precision/ndcg default to 0.
            pass
        else:
            metrics.judged_cases += 1

        tb = _bucket(r.question_type.value)
        tb["total"] += 1

        for k in ks:
            h = _hit_rate_at(grades, k)
            rc = _recall_at(covered, r.gold_count, k)
            mr = _mrr_at(grades, k)
            ng = _ndcg_at(grades, k)
            pr = _precision_at(grades, r.retrieved_count or len(grades), k)
            hit[k].append(h); rec[k].append(rc); mrr[k].append(mr)
            ndcg[k].append(ng); prec[k].append(pr)
            tb["hit"][k].append(h); tb["rec"][k].append(rc); tb["mrr"][k].append(mr)
            tb["ndcg"][k].append(ng); tb["prec"][k].append(pr)

        cr = _context_recall(covered, r.gold_count)
        ctx_recall.append(cr)
        tb["ctx"].append(cr)

        # --- per-case composite / PASS / 诊断（在 K=sk 上）---
        recall_k = _recall_at(covered, r.gold_count, sk)
        mrr_k = _mrr_at(grades, sk)
        ndcg_k = _ndcg_at(grades, sk)
        prec_k = _precision_at(grades, r.retrieved_count or len(grades), sk)
        rank_first = _rank_first(grades, sk)
        has_gold_case = r.gold_count > 0

        score_rank = 0.5 * mrr_k + 0.5 * ndcg_k  # gold 无关
        s_q: float | None = None
        passed: bool | None = None
        if has_gold_case:
            score_find = recall_k
            score_quality = p.quality_alpha * prec_k + (1 - p.quality_alpha) * cr
            s_q = round(
                p.w_find * score_find + p.w_rank * score_rank + p.w_quality * score_quality,
                4,
            )
            passed = (
                recall_k >= p.pass_recall
                and rank_first is not None
                and rank_first <= p.pass_rank
                and cr >= p.pass_ctx_recall
            )

        bucket = _diagnose(
            retrieved_count=r.retrieved_count or len(grades),
            grades=grades,
            recall_k=recall_k,
            precision_k=prec_k,
            ctx_recall=cr,
            rank_first=rank_first,
            has_gold=has_gold_case,
            p=p,
        )
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        metrics.cases.append(
            RetrievalCaseScore(
                case_id=r.case_id,
                question_type=r.question_type.value,
                difficulty=(r.difficulty or "normal"),
                recall_k=round(recall_k, 4),
                mrr_k=round(mrr_k, 4),
                ndcg_k=round(ndcg_k, 4),
                precision_k=round(prec_k, 4),
                context_recall=round(cr, 4),
                rank_first=rank_first,
                bucket=bucket,
                s_q=s_q,
                passed=passed,
            )
        )

        db = _diff(r.difficulty)
        db["total"] += 1
        db["ndcg"].append(ndcg_k)
        db["rec"].append(recall_k)
        db["prec"].append(prec_k)
        if has_gold_case and s_q is not None:
            metrics.gold_cases += 1
            w = p.weight_for(r.difficulty)
            w_sum_total += w
            ws_q_total += w * s_q
            w_pass_total += w * (1.0 if passed else 0.0)
            db["w_sum"] += w
            db["ws_q"] += w * s_q
            db["w_pass"] += w * (1.0 if passed else 0.0)

    metrics.hit_rate = {k: _avg(hit[k]) for k in ks}
    metrics.recall = {k: _avg(rec[k]) for k in ks}
    metrics.mrr = {k: _avg(mrr[k]) for k in ks}
    metrics.ndcg = {k: _avg(ndcg[k]) for k in ks}
    metrics.context_precision = {k: _avg(prec[k]) for k in ks}
    metrics.context_recall = _avg(ctx_recall)

    # order by_type by the canonical taxonomy order
    order = {t.value: i for i, t in enumerate(QuestionType.all())}
    for t in sorted(type_acc, key=lambda x: order.get(x, 99)):
        tb = type_acc[t]
        metrics.by_type.append(
            RetrievalTypeMetrics(
                question_type=t,
                total=tb["total"],
                hit_rate={k: _avg(tb["hit"][k]) for k in ks},
                recall={k: _avg(tb["rec"][k]) for k in ks},
                mrr={k: _avg(tb["mrr"][k]) for k in ks},
                ndcg={k: _avg(tb["ndcg"][k]) for k in ks},
                context_precision={k: _avg(tb["prec"][k]) for k in ks},
                context_recall=_avg(tb["ctx"]),
            )
        )

    # --- KB 级综合分 / 通过率（难度加权，只在带黄金的用例上）---
    metrics.buckets = bucket_counts
    if w_sum_total > 0:
        metrics.kb_score = round(ws_q_total / w_sum_total, 4)
        metrics.pass_rate = round(w_pass_total / w_sum_total, 4)

    diff_order = {"easy": 0, "normal": 1, "medium": 1, "hard": 2}
    for d in sorted(diff_acc, key=lambda x: diff_order.get(x, 9)):
        db = diff_acc[d]
        metrics.by_difficulty.append(
            RetrievalDifficultyMetrics(
                difficulty=d,
                total=db["total"],
                ndcg=_avg(db["ndcg"]),
                recall=_avg(db["rec"]),
                context_precision=_avg(db["prec"]),
                kb_score=round(db["ws_q"] / db["w_sum"], 4) if db["w_sum"] > 0 else None,
                pass_rate=round(db["w_pass"] / db["w_sum"], 4) if db["w_sum"] > 0 else None,
            )
        )

    return metrics
