"""Suite generation + judging orchestration.

Pulls documents out of the store, calls eval-llm over the injected ``LLMClient``,
assembles the TestSuite / EvalRun domain artifacts, and persists them.
Stateless beyond the store + client it is handed.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

from ..shared.models import (
    AgentResponse,
    CaseResult,
    Document,
    EvalRun,
    GoldRecord,
    QuestionType,
    ResponseSet,
    RetrievalCaseResult,
    RetrievalRun,
    RetrievalSet,
    RetrievedItem,
    RunSummary,
    SourceRef,
    TestCase,
    TestSuite,
    TokenUsage,
    Verdict,
)
from .config import ApiConfig
from .db_source import LiveCase
from .llm_client import LLMClient
from .roster import RosterEntry, select_roster
from .store import Store
from .uplift_metrics import compute_uplift


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _upsert_generated_gold(store: Store, project_id: str | None, case: TestCase) -> GoldRecord:
    """出题顺带产出黄金「草稿」并入库（按问题指纹）。

    若该问题已有人工「确认」的黄金，则复用并回填到用例（不被草稿覆盖）；否则写入
    ``status='draft'`` / ``source_kind='llm_generated'`` 的草稿，等人工在黄金集库确认。
    """

    fp = GoldRecord.make_fingerprint(case.question)
    existing = store.get_gold(fp)
    if existing is not None and existing.status == "confirmed":
        existing.apply_to_case(case)
        return existing
    gold = GoldRecord(
        fingerprint=fp,
        project_id=project_id,
        question=case.question,
        question_type=case.question_type,
        expected_answer=case.expected_answer,
        key_points=list(case.key_points),
        expected_evidence=list(case.expected_evidence),
        expected_entities=list(case.expected_entities),
        source=case.source,
        difficulty=case.difficulty,
        notes=case.notes,
        source_kind="llm_generated",
        status="draft",
    )
    if existing is not None:  # 保留首次出现时间
        gold.created_at = existing.created_at
    store.save_gold(gold)
    return gold


def generate_suite(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    *,
    project_id: str,
    document_ids: list[str],
    types: list[str] | None = None,
    per_type: int | None = None,
    persona: str | None = None,
    suite_id: str | None = None,
) -> TestSuite:
    per_type = per_type or config.per_type
    docs: list[Document] = []
    for did in document_ids:
        doc = store.get_document(project_id, did)
        if doc is not None:
            docs.append(doc)
    if not docs:
        raise ValueError("未找到任何有效文档，无法出题")

    suite = TestSuite(
        suite_id=suite_id
        or f"suite_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        project_id=project_id,
        backend="eval-llm",
        corpus_files=[d.rel_path for d in docs],
        document_ids=[d.document_id for d in docs],
    )
    total_usage = TokenUsage()
    backend_name = "eval-llm"
    for doc in docs:
        text = doc.text[: config.max_doc_chars] if config.max_doc_chars else doc.text
        result = client.generate_cases(
            document_text=text,
            doc_ref=doc.rel_path,
            types=types,
            per_type=per_type,
            persona=persona,
        )
        usage = result.get("usage") or {}
        total_usage = total_usage + TokenUsage(**usage)
        for raw in result.get("cases") or []:
            src = raw.get("source") or {}
            case = TestCase(
                id=_new_id("tc"),
                question=raw["question"],
                question_type=raw["question_type"],
                expected_answer=raw["expected_answer"],
                key_points=list(raw.get("key_points") or []),
                expected_evidence=list(raw.get("expected_evidence") or []),
                expected_entities=list(raw.get("expected_entities") or []),
                source=SourceRef(
                    doc=str(src.get("doc") or doc.rel_path),
                    section=src.get("section"),
                    quote=src.get("quote"),
                ),
                difficulty=str(raw.get("difficulty", "normal")) or "normal",
            )
            # 出题顺带产出黄金草稿（已有确认黄金则复用回填），入 gold 表
            _upsert_generated_gold(store, project_id, case)
            suite.cases.append(case)

    suite.backend = backend_name
    suite.generation_usage = total_usage
    store.save_suite(suite)
    return suite


def _judge_one(
    client: LLMClient,
    case: TestCase,
    response: AgentResponse | None,
) -> CaseResult:
    if response is None or not response.answer.strip():
        return CaseResult(
            case_id=case.id,
            question_type=case.question_type,
            verdict=Verdict.MISSING,
            score=0.0,
            rationale="未上传被测 Agent 回答。",
            latency_ms=response.latency_ms if response else None,
            agent_token_usage=response.token_usage if response else TokenUsage(),
        )

    payload = client.judge(
        question=case.question,
        question_type=case.question_type.value,
        expected_answer=case.expected_answer,
        key_points=case.key_points,
        agent_answer=response.answer,
    )
    score = max(0.0, min(1.0, float(payload.get("score", 0.0) or 0.0)))
    verdict_raw = str(payload.get("verdict", "")).strip().lower()
    try:
        verdict = Verdict(verdict_raw)
    except ValueError:
        verdict = (
            Verdict.CORRECT
            if score >= 0.8
            else Verdict.PARTIAL
            if score > 0
            else Verdict.INCORRECT
        )

    return CaseResult(
        case_id=case.id,
        question_type=case.question_type,
        verdict=verdict,
        score=score,
        rationale=str(payload.get("rationale", "")),
        covered_points=[str(p) for p in (payload.get("covered_points") or [])],
        missed_points=[str(p) for p in (payload.get("missed_points") or [])],
        latency_ms=response.latency_ms,
        agent_token_usage=response.token_usage,
        judge_token_usage=TokenUsage(**(payload.get("usage") or {})),
    )


def judge_suite(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    responses: ResponseSet,
    *,
    pass_threshold: float | None = None,
    run_id: str | None = None,
) -> EvalRun:
    threshold = pass_threshold if pass_threshold is not None else config.pass_threshold
    run = EvalRun(
        run_id=run_id or _new_id("run"),
        suite_id=suite.suite_id,
        project_id=suite.project_id,
        agent_name=responses.agent_name,
        backend="eval-llm",
        pass_threshold=threshold,
    )
    for case in suite.cases:
        run.results.append(_judge_one(client, case, responses.by_case(case.id)))
    store.save_run(run)
    return run


# --- agent 跑批：claude -p + MCP 自动检索并作答 ---------------------------


def run_agent_suite(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    *,
    agent_name: str = "claude-agent",
    system: str | None = None,
    only_missing: bool = False,
) -> dict:
    """对测试集每道题跑一次 agent，落地「回答」(L3) + 「检索片段」(L1)。

    - 回答存为 ResponseSet（之后走应用层评测「全部评估」）。
    - agent 自己检索到的片段存为 RetrievalSet（替代人工逐题「检索数据库」，
      之后走检索层「全部评估/生成报告」）。
    - ``only_missing=True``：跳过已有回答的用例（断点续跑/补漏）。

    第一题就失败通常意味着配置错误（MCP 没配好/claude 没登录），会整体抛出；
    后续单题失败则累加到 ``errors`` 里，不打断整批。
    """

    prev_responses = store.get_responses(suite.suite_id) if only_missing else None
    prev_by_case = prev_responses.by_case if prev_responses else (lambda _cid: None)
    prev_rset = store.get_retrieval_set(suite.suite_id) if only_missing else None

    rs = ResponseSet(suite_id=suite.suite_id, agent_name=agent_name)
    rset = RetrievalSet(suite_id=suite.suite_id, agent_name=agent_name)

    answered = 0
    retrieved_cases = 0
    agent_tokens = TokenUsage()
    errors: list[dict] = []

    for idx, case in enumerate(suite.cases):
        # 续跑：已有非空回答的用例直接沿用，不再调用 agent。
        if only_missing:
            existing = prev_by_case(case.id)
            if existing is not None and (existing.answer or "").strip():
                rs.responses.append(existing)
                if existing.answer.strip():
                    answered += 1
                agent_tokens = agent_tokens + existing.token_usage
                if prev_rset is not None and prev_rset.for_case(case.id):
                    rset.items[case.id] = prev_rset.for_case(case.id)
                    retrieved_cases += 1
                continue

        started = time.perf_counter()
        try:
            out = client.run_agent(question=case.question, system=system)
        except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
            if idx == 0 and not errors and answered == 0:
                # 第一题就炸 → 极可能是配置问题，整体抛出更直观。
                raise RuntimeError(f"agent 跑批失败（首题即报错）：{exc}") from exc
            errors.append({"case_id": case.id, "error": str(exc)})
            continue

        latency_ms = (time.perf_counter() - started) * 1000.0
        usage = TokenUsage(**(out.get("usage") or {}))
        agent_tokens = agent_tokens + usage
        answer = str(out.get("answer") or "")
        rs.responses.append(
            AgentResponse(
                case_id=case.id,
                answer=answer,
                latency_ms=latency_ms,
                token_usage=usage,
                raw={
                    "tool_calls": out.get("tool_calls") or [],
                    "num_turns": out.get("num_turns", 0),
                },
            )
        )
        if answer.strip():
            answered += 1

        ranked = [
            RetrievedItem(
                rank=it.get("rank") if it.get("rank") is not None else i,
                text=str(it.get("text") or ""),
                source=it.get("source"),
            )
            for i, it in enumerate(out.get("retrieved_items") or [], start=1)
            if str(it.get("text") or "").strip()
        ]
        if ranked:
            rset.items[case.id] = ranked
            retrieved_cases += 1

    store.save_responses(rs)
    store.save_retrieval_set(rset)
    return {
        "suite_id": suite.suite_id,
        "agent_name": agent_name,
        "total": len(suite.cases),
        "answered": answered,
        "retrieved_cases": retrieved_cases,
        "agent_tokens": agent_tokens.total_tokens,
        "errors": errors,
    }


def run_agent_for_case(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    case: TestCase,
    *,
    agent_name: str = "claude-agent",
    system: str | None = None,
) -> dict:
    """跑一道题的 agent，并「合并」落库（不清空其他题的已有数据）。

    供前端逐题驱动跑批使用：前端一题一题调用，从而拿到实时进度，并能在前端层
    决定「这题已检索/已回答 → 跳过」。

    - 回答合并进该 suite 的 ResponseSet（按 case_id 覆盖这一条，保留其余）。
    - agent 检索到的片段合并进 RetrievalSet（同样只动这一条）。
    - claude/MCP/后端任一出错时由 ``client.run_agent`` 抛出（RuntimeError），
      调用方（端点）转成 4xx 返回，单题失败不影响其它题。
    """

    started = time.perf_counter()
    out = client.run_agent(question=case.question, system=system)
    latency_ms = (time.perf_counter() - started) * 1000.0

    usage = TokenUsage(**(out.get("usage") or {}))
    answer = str(out.get("answer") or "")

    # 合并回答：仅覆盖本题，保留其它题
    rs = store.get_responses(suite.suite_id)
    rs.agent_name = agent_name
    rs.responses = [r for r in rs.responses if r.case_id != case.id]
    rs.responses.append(
        AgentResponse(
            case_id=case.id,
            answer=answer,
            latency_ms=latency_ms,
            token_usage=usage,
            raw={
                "tool_calls": out.get("tool_calls") or [],
                "num_turns": out.get("num_turns", 0),
            },
        )
    )
    store.save_responses(rs)

    # 合并检索片段：仅覆盖本题
    ranked = [
        RetrievedItem(
            rank=it.get("rank") if it.get("rank") is not None else i,
            text=str(it.get("text") or ""),
            source=it.get("source"),
        )
        for i, it in enumerate(out.get("retrieved_items") or [], start=1)
        if str(it.get("text") or "").strip()
    ]
    rset = store.get_retrieval_set(suite.suite_id)
    rset.agent_name = agent_name
    if ranked:
        rset.items[case.id] = ranked
    store.save_retrieval_set(rset)

    return {
        "case_id": case.id,
        "answered": bool(answer.strip()),
        "answer": answer,
        "num_turns": int(out.get("num_turns", 0) or 0),
        "tokens": usage.total_tokens,
        "tool_calls": out.get("tool_calls") or [],
        "retrieved_count": len(ranked),
        "items": [it.model_dump() for it in ranked],
    }


# --- L4 业务价值层：对照组增量（uplift）----------------------------------


def _answer_one(
    client: LLMClient,
    case: TestCase,
    *,
    route: str,
    system: str | None = None,
) -> AgentResponse:
    """跑单题单路作答，返回内存里的 AgentResponse（不落库）。"""

    started = time.perf_counter()
    out = client.run_agent(question=case.question, system=system, route=route)
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = TokenUsage(**(out.get("usage") or {}))
    return AgentResponse(
        case_id=case.id,
        answer=str(out.get("answer") or ""),
        latency_ms=latency_ms,
        token_usage=usage,
        raw={
            "route": route,
            "tool_calls": out.get("tool_calls") or [],
            "num_turns": out.get("num_turns", 0),
        },
    )


def _collect_route(
    client: LLMClient,
    suite: TestSuite,
    *,
    agent_name: str,
    route: str,
    system: str | None = None,
) -> tuple[ResponseSet, TokenUsage, list[dict]]:
    """对整套题跑一条评测路（kb/closed_book/web），只在内存里攒回答。

    不落库（不碰共享的 ResponseSet/RetrievalSet），避免 A、C 两路互相覆盖，也不污染
    L1/L3 用的那份回答。首题即失败按配置错误整体抛出，后续单题失败累加到 errors。
    """

    rs = ResponseSet(suite_id=suite.suite_id, agent_name=agent_name)
    agent_tokens = TokenUsage()
    errors: list[dict] = []
    for idx, case in enumerate(suite.cases):
        try:
            resp = _answer_one(client, case, route=route, system=system)
        except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
            if idx == 0 and not errors:
                raise RuntimeError(
                    f"{route} 路跑批失败（首题即报错）：{exc}"
                ) from exc
            errors.append({"case_id": case.id, "error": str(exc)})
            continue
        agent_tokens = agent_tokens + resp.token_usage
        rs.responses.append(resp)
    return rs, agent_tokens, errors


def iter_value_uplift(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    *,
    baseline_agent: str = "closed-book",
    treatment_agent: str = "kb-agent",
    baseline_system: str | None = None,
    treatment_system: str | None = None,
    tau_low: float = 0.4,
    tau_high: float = 0.6,
    delta: float = 0.1,
    theta_uplift: float = 0.1,
    max_regression: float = 0.05,
    pass_threshold: float | None = None,
) -> Iterator[dict]:
    """L4 增量价值（流式）：闭卷裸答(A) vs 用知识库(C)，逐题配对算增量。

    四个阶段顺序跑（闭卷·作答 → 闭卷·判分 → 用库·作答 → 用库·判分），每题前后各 yield
    一个进度事件供前端画进度条；两路各自判分成稳定 id 的 EvalRun（``run_cb_<sid>`` /
    ``run_kb_<sid>``）→ ``compute_uplift`` → 落 ``RunSummary(layer='value', kind='uplift')``。

    事件 ``event`` 取值：``start`` / ``case_start`` / ``case_done`` / ``case_error`` /
    ``done``；``case_*`` 事件带 ``stage``（如 ``closed_book_answer``）、``label``（如
    「闭卷·作答」）、``route`` / ``i`` / ``n`` / ``case_id`` / ``question``。
    """

    sid = suite.suite_id
    cases = suite.cases
    n = len(cases)
    threshold = pass_threshold if pass_threshold is not None else config.pass_threshold
    yield {"event": "start", "total": n, "stages": 4}

    routes = [
        ("closed_book", baseline_agent, baseline_system, "闭卷", f"run_cb_{sid}"),
        ("kb", treatment_agent, treatment_system, "用库", f"run_kb_{sid}"),
    ]
    runs: dict[str, EvalRun] = {}
    tokens: dict[str, TokenUsage] = {}
    errs: dict[str, list[dict]] = {}

    for route, agent_name, system, route_label, run_id in routes:
        # —— 作答阶段 ——
        rs = ResponseSet(suite_id=sid, agent_name=agent_name)
        agent_tokens = TokenUsage()
        errors: list[dict] = []
        for i, case in enumerate(cases):
            yield {
                "event": "case_start", "stage": f"{route}_answer",
                "label": f"{route_label}·作答", "route": route,
                "i": i, "n": n, "case_id": case.id, "question": case.question,
            }
            try:
                resp = _answer_one(client, case, route=route, system=system)
            except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
                if i == 0 and not errors:
                    raise RuntimeError(f"{route} 路跑批失败（首题即报错）：{exc}") from exc
                errors.append({"case_id": case.id, "error": str(exc)})
                yield {
                    "event": "case_error", "stage": f"{route}_answer",
                    "case_id": case.id, "error": str(exc),
                }
                continue
            agent_tokens = agent_tokens + resp.token_usage
            rs.responses.append(resp)
            yield {
                "event": "case_done", "stage": f"{route}_answer", "route": route,
                "i": i, "n": n, "case_id": case.id,
            }

        # —— 判分阶段 ——
        run = EvalRun(
            run_id=run_id, suite_id=sid, project_id=suite.project_id,
            agent_name=agent_name, backend="eval-llm", pass_threshold=threshold,
        )
        for i, case in enumerate(cases):
            yield {
                "event": "case_start", "stage": f"{route}_judge",
                "label": f"{route_label}·判分", "route": route,
                "i": i, "n": n, "case_id": case.id, "question": case.question,
            }
            run.results.append(_judge_one(client, case, rs.by_case(case.id)))
            yield {
                "event": "case_done", "stage": f"{route}_judge", "route": route,
                "i": i, "n": n, "case_id": case.id,
            }
        store.save_run(run)
        runs[route] = run
        tokens[route] = agent_tokens
        errs[route] = errors

    report = compute_uplift(
        runs["closed_book"], runs["kb"], suite,
        tau_low=tau_low, tau_high=tau_high, delta=delta,
        theta_uplift=theta_uplift, max_regression=max_regression,
    )
    metrics = report.to_metrics_dict()
    metrics["agent_tokens"] = {
        "baseline": tokens["closed_book"].total_tokens,
        "treatment": tokens["kb"].total_tokens,
    }
    metrics["errors"] = {"baseline": errs["closed_book"], "treatment": errs["kb"]}

    summary = RunSummary(
        run_id=f"uplift_{sid}",
        project_id=suite.project_id,
        suite_id=sid,
        layer="value",
        kind="uplift",
        metrics=metrics,
    )
    store.save_run_summary(summary)
    yield {"event": "done", "metrics": metrics}


def run_value_uplift(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    *,
    baseline_agent: str = "closed-book",
    treatment_agent: str = "kb-agent",
    baseline_system: str | None = None,
    treatment_system: str | None = None,
    tau_low: float = 0.4,
    tau_high: float = 0.6,
    delta: float = 0.1,
    theta_uplift: float = 0.1,
    max_regression: float = 0.05,
    pass_threshold: float | None = None,
) -> RunSummary:
    """L4 增量价值（一次性阻塞版）：跑完 :func:`iter_value_uplift` 取回落库的快照。"""

    for _ in iter_value_uplift(
        store, client, config, suite,
        baseline_agent=baseline_agent, treatment_agent=treatment_agent,
        baseline_system=baseline_system, treatment_system=treatment_system,
        tau_low=tau_low, tau_high=tau_high, delta=delta,
        theta_uplift=theta_uplift, max_regression=max_regression,
        pass_threshold=pass_threshold,
    ):
        pass
    return store.get_run_summary(f"uplift_{suite.suite_id}")


# --- L2 答案对照层：多模型 × 黄金答案 ------------------------------------


def _fingerprint(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


def _evidence_pack(retrieval: RetrievalSet | None, case_id: str) -> list[str]:
    """从 RetrievalSet 取某题的证据片段文本（按排名），作为 L2 焊死的作答输入。"""

    if retrieval is None:
        return []
    items = retrieval.for_case(case_id) or []
    return [str(it.text) for it in items if str(it.text).strip()]


def _answer_match_gate(suite: TestSuite, retrieval: RetrievalSet | None) -> None:
    """门禁：每题都得有证据包，缺的直接报错（不静默跳过）。

    L2 焊死证据当输入，没跑过 L1 检索的题没料可喂。缺证据的题列出来，提示先去补检索。
    """

    missing = [
        case.id for case in suite.cases if not _evidence_pack(retrieval, case.id)
    ]
    if missing:
        head = "、".join(missing[:5])
        more = f" 等 {len(missing)} 题" if len(missing) > 5 else ""
        raise RuntimeError(
            f"以下题目缺证据包（还没跑过检索）：{head}{more}。请先去补检索，再跑答案对照。"
        )


def _prior_answer_match_cache(summary: RunSummary | None) -> dict[tuple[str, str, str], dict]:
    """把上一份 ans_<sid> 快照拆成 (模型id, 证据指纹, 问题指纹) -> 逐题明细 的缓存表。

    三者都没变就直接复用旧明细，不重跑该(模型,题)。证据或题目一变 → 键不命中需重跑。
    """

    cache: dict[tuple[str, str, str], dict] = {}
    if summary is None:
        return cache
    for model in summary.metrics.get("models", []) or []:
        mid = str(model.get("id", ""))
        for detail in model.get("cases", []) or []:
            ev_fp = str(detail.get("evidence_fp", ""))
            q_fp = str(detail.get("question_fp", ""))
            if mid and ev_fp and q_fp:
                cache[(mid, ev_fp, q_fp)] = detail
    return cache


def iter_answer_match(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    *,
    roster: list[RosterEntry],
    selected_ids: list[str] | None = None,
    use_cache: bool = True,
) -> Iterator[dict]:
    """L2 答案对照（流式）：焊死证据包，花名册里各模型各写一版答案，固定裁判逐题对照黄金。

    每个模型一段「作答 → 对照判」，逐题 yield 进度事件；跑完聚合排行榜（综合分 F1 排序）
    落 ``RunSummary(layer='answer_match', kind='multi_model', run_id='ans_<sid>')``。

    事件 ``event``：``start`` / ``case_start`` / ``case_done`` / ``case_error`` / ``done``；
    ``case_*`` 带 ``stage``（如 ``<id>_answer``）、``label``、``model_id`` / ``i`` / ``n``。
    """

    sid = suite.suite_id
    cases = suite.cases
    n = len(cases)

    retrieval = store.get_retrieval_set(sid)
    _answer_match_gate(suite, retrieval)  # 门禁：缺证据直接抛

    entries = select_roster(roster, selected_ids)
    if not entries:
        raise RuntimeError("没有可参赛的模型（花名册为空或选中的 id 都不存在）")

    prior = store.get_run_summary(f"ans_{sid}") if use_cache else None
    cache = _prior_answer_match_cache(prior) if use_cache else {}

    yield {"event": "start", "total": n, "models": [e.id for e in entries], "stages": len(entries)}

    models_out: list[dict] = []
    for entry in entries:
        started = time.perf_counter()
        tokens = TokenUsage()
        hard_errors = 0
        cov_sum = 0.0
        prec_sum = 0.0
        f1_sum = 0.0
        scored = 0
        case_details: list[dict] = []
        errors: list[dict] = []

        for i, case in enumerate(cases):
            evidence = _evidence_pack(retrieval, case.id)
            ev_fp = _fingerprint("".join(evidence))
            q_fp = _fingerprint(case.question)
            ckey = (entry.id, ev_fp, q_fp)

            # —— 缓存命中：直接复用旧明细，不重跑该(模型,题) ——
            if use_cache and ckey in cache:
                detail = dict(cache[ckey])
                yield {
                    "event": "case_done", "stage": f"{entry.id}_cached",
                    "model_id": entry.id, "i": i, "n": n, "case_id": case.id,
                    "cached": True,
                }
                case_details.append(detail)
                cov_sum += float(detail.get("coverage", 0.0))
                prec_sum += float(detail.get("precision", 0.0))
                f1_sum += float(detail.get("f1", 0.0))
                scored += 1
                if detail.get("has_hard_error"):
                    hard_errors += 1
                continue

            # —— 作答 ——
            yield {
                "event": "case_start", "stage": f"{entry.id}_answer",
                "label": f"{entry.label}·作答", "model_id": entry.id,
                "i": i, "n": n, "case_id": case.id, "question": case.question,
            }
            try:
                ans = client.answer(
                    question=case.question, evidence=evidence,
                    channel=entry.channel, model=entry.model,
                )
            except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
                if i == 0 and not errors:
                    raise RuntimeError(
                        f"模型 {entry.label} 作答失败（首题即报错）：{exc}"
                    ) from exc
                errors.append({"case_id": case.id, "stage": "answer", "error": str(exc)})
                yield {
                    "event": "case_error", "stage": f"{entry.id}_answer",
                    "model_id": entry.id, "case_id": case.id, "error": str(exc),
                }
                continue
            answer_text = str(ans.get("answer") or "")
            tokens = tokens + TokenUsage(**(ans.get("usage") or {}))

            # —— 对照判 ——
            yield {
                "event": "case_start", "stage": f"{entry.id}_judge",
                "label": f"{entry.label}·对照判", "model_id": entry.id,
                "i": i, "n": n, "case_id": case.id, "question": case.question,
            }
            try:
                verdict = client.judge_answer_match(
                    question=case.question,
                    expected_answer=case.expected_answer,
                    key_points=list(case.key_points),
                    answer=answer_text,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"case_id": case.id, "stage": "judge", "error": str(exc)})
                yield {
                    "event": "case_error", "stage": f"{entry.id}_judge",
                    "model_id": entry.id, "case_id": case.id, "error": str(exc),
                }
                continue
            tokens = tokens + TokenUsage(**(verdict.get("usage") or {}))

            detail = {
                "case_id": case.id,
                "question": case.question,
                "answer": answer_text,
                "coverage": float(verdict.get("coverage", 0.0)),
                "precision": float(verdict.get("precision", 0.0)),
                "f1": float(verdict.get("f1", 0.0)),
                "covered_points": verdict.get("covered_points", []),
                "missed_points": verdict.get("missed_points", []),
                "correct_claims": verdict.get("correct_claims", []),
                "extra_claims": verdict.get("extra_claims", []),
                "contradictions": verdict.get("contradictions", []),
                "has_hard_error": bool(verdict.get("has_hard_error")),
                "evidence_fp": ev_fp,
                "question_fp": q_fp,
            }
            case_details.append(detail)
            cov_sum += detail["coverage"]
            prec_sum += detail["precision"]
            f1_sum += detail["f1"]
            scored += 1
            if detail["has_hard_error"]:
                hard_errors += 1
            yield {
                "event": "case_done", "stage": f"{entry.id}_judge",
                "model_id": entry.id, "i": i, "n": n, "case_id": case.id,
            }

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        denom = scored or 1
        models_out.append({
            "id": entry.id,
            "label": entry.label,
            "channel": entry.channel,
            "model": entry.model,
            "coverage": round(cov_sum / denom, 4),
            "precision": round(prec_sum / denom, 4),
            "f1": round(f1_sum / denom, 4),
            "hard_errors": hard_errors,
            "scored": scored,
            "tokens": tokens.total_tokens,
            "elapsed_ms": round(elapsed_ms, 1),
            "cases": case_details,
            "errors": errors,
        })

    # 排行榜：综合分 F1 降序（并列再按覆盖度），排名稳定。
    models_out.sort(key=lambda m: (m["f1"], m["coverage"]), reverse=True)
    metrics = {
        "models": models_out,
        "roster": [
            {"id": e.id, "label": e.label, "channel": e.channel, "model": e.model}
            for e in entries
        ],
    }
    summary = RunSummary(
        run_id=f"ans_{sid}",
        project_id=suite.project_id,
        suite_id=sid,
        layer="answer_match",
        kind="multi_model",
        metrics=metrics,
    )
    store.save_run_summary(summary)
    yield {"event": "done", "metrics": metrics}


def run_answer_match(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    *,
    roster: list[RosterEntry],
    selected_ids: list[str] | None = None,
    use_cache: bool = True,
) -> RunSummary:
    """L2 答案对照（一次性阻塞版）：跑完 :func:`iter_answer_match` 取回落库的快照。"""

    for _ in iter_answer_match(
        store, client, config, suite,
        roster=roster, selected_ids=selected_ids, use_cache=use_cache,
    ):
        pass
    return store.get_run_summary(f"ans_{suite.suite_id}")


# --- retrieval-layer (检索层) orchestration -------------------------------


def gold_facts(case: TestCase) -> list[str]:
    """The gold evidence nuggets a correct answer must be grounded on.

    Prefers the structured ``expected_evidence`` labels; falls back to
    ``key_points`` so generated suites (which only carry key_points) are also
    evaluable. ``expected_entities`` are appended as additional must-cover facts.
    """

    facts: list[str] = []
    for src in (case.expected_evidence, case.key_points if not case.expected_evidence else []):
        for x in src:
            s = str(x).strip()
            if s and s not in facts:
                facts.append(s)
    for x in case.expected_entities:
        s = str(x).strip()
        if s and s not in facts:
            facts.append(s)
    return facts


def confirmed_gold_facts(store: Store, case: TestCase) -> list[str]:
    """黄金库已轻量退役：检索层评分直接用用例自带事实，不再卡「已确认」门禁。

    保留函数名与签名以避免大面积改调用点；黄金表/接口仍在但不参与打分。
    """

    return gold_facts(case)


def judge_one_retrieval(
    client: LLMClient,
    case: TestCase,
    items: list[RetrievedItem],
    *,
    facts: list[str] | None = None,
) -> RetrievalCaseResult:
    """Grade one case's retrieved evidence against its gold facts (0–3 labels).

    Shared by the batch ``evaluate_retrieval`` chain and the per-question「评估」
    endpoint, so a single case and the whole suite produce identical label
    semantics. With no items it returns an all-miss result（gold 全未覆盖）。
    """

    facts = gold_facts(case) if facts is None else facts
    item_texts = [it.text for it in sorted(items, key=lambda x: x.rank)]
    if not item_texts:
        return RetrievalCaseResult(
            case_id=case.id,
            question_type=case.question_type,
            difficulty=case.difficulty,
            retrieved_count=0,
            gold_count=len(facts),
            item_grades=[],
            gold_covered_at=[0] * len(facts),
            rationale="未上传该用例的检索结果。",
        )
    payload = client.judge_retrieval(
        question=case.question,
        expected_answer=case.expected_answer,
        gold_facts=facts,
        items=item_texts,
    )
    return RetrievalCaseResult(
        case_id=case.id,
        question_type=case.question_type,
        difficulty=case.difficulty,
        retrieved_count=len(item_texts),
        gold_count=len(facts),
        item_grades=[int(g) for g in (payload.get("item_grades") or [])],
        gold_covered_at=[int(c) for c in (payload.get("gold_covered_at") or [])],
        rationale=str(payload.get("rationale", "")),
        judge_token_usage=TokenUsage(**(payload.get("usage") or {})),
    )


def evaluate_retrieval(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    retrieval: RetrievalSet,
    *,
    k_values: list[int] | None = None,
    run_id: str | None = None,
) -> RetrievalRun:
    """Judge each case's uploaded retrieved evidence against its gold facts."""

    ks = sorted(k_values or config.retrieval_k) or [1, 3, 5, 10]
    run = RetrievalRun(
        run_id=run_id or _new_id("rrun"),
        suite_id=suite.suite_id,
        project_id=suite.project_id,
        agent_name=retrieval.agent_name,
        backend="eval-llm",
        k_values=ks,
    )
    for case in suite.cases:
        run.results.append(
            judge_one_retrieval(
                client,
                case,
                retrieval.for_case(case.id),
                facts=confirmed_gold_facts(store, case),  # D9：草稿黄金不参与打分
            )
        )
    store.save_retrieval_run(run)
    return run


