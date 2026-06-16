"""L4 业务价值层：对照组增量（uplift）指标。

把「闭卷裸答」(baseline A) 与「用知识库」(treatment C) 两路的评测结果按 case_id
对齐后逐题相减，得出知识库的**增量价值**——回答「这库到底值不值得建」。

纯函数（不碰 I/O）：输入两个已判完的 ``EvalRun`` + ``TestSuite``（取难度/类型），
输出 headline 四件套（净增量/胜率/负增量率/不可替代率）、五个诊断桶、按题型/难度
分层。指标定义见 ``docs/architecture/2026-06-03-retrieval-scoring-system-design.md`` §15。

注意：增量必须**逐题配对相减**再聚合，不能用两路的均值直接相减——否则丢失配对
信息、算不出诊断桶。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from ..shared.models import EvalRun, QuestionType, TestSuite

# 诊断桶（C vs A），互斥，按本顺序优先级判定。
BUCKET_REGRESSION = "regression"  # 帮倒忙：sc < sa - delta
BUCKET_EXCLUSIVE = "exclusive"  # 不可替代：sa<tau_low 且 sc>=tau_high（库的护城河）
BUCKET_BOTH_FAIL = "both_fail"  # 双输：两路都 < tau_low（根因常在 L0/L1）
BUCKET_BOOST = "boost"  # 锦上添花：sc > sa + delta
BUCKET_TIE = "tie"  # 平局：差距在 ±delta 内（含「通用知识两路都高」）

# 难度权重：hard 题更能拉开知识库差距，权重更高。
_DIFFICULTY_WEIGHT = {"easy": 1.0, "normal": 1.5, "medium": 1.5, "hard": 2.0}


def _difficulty_weight(difficulty: str | None) -> float:
    return _DIFFICULTY_WEIGHT.get((difficulty or "normal").strip().lower(), 1.0)


@dataclass
class UpliftCaseResult:
    """单题对照：两路得分 + 增量 + 诊断桶。"""

    case_id: str
    question_type: str
    difficulty: str
    score_baseline: float
    score_treatment: float
    uplift: float  # score_treatment - score_baseline
    bucket: str


@dataclass
class UpliftBreakdown:
    """某个维度（题型/难度）的分层增量。"""

    key: str
    count: int
    mean_uplift: float


@dataclass
class UpliftReport:
    suite_id: str
    baseline_run_id: str = ""
    treatment_run_id: str = ""
    baseline_agent: str = ""
    treatment_agent: str = ""
    n: int = 0  # 对齐到两路都有的题数
    net_uplift: float = 0.0  # 难度加权净增量
    win_rate: float = 0.0
    regression_rate: float = 0.0
    exclusivity: float = 0.0
    value_pass: bool = False
    bucket_counts: dict[str, int] = field(default_factory=dict)
    by_type: list[UpliftBreakdown] = field(default_factory=list)
    by_difficulty: list[UpliftBreakdown] = field(default_factory=list)
    cases: list[UpliftCaseResult] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def to_metrics_dict(self) -> dict:
        """拍扁成 RunSummary.metrics 用的 dict。"""

        return {
            "baseline_run_id": self.baseline_run_id,
            "treatment_run_id": self.treatment_run_id,
            "baseline_agent": self.baseline_agent,
            "treatment_agent": self.treatment_agent,
            "n": self.n,
            "value_pass": self.value_pass,
            "headline": {
                "net_uplift": self.net_uplift,
                "win_rate": self.win_rate,
                "regression_rate": self.regression_rate,
                "exclusivity": self.exclusivity,
            },
            "bucket_counts": self.bucket_counts,
            "by_type": [b.__dict__ for b in self.by_type],
            "by_difficulty": [b.__dict__ for b in self.by_difficulty],
            "cases": [c.__dict__ for c in self.cases],
            "params": self.params,
        }


def _classify(sa: float, sc: float, *, tau_low: float, tau_high: float, delta: float) -> str:
    if sc < sa - delta:
        return BUCKET_REGRESSION
    if sa < tau_low and sc >= tau_high:
        return BUCKET_EXCLUSIVE
    if sa < tau_low and sc < tau_low:
        return BUCKET_BOTH_FAIL
    if sc > sa + delta:
        return BUCKET_BOOST
    return BUCKET_TIE


def _breakdowns(cases: list[UpliftCaseResult], key: str) -> list[UpliftBreakdown]:
    groups: dict[str, list[float]] = {}
    for c in cases:
        groups.setdefault(getattr(c, key), []).append(c.uplift)
    return [
        UpliftBreakdown(key=k, count=len(v), mean_uplift=round(mean(v), 4))
        for k, v in groups.items()
    ]


def compute_uplift(
    baseline: EvalRun,
    treatment: EvalRun,
    suite: TestSuite | None = None,
    *,
    tau_low: float = 0.4,
    tau_high: float = 0.6,
    delta: float = 0.1,
    theta_uplift: float = 0.1,
    max_regression: float = 0.05,
) -> UpliftReport:
    """算 treatment(C 用库) 相对 baseline(A 闭卷) 的增量价值。"""

    base_scores = {r.case_id: r.score for r in baseline.results}
    treat_by_id = {r.case_id: r for r in treatment.results}

    def _difficulty(case_id: str) -> str:
        if suite is not None:
            tc = suite.by_id(case_id)
            if tc is not None:
                return tc.difficulty or "normal"
        return "normal"

    cases: list[UpliftCaseResult] = []
    for cid, treat in treat_by_id.items():
        if cid not in base_scores:
            continue  # 只评两路都有的题
        sa = base_scores[cid]
        sc = treat.score
        cases.append(
            UpliftCaseResult(
                case_id=cid,
                question_type=treat.question_type.value
                if isinstance(treat.question_type, QuestionType)
                else str(treat.question_type),
                difficulty=_difficulty(cid),
                score_baseline=round(sa, 4),
                score_treatment=round(sc, 4),
                uplift=round(sc - sa, 4),
                bucket=_classify(sa, sc, tau_low=tau_low, tau_high=tau_high, delta=delta),
            )
        )

    report = UpliftReport(
        suite_id=treatment.suite_id,
        baseline_run_id=baseline.run_id,
        treatment_run_id=treatment.run_id,
        baseline_agent=baseline.agent_name,
        treatment_agent=treatment.agent_name,
        n=len(cases),
        cases=cases,
        params={
            "tau_low": tau_low,
            "tau_high": tau_high,
            "delta": delta,
            "theta_uplift": theta_uplift,
            "max_regression": max_regression,
        },
    )
    if not cases:
        return report

    n = len(cases)
    report.win_rate = round(sum(1 for c in cases if c.uplift > delta) / n, 4)
    report.regression_rate = round(
        sum(1 for c in cases if c.bucket == BUCKET_REGRESSION) / n, 4
    )
    report.exclusivity = round(
        sum(1 for c in cases if c.bucket == BUCKET_EXCLUSIVE) / n, 4
    )

    weighted_sum = sum(_difficulty_weight(c.difficulty) * c.uplift for c in cases)
    weight_total = sum(_difficulty_weight(c.difficulty) for c in cases)
    report.net_uplift = round(weighted_sum / weight_total, 4) if weight_total else 0.0

    report.bucket_counts = {
        b: sum(1 for c in cases if c.bucket == b)
        for b in (
            BUCKET_EXCLUSIVE,
            BUCKET_BOOST,
            BUCKET_TIE,
            BUCKET_REGRESSION,
            BUCKET_BOTH_FAIL,
        )
    }
    report.by_type = _breakdowns(cases, "question_type")
    report.by_difficulty = _breakdowns(cases, "difficulty")
    report.value_pass = (
        report.net_uplift >= theta_uplift and report.regression_rate <= max_regression
    )
    return report
