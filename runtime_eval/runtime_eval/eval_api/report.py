"""Render an EvalRun + metrics into Markdown and a standalone HTML report."""

from __future__ import annotations

import html
from pathlib import Path

from ..shared.models import EvalRun, RetrievalRun, TestSuite
from .metrics import ReportMetrics, compute_metrics
from .retrieval_metrics import (
    RetrievalReportMetrics,
    ScoringParams,
    compute_retrieval_metrics,
)

_BUCKET_LABELS = {
    "empty": "空召回",
    "miss": "错召回",
    "buried": "低排序",
    "noisy": "高噪声",
    "unanswerable": "不可答",
    "healthy": "健康",
}
_BUCKET_ORDER = ["empty", "miss", "buried", "noisy", "unanswerable", "healthy"]


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_ms(value: float | None) -> str:
    return f"{value:.0f} ms" if value is not None else "—"


def render_markdown(run: EvalRun, suite: TestSuite) -> str:
    m = compute_metrics(run, suite)
    lines: list[str] = []
    lines.append(f"# 运行态测试报告 · {m.suite_id}")
    lines.append("")
    lines.append(f"- 被测 Agent：`{m.agent_name}`")
    lines.append(f"- 评测后端：`{m.backend}`")
    lines.append(f"- 通过阈值：score ≥ {m.pass_threshold}")
    lines.append(f"- 用例总数：{m.total_cases}（已答 {m.answered_cases}）")
    lines.append(f"- **总体准确率：{_pct(m.overall_accuracy)}**（平均分 {m.overall_avg_score}）")
    lines.append("")

    lines.append("## 按问题类型准确率")
    lines.append("")
    lines.append("| 类型 | 用例数 | 准确率 | 平均分 | 正确 | 部分 | 错误 | 未答 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for b in m.by_type:
        lines.append(
            f"| {b.question_type} | {b.total} | {_pct(b.accuracy)} | {b.avg_score} | "
            f"{b.correct} | {b.partial} | {b.incorrect} | {b.missing} |"
        )
    lines.append("")

    lines.append("## 查询时长")
    lines.append("")
    lines.append(f"- 样本数：{m.latency.count}")
    lines.append(f"- 均值：{_fmt_ms(m.latency.mean_ms)}")
    lines.append(f"- P50：{_fmt_ms(m.latency.p50_ms)} ・ P95：{_fmt_ms(m.latency.p95_ms)} ・ 最大：{_fmt_ms(m.latency.max_ms)}")
    lines.append("")

    lines.append("## Token 消耗")
    lines.append("")
    lines.append(f"- 被测 Agent 合计：{m.agent_tokens.total_tokens}（均值 {m.agent_tokens_avg}/题）")
    lines.append(f"- 框架·出题：{m.framework_generation_tokens.total_tokens}")
    lines.append(f"- 框架·裁判：{m.framework_judge_tokens.total_tokens}")
    lines.append("")

    lines.append("## 用例明细")
    lines.append("")
    for r in run.results:
        case = suite.by_id(r.case_id)
        q = case.question if case else r.case_id
        lines.append(f"### [{r.verdict.value} · {r.score:.2f}] {q}")
        lines.append(f"- 类型：{r.question_type.value} ・ 时长：{_fmt_ms(r.latency_ms)} ・ Agent token：{r.agent_token_usage.total_tokens}")
        if case:
            lines.append(f"- 期望答案：{case.expected_answer}")
        if r.missed_points:
            lines.append(f"- 缺失要点：{', '.join(r.missed_points)}")
        if r.rationale:
            lines.append(f"- 评语：{r.rationale}")
        lines.append("")

    return "\n".join(lines)


def _bar(value: float) -> str:
    pct = max(0.0, min(1.0, value)) * 100
    hue = int(pct * 1.2)  # 0=red -> 120=green
    return (
        f'<div class="bar"><span style="width:{pct:.1f}%;'
        f'background:hsl({hue},70%,45%)"></span><em>{pct:.1f}%</em></div>'
    )


def render_html(run: EvalRun, suite: TestSuite) -> str:
    m = compute_metrics(run, suite)
    rows = "\n".join(
        f"<tr><td>{b.question_type}</td><td>{b.total}</td><td>{_bar(b.accuracy)}</td>"
        f"<td>{b.avg_score}</td><td>{b.correct}</td><td>{b.partial}</td>"
        f"<td>{b.incorrect}</td><td>{b.missing}</td></tr>"
        for b in m.by_type
    )

    verdict_color = {
        "correct": "#1a7f37",
        "partial": "#9a6700",
        "incorrect": "#cf222e",
        "missing": "#57606a",
    }
    detail = []
    for r in run.results:
        case = suite.by_id(r.case_id)
        q = html.escape(case.question if case else r.case_id)
        exp = html.escape(case.expected_answer if case else "")
        color = verdict_color.get(r.verdict.value, "#57606a")
        detail.append(
            f'<details class="case"><summary>'
            f'<b style="color:{color}">{r.verdict.value} · {r.score:.2f}</b> '
            f'<span class="tag">{r.question_type.value}</span> {q}</summary>'
            f'<div class="meta">时长 {_fmt_ms(r.latency_ms)} · Agent token {r.agent_token_usage.total_tokens}</div>'
            f'<p><b>期望答案：</b>{exp}</p>'
            f'<p><b>缺失要点：</b>{html.escape(", ".join(r.missed_points)) or "—"}</p>'
            f'<p><b>评语：</b>{html.escape(r.rationale)}</p>'
            f"</details>"
        )

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>运行态测试报告 · {html.escape(m.suite_id)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
 .wrap{{max-width:960px;margin:0 auto;padding:24px}}
 h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:6px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap}}
 .card{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 18px;flex:1;min-width:150px}}
 .card .n{{font-size:26px;font-weight:700}} .card .l{{color:#57606a;font-size:13px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:8px;overflow:hidden}}
 th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #eaeef2;font-size:13px}}
 th{{background:#f6f8fa}}
 .bar{{position:relative;background:#eaeef2;border-radius:5px;height:18px;min-width:120px}}
 .bar span{{position:absolute;left:0;top:0;bottom:0;border-radius:5px}}
 .bar em{{position:relative;font-style:normal;font-size:11px;padding-left:6px;line-height:18px;color:#1f2328}}
 .tag{{background:#ddf4ff;color:#0969da;border-radius:6px;padding:1px 7px;font-size:11px;margin-right:6px}}
 details.case{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:10px 14px;margin:8px 0}}
 details.case summary{{cursor:pointer}} .meta{{color:#57606a;font-size:12px;margin:6px 0}}
</style></head><body><div class="wrap">
<h1>运行态测试报告 · {html.escape(m.suite_id)}</h1>
<div class="meta">被测 Agent <code>{html.escape(m.agent_name)}</code> · 评测后端 <code>{html.escape(m.backend)}</code> · 通过阈值 score ≥ {m.pass_threshold}</div>
<div class="cards">
 <div class="card"><div class="n">{_pct(m.overall_accuracy)}</div><div class="l">总体准确率</div></div>
 <div class="card"><div class="n">{m.overall_avg_score}</div><div class="l">平均分</div></div>
 <div class="card"><div class="n">{m.total_cases}</div><div class="l">用例数（已答 {m.answered_cases}）</div></div>
 <div class="card"><div class="n">{_fmt_ms(m.latency.mean_ms)}</div><div class="l">平均查询时长（P95 {_fmt_ms(m.latency.p95_ms)}）</div></div>
 <div class="card"><div class="n">{m.agent_tokens.total_tokens}</div><div class="l">Agent token（均值 {m.agent_tokens_avg}/题）</div></div>
</div>
<h2>按问题类型准确率</h2>
<table><thead><tr><th>类型</th><th>用例数</th><th>准确率</th><th>平均分</th><th>正确</th><th>部分</th><th>错误</th><th>未答</th></tr></thead>
<tbody>{rows}</tbody></table>
<h2>用例明细</h2>
{''.join(detail)}
<h2>框架自身 LLM 成本</h2>
<div class="meta">出题 {m.framework_generation_tokens.total_tokens} token · 裁判 {m.framework_judge_tokens.total_tokens} token</div>
</div></body></html>"""


def write_reports(run: EvalRun, suite: TestSuite, reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"{run.run_id}.md"
    html_path = reports_dir / f"{run.run_id}.html"
    md_path.write_text(render_markdown(run, suite), encoding="utf-8")
    html_path.write_text(render_html(run, suite), encoding="utf-8")
    return {"markdown": md_path, "html": html_path}


# --- retrieval-layer (检索层) report ---------------------------------------


def _krow(label: str, values: dict[int, float], ks: list[int]) -> str:
    cells = " | ".join(_pct(values.get(k, 0.0)) for k in ks)
    return f"| {label} | {cells} |"


def render_retrieval_markdown(
    run: RetrievalRun, suite: TestSuite, scoring: ScoringParams | None = None
) -> str:
    m = compute_retrieval_metrics(run, scoring)
    ks = m.k_values
    head = " | ".join(f"@{k}" for k in ks)
    sep = " | ".join("---:" for _ in ks)
    lines: list[str] = []
    lines.append(f"# 检索层测试报告 · {m.suite_id}")
    lines.append("")
    lines.append(f"- 被测检索器：`{m.agent_name}`")
    lines.append(f"- 评测后端：`{m.backend}`")
    lines.append(f"- 用例总数：{m.total_cases}（已上传检索结果 {m.judged_cases}）")
    na = "N/A（需黄金标注，后续开发）"
    if m.has_gold:
        lines.append(f"- **Context Recall（整体覆盖率）：{_pct(m.context_recall)}**")
    else:
        lines.append(f"- **Context Recall（整体覆盖率）：{na}**")
        lines.append(
            "- 说明：本次为真实查询日志（无黄金标注），仅给出精确率族指标"
            "（HitRate / MRR / NDCG / ContextPrecision）；召回率族需 gold，暂列 N/A。"
        )
    lines.append("")
    lines.append("## 总体 IR 指标")
    lines.append("")
    lines.append(f"| 指标 | {head} |")
    lines.append(f"| --- | {sep} |")
    lines.append(_krow("HitRate", m.hit_rate, ks))
    if m.has_gold:
        lines.append(_krow("Recall", m.recall, ks))
    else:
        lines.append(f"| Recall | {' | '.join('N/A' for _ in ks)} |")
    lines.append(_krow("MRR", m.mrr, ks))
    lines.append(_krow("NDCG", m.ndcg, ks))
    lines.append(_krow("ContextPrecision", m.context_precision, ks))
    lines.append("")

    # 综合分 / 通过率（难度加权，K=score_k）
    lines.append(f"## 综合质量分（@{m.score_k}，难度加权）")
    lines.append("")
    if m.kb_score is not None:
        lines.append(f"- **KB 检索质量分：{m.kb_score:.4f}**（带黄金用例 {m.gold_cases} 道）")
        lines.append(f"- **KB 通过率：{_pct(m.pass_rate or 0.0)}**（PASS：Recall≥0.5 且 rank≤3 且 ContextRecall≥0.8）")
    else:
        lines.append(f"- KB 检索质量分 / 通过率：{na}")
        lines.append("- 综合分 S_q 含 Recall 与 ContextRecall，无黄金标注时不计算；下方诊断桶仍可用。")
    lines.append("")

    # 诊断桶分布
    total_b = sum(m.buckets.values()) or 1
    lines.append("## 失败诊断分布")
    lines.append("")
    lines.append("| 诊断桶 | 题数 | 占比 |")
    lines.append("| --- | ---: | ---: |")
    for key in _BUCKET_ORDER:
        cnt = m.buckets.get(key, 0)
        if cnt:
            lines.append(f"| {_BUCKET_LABELS[key]} | {cnt} | {_pct(cnt / total_b)} |")
    lines.append("")

    # 按难度
    if m.by_difficulty:
        lines.append(f"## 按难度（NDCG@{m.score_k} / 综合分 / 通过率）")
        lines.append("")
        lines.append("| 难度 | 题数 | NDCG | 综合分 | 通过率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for d in m.by_difficulty:
            sc = f"{d.kb_score:.4f}" if d.kb_score is not None else "N/A"
            pr = _pct(d.pass_rate) if d.pass_rate is not None else "N/A"
            lines.append(f"| {d.difficulty} | {d.total} | {_pct(d.ndcg)} | {sc} | {pr} |")
        lines.append("")

    lines.append("## 按问题类型（NDCG / Recall）")
    lines.append("")
    lines.append(f"| 类型 | 用例数 | NDCG | Recall | ContextRecall |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    top = max(ks)
    for b in m.by_type:
        rec = _pct(b.recall.get(top, 0.0)) if m.has_gold else "N/A"
        ctx = _pct(b.context_recall) if m.has_gold else "N/A"
        lines.append(
            f"| {b.question_type} | {b.total} | {_pct(b.ndcg.get(top, 0.0))} | "
            f"{rec} | {ctx} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_retrieval_html(
    run: RetrievalRun, suite: TestSuite, scoring: ScoringParams | None = None
) -> str:
    m = compute_retrieval_metrics(run, scoring)
    ks = m.k_values
    head = "".join(f"<th>@{k}</th>" for k in ks)

    def _krow(label: str, values: dict[int, float]) -> str:
        cells = "".join(f"<td>{_bar(values.get(k, 0.0))}</td>" for k in ks)
        return f"<tr><td>{label}</td>{cells}</tr>"

    def _na_row(label: str) -> str:
        cells = "".join('<td class="na">N/A</td>' for _ in ks)
        return f"<tr><td>{label}</td>{cells}</tr>"

    metric_rows = "\n".join(
        [
            _krow("HitRate", m.hit_rate),
            _krow("Recall", m.recall) if m.has_gold else _na_row("Recall"),
            _krow("MRR", m.mrr),
            _krow("NDCG", m.ndcg),
            _krow("ContextPrecision", m.context_precision),
        ]
    )
    top = max(ks)

    def _na_cell(value: float) -> str:
        return _bar(value) if m.has_gold else '<span class="na">N/A</span>'

    type_rows = "\n".join(
        f"<tr><td>{html.escape(b.question_type)}</td><td>{b.total}</td>"
        f"<td>{_bar(b.ndcg.get(top, 0.0))}</td><td>{_na_cell(b.recall.get(top, 0.0))}</td>"
        f"<td>{_na_cell(b.context_recall)}</td></tr>"
        for b in m.by_type
    )
    gold_note = (
        ""
        if m.has_gold
        else '<div class="meta">本次为真实查询日志（无黄金标注），仅精确率族有效；'
        "召回率族（Recall / ContextRecall）与综合分 S_q 需 gold，暂列 N/A。</div>"
    )

    score_card = (
        f'<div class="card"><div class="n">{m.kb_score:.4f}</div>'
        f'<div class="l">KB 综合分（@{m.score_k}，难度加权）</div></div>'
        f'<div class="card"><div class="n">{_pct(m.pass_rate or 0.0)}</div>'
        f'<div class="l">KB 通过率（黄金用例 {m.gold_cases} 道）</div></div>'
        if m.kb_score is not None
        else f'<div class="card"><div class="n na">N/A</div>'
        f'<div class="l">KB 综合分 / 通过率（需黄金标注）</div></div>'
    )

    total_b = sum(m.buckets.values()) or 1
    bucket_rows = "".join(
        f"<tr><td>{_BUCKET_LABELS[key]}</td><td>{m.buckets.get(key, 0)}</td>"
        f"<td>{_bar(m.buckets.get(key, 0) / total_b)}</td></tr>"
        for key in _BUCKET_ORDER
        if m.buckets.get(key, 0)
    )

    def _opt(value: float | None, pct: bool = False) -> str:
        if value is None:
            return '<span class="na">N/A</span>'
        return _pct(value) if pct else f"{value:.4f}"

    diff_rows = "".join(
        f"<tr><td>{html.escape(d.difficulty)}</td><td>{d.total}</td>"
        f"<td>{_bar(d.ndcg)}</td><td>{_opt(d.kb_score)}</td>"
        f"<td>{_opt(d.pass_rate, pct=True)}</td></tr>"
        for d in m.by_difficulty
    )
    diff_section = (
        f"<h2>按难度（@{m.score_k}）</h2>"
        f"<table><thead><tr><th>难度</th><th>题数</th><th>NDCG</th>"
        f"<th>综合分</th><th>通过率</th></tr></thead><tbody>{diff_rows}</tbody></table>"
        if m.by_difficulty
        else ""
    )
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>检索层测试报告 · {html.escape(m.suite_id)}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
 .wrap{{max-width:960px;margin:0 auto;padding:24px}}
 h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:6px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap}}
 .card{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:14px 18px;flex:1;min-width:150px}}
 .card .n{{font-size:26px;font-weight:700}} .card .l{{color:#57606a;font-size:13px}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d0d7de;border-radius:8px;overflow:hidden}}
 th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid #eaeef2;font-size:13px}}
 th{{background:#f6f8fa}}
 .bar{{position:relative;background:#eaeef2;border-radius:5px;height:18px;min-width:90px}}
 .bar span{{position:absolute;left:0;top:0;bottom:0;border-radius:5px}}
 .bar em{{position:relative;font-style:normal;font-size:11px;padding-left:6px;line-height:18px;color:#1f2328}}
 .meta{{color:#57606a;font-size:12px;margin:6px 0}}
 .na{{color:#9a6700;font-size:12px}}
</style></head><body><div class="wrap">
<h1>检索层测试报告 · {html.escape(m.suite_id)}</h1>
<div class="meta">被测检索器 <code>{html.escape(m.agent_name)}</code> · 评测后端 <code>{html.escape(m.backend)}</code></div>
<div class="cards">
 {score_card}
 <div class="card"><div class="n">{_pct(m.context_recall) if m.has_gold else "N/A"}</div><div class="l">Context Recall（整体覆盖率）</div></div>
 <div class="card"><div class="n">{_pct(m.ndcg.get(top, 0.0))}</div><div class="l">NDCG@{top}</div></div>
 <div class="card"><div class="n">{_pct(m.recall.get(top, 0.0)) if m.has_gold else "N/A"}</div><div class="l">Recall@{top}</div></div>
 <div class="card"><div class="n">{m.total_cases}</div><div class="l">用例数（已上传 {m.judged_cases}）</div></div>
</div>
{gold_note}
<h2>总体 IR 指标</h2>
<table><thead><tr><th>指标</th>{head}</tr></thead>
<tbody>{metric_rows}</tbody></table>
<h2>失败诊断分布</h2>
<table><thead><tr><th>诊断桶</th><th>题数</th><th>占比</th></tr></thead>
<tbody>{bucket_rows}</tbody></table>
{diff_section}
<h2>按问题类型（@{top}）</h2>
<table><thead><tr><th>类型</th><th>用例数</th><th>NDCG</th><th>Recall</th><th>ContextRecall</th></tr></thead>
<tbody>{type_rows}</tbody></table>
</div></body></html>"""


def write_retrieval_reports(
    run: RetrievalRun,
    suite: TestSuite,
    reports_dir: Path,
    scoring: ScoringParams | None = None,
) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / f"{run.run_id}.md"
    html_path = reports_dir / f"{run.run_id}.html"
    md_path.write_text(render_retrieval_markdown(run, suite, scoring), encoding="utf-8")
    html_path.write_text(render_retrieval_html(run, suite, scoring), encoding="utf-8")
    return {"markdown": md_path, "html": html_path}


# --- L4 业务价值层（对照组增量 uplift）report -----------------------------

_UPLIFT_BUCKET_LABELS = {
    "exclusive": "不可替代",
    "boost": "锦上添花",
    "tie": "平局",
    "regression": "帮倒忙",
    "both_fail": "双输",
}
_UPLIFT_BUCKET_ORDER = ["exclusive", "boost", "tie", "regression", "both_fail"]


def render_uplift_markdown(metrics: dict, *, suite_id: str = "") -> str:
    """把 L4 增量价值快照（``UpliftReport.to_metrics_dict()`` 的 dict）渲成 Markdown。

    渲染输入是「拍扁的 metrics dict」而非 EvalRun —— L4 结果以 RunSummary 形式落库，
    没有独立领域对象，直接读 dict 最省事，也方便前后端共用同一份数据形状。
    """

    h = metrics.get("headline", {}) or {}
    buckets = metrics.get("bucket_counts", {}) or {}
    lines: list[str] = []
    lines.append(f"# 增量价值报告（L4）· {suite_id or metrics.get('suite_id', '')}")
    lines.append("")
    lines.append(f"- 闭卷基线(A)：`{metrics.get('baseline_agent', '')}`（run `{metrics.get('baseline_run_id', '')}`）")
    lines.append(f"- 用库处理(C)：`{metrics.get('treatment_agent', '')}`（run `{metrics.get('treatment_run_id', '')}`）")
    lines.append(f"- 配对题数：{metrics.get('n', 0)}")
    verdict = "✅ 值得建" if metrics.get("value_pass") else "❌ 暂不达标"
    lines.append(f"- **价值判定：{verdict}**")
    lines.append("")

    lines.append("## Headline 四件套")
    lines.append("")
    lines.append("| 指标 | 数值 | 含义 |")
    lines.append("| --- | ---: | --- |")
    lines.append(f"| 净增量 net_uplift | {h.get('net_uplift', 0):+.4f} | 难度加权后，用库比闭卷平均多得多少分 |")
    lines.append(f"| 胜率 win_rate | {_pct(h.get('win_rate', 0))} | 多大比例的题，用库明显更好 |")
    lines.append(f"| 负增量率 regression_rate | {_pct(h.get('regression_rate', 0))} | 多大比例的题，用库反而帮倒忙 |")
    lines.append(f"| 不可替代率 exclusivity | {_pct(h.get('exclusivity', 0))} | 多大比例的题，只有用库才答得对 |")
    lines.append("")

    lines.append("## 诊断桶分布")
    lines.append("")
    lines.append("| 桶 | 题数 |")
    lines.append("| --- | ---: |")
    for b in _UPLIFT_BUCKET_ORDER:
        lines.append(f"| {_UPLIFT_BUCKET_LABELS[b]} | {buckets.get(b, 0)} |")
    lines.append("")

    by_diff = metrics.get("by_difficulty", []) or []
    if by_diff:
        lines.append("## 按难度分层增量")
        lines.append("")
        lines.append("| 难度 | 题数 | 平均增量 |")
        lines.append("| --- | ---: | ---: |")
        for d in by_diff:
            lines.append(f"| {d.get('key', '')} | {d.get('count', 0)} | {d.get('mean_uplift', 0):+.4f} |")
        lines.append("")

    by_type = metrics.get("by_type", []) or []
    if by_type:
        lines.append("## 按题型分层增量")
        lines.append("")
        lines.append("| 题型 | 题数 | 平均增量 |")
        lines.append("| --- | ---: | ---: |")
        for t in by_type:
            lines.append(f"| {t.get('key', '')} | {t.get('count', 0)} | {t.get('mean_uplift', 0):+.4f} |")
        lines.append("")

    cases = metrics.get("cases", []) or []
    if cases:
        lines.append("## 逐题对照")
        lines.append("")
        lines.append("| 用例 | 题型 | 难度 | 闭卷分 | 用库分 | 增量 | 诊断桶 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | --- |")
        for c in cases:
            lines.append(
                f"| {c.get('case_id', '')} | {c.get('question_type', '')} | {c.get('difficulty', '')} | "
                f"{c.get('score_baseline', 0):.2f} | {c.get('score_treatment', 0):.2f} | "
                f"{c.get('uplift', 0):+.2f} | {_UPLIFT_BUCKET_LABELS.get(c.get('bucket', ''), c.get('bucket', ''))} |"
            )
        lines.append("")

    return "\n".join(lines)