# --- live (DB) retrieval evaluation：拉真实查询日志，无需 gold ----------------


def _coerce_live_type(intent: str | None) -> QuestionType:
    """Map a serving intent onto the question taxonomy, defaulting safely.

    Production intents are often ``general`` (not a taxonomy member); rather than
    fail, such cases bucket as FACTOID. Type buckets are advisory for live runs —
    the overall (type-agnostic) IR metrics are the load-bearing numbers.
    """

    try:
        return QuestionType.coerce(intent or "")
    except ValueError:
        return QuestionType.FACTOID


def build_live_artifacts(
    cases: list[LiveCase],
    *,
    project_id: str | None,
    agent_name: str = "serving",
    suite_id: str | None = None,
) -> tuple[TestSuite, RetrievalSet]:
    """Turn pulled LiveCases into a synthetic suite + retrieval set.

    The suite carries the real questions (no gold labels), and the retrieval set
    carries the ranked evidence keyed by query_id, so the existing
    ``evaluate_retrieval`` chain runs unchanged (precision-only, gold_count=0).
    """

    sid = suite_id or f"live_{uuid.uuid4().hex[:10]}"
    suite = TestSuite(
        suite_id=sid,
        project_id=project_id,
        backend="serving-db",
        corpus_files=sorted({c.domain for c in cases if c.domain}),
    )
    rset = RetrievalSet(suite_id=sid, agent_name=agent_name)
    for c in cases:
        suite.cases.append(
            TestCase(
                id=c.query_id,
                question=c.question,
                question_type=_coerce_live_type(c.intent),
                expected_answer="",
                source=SourceRef(doc=c.domain or "serving"),
            )
        )
        rset.items[c.query_id] = [
            RetrievedItem(rank=it.rank, text=it.text, source=it.source_path)
            for it in sorted(c.items, key=lambda x: x.rank)
        ]
    return suite, rset


def evaluate_live_retrieval(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    cases: list[LiveCase],
    *,
    project_id: str | None = None,
    agent_name: str = "serving",
    k_values: list[int] | None = None,
    run_id: str | None = None,
) -> tuple[RetrievalRun, TestSuite]:
    """Evaluate already-pulled live cases (precision-family only, no gold).

    Persists a synthetic suite + retrieval set so the run's report endpoint works
    like any other retrieval run. DB access is the caller's job (keeps this
    function pure + unit-testable with hand-built LiveCases).
    """

    if not cases:
        raise ValueError("未拉取到任何查询日志，无法评测")
    suite, rset = build_live_artifacts(
        cases, project_id=project_id, agent_name=agent_name
    )
    store.save_suite(suite)
    store.save_retrieval_set(rset)
    run = evaluate_retrieval(
        store, client, config, suite, rset, k_values=k_values, run_id=run_id
    )
    return run, suite
