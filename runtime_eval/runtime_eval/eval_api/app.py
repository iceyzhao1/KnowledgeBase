"""eval-api FastAPI app: orchestration backend + eval-web static host.

REST surface (consumed by eval-web):

    POST /api/v1/projects                        {name} -> {project_id}
    GET  /api/v1/projects
    POST /api/v1/projects/{pid}/documents        multipart -> {document_id, sections}
    GET  /api/v1/projects/{pid}/documents
    POST /api/v1/projects/{pid}/suites:generate  {document_ids[], types[], per_type} -> suite
    POST /api/v1/projects/{pid}/suites:import     multipart (YAML/JSON cases) -> suite
    GET  /api/v1/projects/{pid}/suites            -> [suite brief]  (服务端测试集列表)
    GET  /api/v1/projects/{pid}/runs              -> [run summary]  (项目级评测档案)
    GET  /api/v1/suites/{sid}/runs                -> [run summary]  (该测试集历次评测)
    GET  /api/v1/suites/{sid}/state               -> {steps, current_step, ...}  (四步完成度/恢复)
    GET  /api/v1/suites/{sid}
    POST /api/v1/suites/{sid}/responses          {agent_name, answers[]}
    GET  /api/v1/suites/{sid}/responses
    POST /api/v1/suites/{sid}/judge              {threshold?} -> {run_id, metrics}
    GET  /api/v1/runs/{rid}/metrics
    GET  /api/v1/runs/{rid}/report?format=html|md
    POST /api/v1/suites/{sid}/retrieval          {agent_name, items{cid:[...]}}
    POST /api/v1/suites/{sid}/retrieval:import   multipart (YAML/JSON evidence)
    GET  /api/v1/suites/{sid}/retrieval
    POST /api/v1/suites/{sid}/retrieval:evaluate {k_values?} -> {run_id, metrics}
    POST /api/v1/projects/{pid}/retrieval/live:evaluate {domain?,channel?,since?,limit?} -> {run_id, metrics}
    POST /api/v1/suites/{sid}/cases/{cid}/agent:run  {agent_name?} -> {answered,items[...]}  (逐题跑 agent)
    GET  /api/v1/suites/{sid}/agent:run/stream  ?agent_name&only_missing -> text/event-stream  (跑批 + SSE 实时进度)
    POST /api/v1/suites/{sid}/cases/{cid}/retrieval:pull  {domain?} -> {items[...]}   (逐题：拉最新一条)
    GET  /api/v1/suites/{sid}/retrieval:pull/stream  ?domain&agent_name&only_missing -> text/event-stream  (serving 批量取证 + SSE)
    POST /api/v1/suites/{sid}/cases/{cid}/retrieval:judge  -> {result}                 (逐题：评估这一条)
    GET  /api/v1/suites/{sid}/retrieval/progress  -> {cases[], pulled, judged, all_judged}
    GET  /api/v1/eval-criteria                    -> {k_values, score_k, weights, pass, ...}  (评估标准快照)
    POST /api/v1/suites/{sid}/retrieval:report  -> {run_id, metrics}                   (逐题：最终报告)
    GET  /api/v1/retrieval-runs/{rid}/metrics
    GET  /api/v1/retrieval-runs/{rid}/report?format=html|md
    GET  /api/v1/projects/{pid}/gold              ?status=draft|confirmed -> {records}
    POST /api/v1/projects/{pid}/gold              {question,...} -> gold     (新建人工黄金)
    GET  /api/v1/gold/{fingerprint}               -> gold
    PUT  /api/v1/gold/{fingerprint}               {patch...} -> gold         (编辑)
    DELETE /api/v1/gold/{fingerprint}             -> {deleted}
    POST /api/v1/gold/{fingerprint}:confirm       -> gold                    (确认草稿)
    POST /api/v1/suites/{sid}/gold:confirm-all    -> {confirmed,total}       (批量确认)
    GET  /                                        eval-web SPA
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..shared.models import (
    AgentResponse,
    Document,
    GoldRecord,
    _now_iso,
    Project,
    QuestionType,
    ResponseSet,
    RetrievalRun,
    RetrievalSet,
    RetrievedItem,
    RunSummary,
    SourceRef,
    TestCase,
    TestSuite,
    TokenUsage,
)
from . import orchestrator
from .config import ApiConfig, load_config
from .db_source import (
    ServingDBUnavailable,
    pull_by_query_id,
    pull_latest_for_question,
    pull_live_cases,
    search_similar_questions,
)
from .importer import ImportError_, parse_retrieval_file, parse_suite_file
from .llm_client import LLMClient
from .roster import load_roster
from .metrics import ReportMetrics, compute_metrics
from .parser import UnsupportedDocument, parse_document
from .report import (
    render_html,
    render_markdown,
    render_retrieval_html,
    render_retrieval_markdown,
    write_reports,
    write_retrieval_reports,
)
from .retrieval_metrics import (
    RetrievalReportMetrics,
    ScoringParams,
    compute_retrieval_metrics,
)
from .store import Store, create_store

STATIC_DIR = Path(__file__).resolve().parent.parent / "eval_web" / "static"


class CreateProjectIn(BaseModel):
    name: str


class GenerateSuiteIn(BaseModel):
    document_ids: list[str]
    types: list[str] | None = None
    per_type: int | None = None
    persona: str | None = None


class UpdateSuiteIn(BaseModel):
    cases: list[TestCase]
    name: str | None = None


class AnswerIn(BaseModel):
    case_id: str
    answer: str = ""
    latency_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class SaveResponsesIn(BaseModel):
    agent_name: str = "copilot-agent"
    answers: list[AnswerIn] = Field(default_factory=list)


class JudgeIn(BaseModel):
    threshold: float | None = None


class RetrievedItemIn(BaseModel):
    rank: int | None = None
    text: str
    source: str | None = None


class SaveRetrievalIn(BaseModel):
    agent_name: str = "retriever"
    items: dict[str, list[RetrievedItemIn]] = Field(default_factory=dict)


class EvaluateRetrievalIn(BaseModel):
    k_values: list[int] | None = None
    # A3 批量评估：只评估已标注（带黄金证据）的用例，召回族指标才有意义。
    only_annotated: bool = False


class EvaluateLiveIn(BaseModel):
    domain: str | None = None
    channel: str | None = None
    since: str | None = None  # ISO timestamp; rows with queried_at >= since
    limit: int = 50
    agent_name: str = "serving"
    k_values: list[int] | None = None


class PullForCaseIn(BaseModel):
    """Per-question「检索数据库」: pull the latest live record matching the text.

    ``query_id`` 为空时按问题文本做精确/子串匹配；命不中则返回相似候选供人工确认。
    人工确认某候选后，前端带上该候选的 ``query_id`` 再次请求以精确拉取。
    """

    domain: str | None = None
    agent_name: str = "serving"
    query_id: str | None = None


class PullDraftIn(BaseModel):
    """批量拉取 → 草稿 suite（A1）：从 serving 日志拉一批真实查询生成待标注 suite。

    与 ``EvaluateLiveIn`` 同源，但只生成草稿（suite + retrieval set），不立即评估；
    命中黄金库的用例自动回填黄金字段，省去重复标注。
    """

    domain: str | None = None
    channel: str | None = None
    since: str | None = None
    limit: int = 50
    agent_name: str = "serving"


class ChatIn(BaseModel):
    """对话页「知识库试问」：按问题文本预览知识库会检索到什么。

    复用 serving 查询日志：先精确/子串匹配最近一条；命不中则返回相似候选，
    让用户确认后再带 ``query_id`` 精确拉取。不依赖任何新服务。
    """

    question: str
    domain: str | None = None
    query_id: str | None = None


class RunAgentSuiteIn(BaseModel):
    """Claude 自动跑批：对测试集每题用 claude -p + MCP 自动检索并作答。

    回答落为 ResponseSet（应用层评测），agent 检索到的片段落为 RetrievalSet
    （检索层评测），替代人工逐题「检索数据库」。
    """

    agent_name: str = "claude-agent"
    system: str | None = None
    only_missing: bool = False  # True=只补跑还没回答的用例（断点续跑）


class RunAgentCaseIn(BaseModel):
    """逐题跑批：对单道题用 claude -p + MCP 自动检索并作答（前端逐题驱动）。"""

    agent_name: str = "claude-agent"
    system: str | None = None


class UpliftRunIn(BaseModel):
    """L4 增量价值：闭卷裸答(A) vs 用知识库(C) 两路对照，逐题配对算增量。"""

    baseline_agent: str = "closed-book"
    treatment_agent: str = "kb-agent"
    baseline_system: str | None = None
    treatment_system: str | None = None
    tau_low: float = 0.4
    tau_high: float = 0.6
    delta: float = 0.1
    theta_uplift: float = 0.1
    max_regression: float = 0.05
    pass_threshold: float | None = None


class GoldAnnotationIn(BaseModel):
    """黄金标注（A2）：对某用例写入/更新黄金字段，并落入黄金库按指纹复用。"""

    expected_answer: str = ""
    key_points: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    expected_entities: list[str] = Field(default_factory=list)
    source_doc: str | None = None
    source_section: str | None = None
    source_quote: str | None = None
    difficulty: str | None = None
    question_type: str | None = None
    notes: str | None = None
    save_to_library: bool = True  # 同步写入黄金库（默认开）


class GoldUpsertIn(BaseModel):
    """黄金集库直接编辑（Phase 1）：patch 已有黄金的字段。

    所有字段可空 → 只更新传入的部分。``question`` 一般不改（指纹是主键），如需改题
    请走「新建」。人工编辑默认把 ``source_kind`` 记为 manual，但 ``status`` 保持不变，
    需另调 :confirm 才算确认。
    """

    question: str | None = None
    question_type: str | None = None
    expected_answer: str | None = None
    key_points: list[str] | None = None
    expected_evidence: list[str] | None = None
    expected_entities: list[str] | None = None
    source_doc: str | None = None
    source_section: str | None = None
    source_quote: str | None = None
    difficulty: str | None = None
    notes: str | None = None
    status: str | None = None  # 显式传可改状态（draft/confirmed）


class GoldCreateIn(BaseModel):
    """黄金集库「新建」：服务端按 question 算指纹建一条已确认的人工黄金。"""

    question: str
    question_type: str = "factoid"
    expected_answer: str = ""
    key_points: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    expected_entities: list[str] = Field(default_factory=list)
    source_doc: str | None = None
    source_section: str | None = None
    source_quote: str | None = None
    difficulty: str = "normal"
    notes: str | None = None


def _metrics_dict(m: ReportMetrics) -> dict:
    return {
        "run_id": m.run_id,
        "suite_id": m.suite_id,
        "agent_name": m.agent_name,
        "backend": m.backend,
        "pass_threshold": m.pass_threshold,
        "total_cases": m.total_cases,
        "answered_cases": m.answered_cases,
        "overall_accuracy": m.overall_accuracy,
        "overall_avg_score": m.overall_avg_score,
        "by_type": [
            {
                "type": b.question_type,
                "total": b.total,
                "accuracy": b.accuracy,
                "avg_score": b.avg_score,
                "correct": b.correct,
                "partial": b.partial,
                "incorrect": b.incorrect,
                "missing": b.missing,
            }
            for b in m.by_type
        ],
        "latency": {
            "count": m.latency.count,
            "mean_ms": m.latency.mean_ms,
            "p50_ms": m.latency.p50_ms,
            "p95_ms": m.latency.p95_ms,
            "max_ms": m.latency.max_ms,
        },
        "agent_tokens": m.agent_tokens.total_tokens,
        "agent_tokens_avg": m.agent_tokens_avg,
        "framework_generation_tokens": m.framework_generation_tokens.total_tokens,
        "framework_judge_tokens": m.framework_judge_tokens.total_tokens,
    }


def _retrieval_metrics_dict(m: RetrievalReportMetrics) -> dict:
    def _kmap(d: dict[int, float]) -> dict[str, float]:
        return {str(k): d.get(k, 0.0) for k in m.k_values}

    return {
        "run_id": m.run_id,
        "suite_id": m.suite_id,
        "agent_name": m.agent_name,
        "backend": m.backend,
        "k_values": m.k_values,
        "total_cases": m.total_cases,
        "judged_cases": m.judged_cases,
        "has_gold": m.has_gold,
        "hit_rate": _kmap(m.hit_rate),
        "recall": _kmap(m.recall),
        "mrr": _kmap(m.mrr),
        "ndcg": _kmap(m.ndcg),
        "context_precision": _kmap(m.context_precision),
        "context_recall": m.context_recall,
        "by_type": [
            {
                "type": b.question_type,
                "total": b.total,
                "hit_rate": {str(k): b.hit_rate.get(k, 0.0) for k in m.k_values},
                "recall": {str(k): b.recall.get(k, 0.0) for k in m.k_values},
                "mrr": {str(k): b.mrr.get(k, 0.0) for k in m.k_values},
                "ndcg": {str(k): b.ndcg.get(k, 0.0) for k in m.k_values},
                "context_precision": {
                    str(k): b.context_precision.get(k, 0.0) for k in m.k_values
                },
                "context_recall": b.context_recall,
            }
            for b in m.by_type
        ],
        "score_k": m.score_k,
        "gold_cases": m.gold_cases,
        "kb_score": m.kb_score,
        "pass_rate": m.pass_rate,
        "buckets": m.buckets,
        "by_difficulty": [
            {
                "difficulty": d.difficulty,
                "total": d.total,
                "ndcg": d.ndcg,
                "recall": d.recall,
                "context_precision": d.context_precision,
                "kb_score": d.kb_score,
                "pass_rate": d.pass_rate,
            }
            for d in m.by_difficulty
        ],
        "cases": [
            {
                "case_id": c.case_id,
                "type": c.question_type,
                "difficulty": c.difficulty,
                "recall_k": c.recall_k,
                "mrr_k": c.mrr_k,
                "ndcg_k": c.ndcg_k,
                "precision_k": c.precision_k,
                "context_recall": c.context_recall,
                "rank_first": c.rank_first,
                "bucket": c.bucket,
                "s_q": c.s_q,
                "passed": c.passed,
            }
            for c in m.cases
        ],
    }


def _retrieval_case_dict(r) -> dict:
    """Serialize one RetrievalCaseResult for the per-question UI."""
    relevant = sum(1 for g in r.item_grades if g > 0)
    covered = sum(1 for c in r.gold_covered_at if c >= 1)
    return {
        "case_id": r.case_id,
        "question_type": r.question_type.value,
        "retrieved_count": r.retrieved_count,
        "gold_count": r.gold_count,
        "relevant_count": relevant,
        "covered_count": covered,
        "item_grades": r.item_grades,
        "gold_covered_at": r.gold_covered_at,
        "rationale": r.rationale,
        "judge_tokens": r.judge_token_usage.total_tokens,
    }


def _suite_dict(suite) -> dict:
    return {
        "suite_id": suite.suite_id,
        "project_id": suite.project_id,
        "backend": suite.backend,
        "name": suite.name,
        "created_at": suite.created_at,
        "corpus_files": suite.corpus_files,
        "document_ids": suite.document_ids,
        "generation_usage": suite.generation_usage.model_dump(),
        "cases": [
            {
                "id": c.id,
                "question": c.question,
                "question_type": c.question_type.value,
                "expected_answer": c.expected_answer,
                "key_points": c.key_points,
                "expected_evidence": c.expected_evidence,
                "expected_entities": c.expected_entities,
                "source": c.source.model_dump(),
                "difficulty": c.difficulty,
            }
            for c in suite.cases
        ],
    }


def _suite_brief(suite) -> dict:
    """测试集列表项（轻量，不含全部用例），供下拉/看板读。"""
    return {
        "suite_id": suite.suite_id,
        "project_id": suite.project_id,
        "backend": suite.backend,
        "name": suite.name,
        "created_at": suite.created_at,
        "case_count": len(suite.cases),
        "corpus_files": suite.corpus_files,
    }


def _run_summary_dict(s: RunSummary) -> dict:
    return {
        "run_id": s.run_id,
        "project_id": s.project_id,
        "suite_id": s.suite_id,
        "layer": s.layer,
        "kind": s.kind,
        "created_at": s.created_at,
        "k": s.k,
        "status": s.status,
        "metrics": s.metrics,
    }


def _gold_dict(g: GoldRecord) -> dict:
    return {
        "fingerprint": g.fingerprint,
        "project_id": g.project_id,
        "question": g.question,
        "question_type": g.question_type.value,
        "expected_answer": g.expected_answer,
        "key_points": g.key_points,
        "expected_evidence": g.expected_evidence,
        "expected_entities": g.expected_entities,
        "source": g.source.model_dump() if g.source else None,
        "difficulty": g.difficulty,
        "notes": g.notes,
        "source_kind": g.source_kind,
        "status": g.status,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
    }


def _gold_confirmed_for(store, case) -> bool:
    """该用例的黄金是否已确认（黄金库里存在且 status='confirmed'）。"""
    gold = store.match_gold(case.question)
    return gold is not None and gold.status == "confirmed"


def _retrieval_summary(run, mdict: dict, *, kind: str, status: str = "done") -> RunSummary:
    """从检索层指标 dict 拍扁出一条 run 快照（看板/报告历史读这个）。"""
    k = mdict.get("score_k")
    ks = str(k) if k is not None else None
    metrics = {
        "total_cases": mdict.get("total_cases", 0),
        "judged_cases": mdict.get("judged_cases", 0),
        "has_gold": mdict.get("has_gold", False),
        "kb_score": mdict.get("kb_score"),
        "pass_rate": mdict.get("pass_rate"),
        "context_recall": mdict.get("context_recall"),
        "ndcg": (mdict.get("ndcg") or {}).get(ks) if ks else None,
        "recall": (mdict.get("recall") or {}).get(ks) if ks else None,
        "hit_rate": (mdict.get("hit_rate") or {}).get(ks) if ks else None,
        "mrr": (mdict.get("mrr") or {}).get(ks) if ks else None,
        "agent_name": mdict.get("agent_name"),
    }
    return RunSummary(
        run_id=run.run_id,
        project_id=run.project_id,
        suite_id=run.suite_id,
        layer="retrieval",
        kind=kind,
        k=k,
        status=status,
        metrics=metrics,
    )


def _response_summary(run, mdict: dict, *, kind: str = "batch", status: str = "done") -> RunSummary:
    """从生成层指标 dict 拍扁出一条 run 快照。"""
    metrics = {
        "total_cases": mdict.get("total_cases", 0),
        "answered_cases": mdict.get("answered_cases", 0),
        "overall_accuracy": mdict.get("overall_accuracy"),
        "overall_avg_score": mdict.get("overall_avg_score"),
        "pass_threshold": mdict.get("pass_threshold"),
        "agent_name": mdict.get("agent_name"),
    }
    return RunSummary(
        run_id=run.run_id,
        project_id=run.project_id,
        suite_id=run.suite_id,
        layer="response",
        kind=kind,
        status=status,
        metrics=metrics,
    )


def _criteria_dict(config) -> dict:
    """评估标准快照：K 值 / 通过线 / 权重 / 难度系数，前端在出报告前展示给人看。"""
    return {
        "k_values": sorted(config.retrieval_k) or [1, 3, 5, 10],
        "score_k": config.score_k,
        "weights": {
            "find": config.score_w_find,
            "rank": config.score_w_rank,
            "quality": config.score_w_quality,
            "quality_alpha": config.score_quality_alpha,
        },
        "pass": {
            "recall": config.pass_recall,
            "rank": config.pass_rank,
            "context_recall": config.pass_ctx_recall,
        },
        "noisy_precision": config.noisy_precision,
        "difficulty_weights": dict(config.difficulty_weights),
    }


def create_app(
    env_file: str | None = None,
    *,
    config: ApiConfig | None = None,
    store: Store | None = None,
    client: LLMClient | None = None,
) -> FastAPI:
    config = config or load_config(env_file)
    store = store or create_store(config)
    client = client or LLMClient(config.eval_llm_url, timeout=config.request_timeout)
    scoring = ScoringParams.from_config(config)
    roster = load_roster(config.roster_env)  # L2 参赛模型花名册（配置驱动）
    app = FastAPI(title="eval-api", version="2.0.0")

    # --- projects ---
    @app.post("/api/v1/projects")
    def create_project(body: CreateProjectIn):
        project = Project(project_id=f"proj_{uuid.uuid4().hex[:10]}", name=body.name)
        store.save_project(project)
        return {"project_id": project.project_id, "name": project.name}

    @app.get("/api/v1/projects")
    def list_projects():
        return [p.model_dump() for p in store.list_projects()]

    @app.delete("/api/v1/projects/{pid}")
    def delete_project(pid: str):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        store.delete_project(pid)
        shutil.rmtree(store.project_dir(pid), ignore_errors=True)
        return {"deleted": pid}

    @app.get("/api/v1/eval-criteria")
    def eval_criteria():
        """评估标准快照：K 值 / 通过线 / 权重 / 难度系数，出报告前展示给人看。"""
        return _criteria_dict(config)

    # --- documents ---
    @app.post("/api/v1/projects/{pid}/documents")
    async def upload_document(pid: str, file: UploadFile = File(...)):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        data = await file.read()
        name = file.filename or "document.txt"
        try:
            parsed = parse_document(name=name, data=data, max_chars=config.max_doc_chars)
        except UnsupportedDocument as exc:
            raise HTTPException(415, str(exc))
        doc = Document(
            document_id=f"doc_{uuid.uuid4().hex[:10]}",
            project_id=pid,
            name=name,
            rel_path=name,
            char_count=parsed["char_count"],
            sections=parsed["sections"],
            text=parsed["text"],
        )
        store.save_document(doc)
        return {
            "document_id": doc.document_id,
            "name": doc.name,
            "char_count": doc.char_count,
            "sections": doc.section_titles(),
        }

    @app.get("/api/v1/projects/{pid}/documents")
    def list_documents(pid: str):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        return [
            {
                "document_id": d.document_id,
                "name": d.name,
                "char_count": d.char_count,
                "sections": d.section_titles(),
            }
            for d in store.list_documents(pid)
        ]

    # --- suites ---
    @app.post("/api/v1/projects/{pid}/suites:generate")
    def generate_suite(pid: str, body: GenerateSuiteIn):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        try:
            suite = orchestrator.generate_suite(
                store,
                client,
                config,
                project_id=pid,
                document_ids=body.document_ids,
                types=body.types,
                per_type=body.per_type,
                persona=body.persona,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            # eval-llm/claude 的真实报错透传给前端（502），而非泛化 500。
            raise HTTPException(502, str(exc))
        return _suite_dict(suite)

    @app.post("/api/v1/projects/{pid}/suites:generate/preview")
    def generate_suite_preview(pid: str, body: GenerateSuiteIn):
        """预览出题将发给大模型的提示词（按所选文档逐篇渲染，不调用模型）。"""
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        docs = [store.get_document(pid, did) for did in body.document_ids]
        docs = [d for d in docs if d is not None]
        if not docs:
            raise HTTPException(400, "未找到任何有效文档，无法预览提示词")
        prompts = []
        for doc in docs:
            text = doc.text[: config.max_doc_chars] if config.max_doc_chars else doc.text
            try:
                pv = client.generate_cases_preview(
                    document_text=text,
                    doc_ref=doc.rel_path,
                    types=body.types,
                    per_type=body.per_type or config.per_type,
                    persona=body.persona,
                )
            except RuntimeError as exc:
                raise HTTPException(502, str(exc))
            pv["doc_name"] = doc.name
            prompts.append(pv)
        return {"prompts": prompts}

    @app.post("/api/v1/projects/{pid}/suites:import")
    async def import_suite(
        pid: str,
        file: UploadFile = File(...),
        name: str = Form(""),
    ):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        filename = file.filename or "suite.yaml"
        data = await file.read()
        doc_ref = filename.rsplit(".", 1)[0]  # default SourceRef.doc if none given
        try:
            cases = parse_suite_file(name=filename, data=data, doc_ref=doc_ref)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc))
        if not cases:
            raise HTTPException(400, "未解析到任何用例")
        suite = TestSuite(
            suite_id=f"suite_{uuid.uuid4().hex[:10]}",
            project_id=pid,
            backend="imported",
            name=(name.strip() or doc_ref),
            corpus_files=[filename],
            cases=cases,
        )
        store.save_suite(suite)
        return _suite_dict(suite)

    @app.get("/api/v1/suites/{sid}")
    def get_suite(sid: str):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        return _suite_dict(suite)

    @app.put("/api/v1/suites/{sid}")
    def update_suite(sid: str, body: UpdateSuiteIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not body.cases:
            raise HTTPException(400, "测试集至少保留一题")
        suite.cases = body.cases
        if body.name is not None:
            suite.name = body.name.strip()
        store.save_suite(suite)
        return _suite_dict(suite)

    # --- 评测档案：服务端列表 / 恢复（前端不再靠 localStorage 记账） ---
    @app.get("/api/v1/projects/{pid}/suites")
    def list_project_suites(pid: str):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        return [_suite_brief(s) for s in store.list_suites(pid)]

    @app.get("/api/v1/projects/{pid}/runs")
    def list_project_runs(pid: str):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        return [_run_summary_dict(r) for r in store.list_run_summaries(project_id=pid)]

    @app.get("/api/v1/suites/{sid}/runs")
    def list_suite_runs(sid: str):
        if store.get_suite(sid) is None:
            raise HTTPException(404, "suite not found")
        return [_run_summary_dict(r) for r in store.list_run_summaries(suite_id=sid)]

    @app.get("/api/v1/suites/{sid}/state")
    def suite_state(sid: str):
        """聚合该测试集的四步完成度，供向导刷新/换设备时定位该停在哪一步。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        pid = suite.project_id or "default"
        case_count = len(suite.cases)
        # 黄金确认数：黄金库里该问题已 confirmed 的题（D9 草稿不算）
        gold_confirmed = sum(1 for c in suite.cases if _gold_confirmed_for(store, c))
        # 证据数：检索结果集中已有条目的题
        rset = store.get_retrieval_set(sid)
        evidence_count = sum(1 for items in rset.items.values() if items)
        # 文档数（项目级）
        doc_count = len(store.list_documents(pid)) if store.get_project(pid) else 0
        latest = store.latest_run_summary(sid)
        latest_dict = _run_summary_dict(latest) if latest else None
        # 四步完成度：①有文档 ②有题 ③黄金已确认 ④有证据（出报告以最近 run 为准）
        steps = {
            "documents": doc_count > 0,
            "questions": case_count > 0,
            "gold": gold_confirmed > 0,
            "evidence": evidence_count > 0,
            "report": latest is not None and latest.status == "done",
        }
        # 该停的步骤：第一个未完成的
        order = ["documents", "questions", "gold", "evidence", "report"]
        current = next((s for s in order if not steps[s]), "report")
        return {
            "suite_id": sid,
            "project_id": pid,
            "doc_count": doc_count,
            "case_count": case_count,
            "gold_confirmed": gold_confirmed,
            "evidence_count": evidence_count,
            "steps": steps,
            "current_step": current,
            "latest_run": latest_dict,
        }

    # --- responses ---
    @app.post("/api/v1/suites/{sid}/responses")
    def save_responses(sid: str, body: SaveResponsesIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        rs = ResponseSet(suite_id=sid, agent_name=body.agent_name)
        for a in body.answers:
            rs.responses.append(
                AgentResponse(
                    case_id=a.case_id,
                    answer=a.answer,
                    latency_ms=a.latency_ms,
                    token_usage=TokenUsage(
                        prompt_tokens=a.prompt_tokens,
                        completion_tokens=a.completion_tokens,
                        total_tokens=a.total_tokens
                        or (a.prompt_tokens + a.completion_tokens),
                    ),
                )
            )
        store.save_responses(rs)
        return {"suite_id": sid, "count": len(rs.responses)}

    @app.get("/api/v1/suites/{sid}/responses")
    def get_responses(sid: str):
        if store.get_suite(sid) is None:
            raise HTTPException(404, "suite not found")
        rs = store.get_responses(sid)
        return {
            "suite_id": sid,
            "agent_name": rs.agent_name,
            "responses": [r.model_dump() for r in rs.responses],
        }

    # --- agent 自动跑批（claude -p + MCP 检索知识库） ---
    @app.post("/api/v1/suites/{sid}/agent:run")
    def run_agent_suite(sid: str, body: RunAgentSuiteIn):
        """对测试集每题跑 claude agent：自动检索知识库 + 作答，落库回答与检索片段。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可跑")
        try:
            summary = orchestrator.run_agent_suite(
                store,
                client,
                config,
                suite,
                agent_name=body.agent_name,
                system=body.system,
                only_missing=body.only_missing,
            )
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        return summary

    @app.post("/api/v1/suites/{sid}/cases/{cid}/agent:run")
    def run_agent_case(sid: str, cid: str, body: RunAgentCaseIn):
        """逐题跑 claude agent：自动检索 + 作答，合并落库（不动其它题）。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        case = suite.by_id(cid)
        if case is None:
            raise HTTPException(404, "case not found")
        try:
            return orchestrator.run_agent_for_case(
                store,
                client,
                config,
                suite,
                case,
                agent_name=body.agent_name,
                system=body.system,
            )
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/v1/suites/{sid}/agent:run/stream")
    def run_agent_suite_stream(
        sid: str,
        agent_name: str = "claude-agent",
        only_missing: bool = False,
        system: str | None = None,
    ):
        """跑批 + SSE 实时进度：逐题调用 agent，边跑边把进度事件推给前端。

        前端用 ``EventSource`` 订阅，每个事件是一行 ``data: <json>``，字段 ``event``
        取值：``start`` / ``case_start`` / ``case_done`` / ``case_error`` / ``done``。
        ``only_missing=true`` 时由后端判定「已检索或已回答」的题并跳过（真正生效）。
        """

        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可跑")

        def _sse(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def _done_case_ids() -> set[str]:
            """已完成（已有非空回答 或 已有检索片段）的用例集合。"""
            done: set[str] = set()
            for r in store.get_responses(sid).responses:
                if (r.answer or "").strip():
                    done.add(r.case_id)
            for cid, items in store.get_retrieval_set(sid).items.items():
                if items:
                    done.add(cid)
            return done

        def gen():
            done = _done_case_ids() if only_missing else set()
            todo = [c for c in suite.cases if c.id not in done]
            skipped = len(suite.cases) - len(todo)
            yield _sse(
                {
                    "event": "start",
                    "total": len(suite.cases),
                    "todo": len(todo),
                    "skipped": skipped,
                }
            )
            ok = ret = tok = err = 0
            for i, case in enumerate(todo):
                yield _sse(
                    {
                        "event": "case_start",
                        "i": i,
                        "n": len(todo),
                        "case_id": case.id,
                        "question": case.question,
                    }
                )
                try:
                    res = orchestrator.run_agent_for_case(
                        store,
                        client,
                        config,
                        suite,
                        case,
                        agent_name=agent_name,
                        system=system,
                    )
                    if res.get("answered"):
                        ok += 1
                    if res.get("retrieved_count"):
                        ret += 1
                    tok += int(res.get("tokens") or 0)
                    yield _sse({"event": "case_done", **res})
                except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
                    err += 1
                    yield _sse(
                        {"event": "case_error", "case_id": case.id, "error": str(exc)}
                    )
            yield _sse(
                {
                    "event": "done",
                    "ok": ok,
                    "ret": ret,
                    "tok": tok,
                    "err": err,
                    "todo": len(todo),
                    "skipped": skipped,
                }
            )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- L4 业务价值层：对照组增量（uplift）---
    @app.post("/api/v1/suites/{sid}/uplift:run")
    def run_uplift(sid: str, body: UpliftRunIn):
        """跑闭卷(A) vs 用库(C) 两路对照，算增量价值，落 RunSummary(layer='value')。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可跑")
        try:
            summary = orchestrator.run_value_uplift(
                store,
                client,
                config,
                suite,
                baseline_agent=body.baseline_agent,
                treatment_agent=body.treatment_agent,
                baseline_system=body.baseline_system,
                treatment_system=body.treatment_system,
                tau_low=body.tau_low,
                tau_high=body.tau_high,
                delta=body.delta,
                theta_uplift=body.theta_uplift,
                max_regression=body.max_regression,
                pass_threshold=body.pass_threshold,
            )
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        return {"run_id": summary.run_id, "metrics": summary.metrics}

    @app.get("/api/v1/suites/{sid}/uplift:run/stream")
    def run_uplift_stream(sid: str):
        """跑两路对照 + SSE 实时进度：四阶段（闭卷作答/判分、用库作答/判分）逐题推进度。

        前端用 ``EventSource`` 订阅，事件 ``event`` 取值 ``start`` / ``case_start`` /
        ``case_done`` / ``case_error`` / ``done``（``done`` 带 ``metrics``，可直接渲染报告）。
        阈值参数走默认（高级项，前端隐藏）。
        """
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可跑")

        def _sse(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def gen():
            try:
                for ev in orchestrator.iter_value_uplift(store, client, config, suite):
                    yield _sse(ev)
            except Exception as exc:  # noqa: BLE001 - 把致命错误作为事件发回前端
                yield _sse({"event": "fatal", "error": str(exc)})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/suites/{sid}/uplift")
    def get_uplift(sid: str):
        """读该测试集最近一次增量价值快照（没有则 404）。"""
        summary = store.get_run_summary(f"uplift_{sid}")
        if summary is None:
            summary = store.latest_run_summary(sid, layer="value")
        if summary is None:
            raise HTTPException(404, "尚无增量价值评测结果")
        return {"run_id": summary.run_id, "metrics": summary.metrics}

    # --- L2 答案对照层：多模型 × 黄金答案 ---
    @app.get("/api/v1/eval-roster")
    def eval_roster():
        """读参赛模型花名册：前端据此渲染「可勾选哪些模型参赛」。"""
        return {
            "roster": [
                {
                    "id": e.id, "label": e.label, "channel": e.channel,
                    "model": e.model, "enabled": e.enabled,
                }
                for e in roster
            ]
        }

    @app.get("/api/v1/suites/{sid}/answer-match:run/stream")
    def run_answer_match_stream(sid: str, models: str = "", cache: int = 1):
        """跑答案对照 + SSE 实时进度：每个模型一段「作答 → 对照判」，逐题推进度。

        ``models`` 为前端勾选的模型 id（逗号分隔），空=花名册里默认参赛的那批。
        ``cache=1`` 命中上次快照里没变的(模型,题)直接复用；``cache=0`` 强制全跑。
        前端用 ``EventSource`` 订阅，事件同 uplift（start/case_*/done，done 带 metrics）。
        """
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可跑")

        selected = [m.strip() for m in models.split(",") if m.strip()] or None

        def _sse(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def gen():
            try:
                for ev in orchestrator.iter_answer_match(
                    store, client, config, suite,
                    roster=roster, selected_ids=selected, use_cache=bool(cache),
                ):
                    yield _sse(ev)
            except Exception as exc:  # noqa: BLE001 - 把致命错误作为事件发回前端
                yield _sse({"event": "fatal", "error": str(exc)})

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/suites/{sid}/answer-match")
    def get_answer_match(sid: str):
        """读该测试集最近一次答案对照快照（没有则 404）。"""
        summary = store.get_run_summary(f"ans_{sid}")
        if summary is None:
            summary = store.latest_run_summary(sid, layer="answer_match")
        if summary is None:
            raise HTTPException(404, "尚无答案对照评测结果")
        return {"run_id": summary.run_id, "metrics": summary.metrics}

    # --- judging ---
    @app.post("/api/v1/suites/{sid}/judge")
    def judge(sid: str, body: JudgeIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        responses = store.get_responses(sid)
        if not any(r.answer.strip() for r in responses.responses):
            raise HTTPException(400, "尚无任何已上传回答，无法评测")
        run = orchestrator.judge_suite(
            store, client, config, suite, responses, pass_threshold=body.threshold
        )
        write_reports(run, suite, store.reports_dir(run.project_id or "default"))
        m = compute_metrics(run, suite)
        mdict = _metrics_dict(m)
        store.save_run_summary(_response_summary(run, mdict, kind="batch"))
        return {"run_id": run.run_id, "metrics": mdict}

    # --- retrieval layer (检索层) ---
    @app.post("/api/v1/suites/{sid}/retrieval")
    def save_retrieval(sid: str, body: SaveRetrievalIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        rset = RetrievalSet(suite_id=sid, agent_name=body.agent_name)
        for cid, items in body.items.items():
            ranked = [
                RetrievedItem(
                    rank=it.rank if it.rank is not None else i,
                    text=it.text,
                    source=it.source,
                )
                for i, it in enumerate(items, start=1)
                if it.text.strip()
            ]
            if ranked:
                rset.items[cid] = ranked
        store.save_retrieval_set(rset)
        return {"suite_id": sid, "cases": len(rset.items)}

    @app.post("/api/v1/suites/{sid}/retrieval:import")
    async def import_retrieval(sid: str, file: UploadFile = File(...)):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        name = file.filename or "retrieval.json"
        data = await file.read()
        try:
            agent_name, items = parse_retrieval_file(name=name, data=data)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc))
        rset = RetrievalSet(suite_id=sid, agent_name=agent_name, items=items)
        store.save_retrieval_set(rset)
        return {"suite_id": sid, "agent_name": agent_name, "cases": len(rset.items)}

    @app.get("/api/v1/suites/{sid}/retrieval")
    def get_retrieval(sid: str):
        if store.get_suite(sid) is None:
            raise HTTPException(404, "suite not found")
        rset = store.get_retrieval_set(sid)
        return {
            "suite_id": sid,
            "agent_name": rset.agent_name,
            "items": {
                cid: [it.model_dump() for it in items]
                for cid, items in rset.items.items()
            },
        }

    @app.post("/api/v1/suites/{sid}/retrieval:evaluate")
    def evaluate_retrieval(sid: str, body: EvaluateRetrievalIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        rset = store.get_retrieval_set(sid)
        if not rset.items:
            raise HTTPException(400, "尚无任何已上传检索结果，无法评测")
        # A3 批量评估：仅保留「已确认黄金」（D9）且已有检索结果的用例
        if body.only_annotated:
            kept = [
                c for c in suite.cases
                if orchestrator.confirmed_gold_facts(store, c) and rset.items.get(c.id)
            ]
            if not kept:
                raise HTTPException(400, "没有同时具备已确认黄金与检索结果的用例，无法批量评估")
            suite = suite.model_copy(update={"cases": kept})
            rset = rset.model_copy(
                update={"items": {c.id: rset.items[c.id] for c in kept}}
            )
        run = orchestrator.evaluate_retrieval(
            store, client, config, suite, rset, k_values=body.k_values
        )
        write_retrieval_reports(run, suite, store.reports_dir(run.project_id or "default"), scoring)
        m = compute_retrieval_metrics(run, scoring)
        mdict = _retrieval_metrics_dict(m)
        store.save_run_summary(_retrieval_summary(run, mdict, kind="batch"))
        return {
            "run_id": run.run_id,
            "evaluated_cases": len(suite.cases),
            "metrics": mdict,
        }

    @app.post("/api/v1/projects/{pid}/retrieval/live:evaluate")
    def evaluate_live_retrieval(pid: str, body: EvaluateLiveIn):
        """拉取真实查询日志（serving_query_logs）→ 精确率族检索层评测（无需 gold）。"""
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        try:
            cases = pull_live_cases(
                config,
                domain=body.domain,
                channel=body.channel,
                since=body.since,
                limit=body.limit,
            )
        except ServingDBUnavailable as exc:
            raise HTTPException(503, str(exc))
        if not cases:
            raise HTTPException(404, "未拉取到任何查询日志（检查 domain/channel/since 过滤）")
        run, suite = orchestrator.evaluate_live_retrieval(
            store,
            client,
            config,
            cases,
            project_id=pid,
            agent_name=body.agent_name,
            k_values=body.k_values,
        )
        write_retrieval_reports(run, suite, store.reports_dir(run.project_id or "default"), scoring)
        m = compute_retrieval_metrics(run, scoring)
        mdict = _retrieval_metrics_dict(m)
        store.save_run_summary(_retrieval_summary(run, mdict, kind="live"))
        return {
            "run_id": run.run_id,
            "suite_id": suite.suite_id,
            "pulled_cases": len(cases),
            "metrics": mdict,
        }

    @app.post("/api/v1/projects/{pid}/retrieval/live:pull")
    def pull_draft_suite(pid: str, body: PullDraftIn):
        """批量拉取真实查询日志 → 生成「待标注」草稿 suite（A1，不评估）。

        每条用例按问题指纹查黄金库；命中则自动回填黄金字段（``gold_hits``），其余
        留待人工在「黄金标注」界面补齐。拉取的检索证据一并存为 retrieval set，
        标注完成后可直接走批量评估。
        """
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        try:
            cases = pull_live_cases(
                config,
                domain=body.domain,
                channel=body.channel,
                since=body.since,
                limit=body.limit,
            )
        except ServingDBUnavailable as exc:
            raise HTTPException(503, str(exc))
        if not cases:
            raise HTTPException(404, "未拉取到任何查询日志（检查 domain/channel/since 过滤）")
        suite, rset = orchestrator.build_live_artifacts(
            cases, project_id=pid, agent_name=body.agent_name
        )
        # 命中黄金库 → 回填黄金字段，省去重复标注
        gold_hits = 0
        for case in suite.cases:
            gold = store.match_gold(case.question)
            if gold is not None:
                gold.apply_to_case(case)
                gold_hits += 1
        store.save_suite(suite)
        store.save_retrieval_set(rset)
        return {
            "suite_id": suite.suite_id,
            "pulled_cases": len(cases),
            "gold_hits": gold_hits,
            "pending_annotation": len(suite.cases) - gold_hits,
            "suite": _suite_dict(suite),
        }

    # --- 黄金标注 (A2) + 黄金库 (A4) ---
    @app.put("/api/v1/suites/{sid}/cases/{cid}/gold")
    def annotate_case_gold(sid: str, cid: str, body: GoldAnnotationIn):
        """对某用例写入黄金字段并回填到 suite；默认同步落入黄金库按指纹复用。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        case = suite.by_id(cid)
        if case is None:
            raise HTTPException(404, "case not found")
        # 1) 把标注写回用例
        case.expected_answer = body.expected_answer
        case.key_points = body.key_points
        case.expected_evidence = body.expected_evidence
        case.expected_entities = body.expected_entities
        if body.difficulty:
            case.difficulty = body.difficulty
        if body.question_type:
            case.question_type = QuestionType.coerce(body.question_type)
        if body.notes is not None:
            case.notes = body.notes
        if body.source_doc:
            case.source = SourceRef(
                doc=body.source_doc,
                section=body.source_section,
                quote=body.source_quote,
            )
        store.save_suite(suite)
        # 2) 同步黄金库（按问题指纹，可跨 suite 复用）
        gold = None
        if body.save_to_library:
            fp = GoldRecord.make_fingerprint(case.question)
            existing = store.get_gold(fp)
            gold = GoldRecord(
                fingerprint=fp,
                project_id=suite.project_id,
                question=case.question,
                question_type=case.question_type,
                expected_answer=case.expected_answer,
                key_points=case.key_points,
                expected_evidence=case.expected_evidence,
                expected_entities=case.expected_entities,
                source=case.source,
                difficulty=case.difficulty,
                notes=case.notes,
            )
            if existing is not None:  # 保留首次标注时间
                gold.created_at = existing.created_at
            store.save_gold(gold)
        return {
            "suite_id": sid,
            "case_id": cid,
            "saved_to_library": gold is not None,
            "fingerprint": gold.fingerprint if gold else None,
        }

    @app.get("/api/v1/suites/{sid}/gold/progress")
    def gold_progress(sid: str):
        """统计该 suite 的黄金标注进度（哪些用例已具备黄金证据/答案）。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        annotated = [
            c.id for c in suite.cases if c.expected_evidence or c.expected_answer
        ]
        return {
            "suite_id": sid,
            "total": len(suite.cases),
            "annotated": len(annotated),
            "pending": len(suite.cases) - len(annotated),
            "annotated_case_ids": annotated,
        }

    @app.get("/api/v1/projects/{pid}/gold")
    def list_gold_library(pid: str, status: str | None = None):
        """列出某项目下黄金库已有的标注（供复用/管理）。``status`` 可筛 draft/confirmed。"""
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        records = store.list_gold(pid)
        if status:
            records = [g for g in records if g.status == status]
        return {"project_id": pid, "records": [_gold_dict(g) for g in records]}

    @app.post("/api/v1/projects/{pid}/gold")
    def create_gold(pid: str, body: GoldCreateIn):
        """黄金集库「新建」一条人工黄金（默认已确认）。指纹由 question 计算。"""
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        q = (body.question or "").strip()
        if not q:
            raise HTTPException(400, "请填写问题")
        fp = GoldRecord.make_fingerprint(q)
        if store.get_gold(fp) is not None:
            raise HTTPException(409, "该问题已存在黄金，请直接编辑")
        source = None
        if body.source_doc:
            source = SourceRef(doc=body.source_doc, section=body.source_section, quote=body.source_quote)
        gold = GoldRecord(
            fingerprint=fp,
            project_id=pid,
            question=q,
            question_type=QuestionType.coerce(body.question_type),
            expected_answer=body.expected_answer,
            key_points=body.key_points,
            expected_evidence=body.expected_evidence,
            expected_entities=body.expected_entities,
            source=source,
            difficulty=body.difficulty or "normal",
            notes=body.notes,
            source_kind="manual",
            status="confirmed",
        )
        store.save_gold(gold)
        return _gold_dict(gold)

    # --- 黄金集库：单条 CRUD（Phase 1，按指纹直接管理） ---
    @app.get("/api/v1/gold/{fingerprint}")
    def get_gold_record(fingerprint: str):
        gold = store.get_gold(fingerprint)
        if gold is None:
            raise HTTPException(404, "gold not found")
        return _gold_dict(gold)

    @app.put("/api/v1/gold/{fingerprint}")
    def update_gold_record(fingerprint: str, body: GoldUpsertIn):
        """编辑一条黄金（只 patch 传入字段）。状态默认不动，需另调 :confirm。"""
        gold = store.get_gold(fingerprint)
        if gold is None:
            raise HTTPException(404, "gold not found")
        if body.question_type is not None:
            gold.question_type = QuestionType.coerce(body.question_type)
        if body.expected_answer is not None:
            gold.expected_answer = body.expected_answer
        if body.key_points is not None:
            gold.key_points = body.key_points
        if body.expected_evidence is not None:
            gold.expected_evidence = body.expected_evidence
        if body.expected_entities is not None:
            gold.expected_entities = body.expected_entities
        if body.difficulty is not None:
            gold.difficulty = body.difficulty
        if body.notes is not None:
            gold.notes = body.notes
        if body.source_doc is not None:
            gold.source = SourceRef(
                doc=body.source_doc, section=body.source_section, quote=body.source_quote
            )
        if body.status is not None:
            gold.status = body.status
        gold.source_kind = "manual"  # 人工编辑过
        gold.updated_at = _now_iso()
        store.save_gold(gold)
        return _gold_dict(gold)

    @app.delete("/api/v1/gold/{fingerprint}")
    def delete_gold_record(fingerprint: str):
        if not store.delete_gold(fingerprint):
            raise HTTPException(404, "gold not found")
        return {"deleted": True, "fingerprint": fingerprint}

    @app.post("/api/v1/gold/{fingerprint}:confirm")
    def confirm_gold_record(fingerprint: str):
        """把一条草稿黄金标记为已确认（确认后才参与打分，D9）。"""
        gold = store.get_gold(fingerprint)
        if gold is None:
            raise HTTPException(404, "gold not found")
        gold.status = "confirmed"
        gold.updated_at = _now_iso()
        store.save_gold(gold)
        return _gold_dict(gold)

    @app.post("/api/v1/suites/{sid}/gold:confirm-all")
    def confirm_suite_gold(sid: str):
        """批量确认：把该测试集所有题对应的草稿黄金一次性确认。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        confirmed = 0
        for case in suite.cases:
            gold = store.match_gold(case.question)
            if gold is not None and gold.status != "confirmed":
                gold.status = "confirmed"
                gold.updated_at = _now_iso()
                store.save_gold(gold)
                confirmed += 1
        return {"suite_id": sid, "confirmed": confirmed, "total": len(suite.cases)}

    # --- 对话页：知识库试问（检索预览） ---
    @app.post("/api/v1/chat")
    def chat_preview(body: ChatIn):
        """按问题预览知识库会检索到的片段（复用 serving 查询日志，无需 gold）。

        - 带 ``query_id``：用户已确认某候选，按 id 精确拉取。
        - 不带：先精确/子串匹配最近一条；命不中则返回相似候选交前端确认。
        """
        question = (body.question or "").strip()
        if not question and not body.query_id:
            raise HTTPException(400, "请输入问题")
        try:
            if body.query_id:
                live = pull_by_query_id(config, body.query_id)
                if live is None:
                    raise HTTPException(404, "未找到指定的查询记录（query_id 不存在）")
            else:
                live = pull_latest_for_question(config, question, domain=body.domain)
                if live is None:
                    candidates = search_similar_questions(
                        config, question, domain=body.domain, limit=5
                    )
                    return {"matched": False, "question": question, "candidates": candidates}
        except ServingDBUnavailable as exc:
            raise HTTPException(503, str(exc))
        items = [
            {"rank": it.rank, "text": it.text, "source": it.source_path}
            for it in sorted(live.items, key=lambda x: x.rank)
        ]
        return {
            "matched": True,
            "query_id": live.query_id,
            "question": question or live.question,
            "matched_question": live.question,
            "queried_at": live.queried_at,
            "duration_ms": live.duration_ms,
            "source_domain": live.domain,
            "items": items,
        }

    # --- per-question (逐题) retrieval flow ---
    # 逐题工作流：①「检索数据库」按问题文本拉取最新一条真实检索 → ②「评估」用
    # judge-retrieval 给这条打 0–3 相关性 → ③ 全部评完后「生成最终报告」聚合。
    @app.post("/api/v1/suites/{sid}/cases/{cid}/retrieval:pull")
    def pull_case_retrieval(sid: str, cid: str, body: PullForCaseIn):
        """按问题文本拉取该用例最新一条真实检索结果。

        - 带 ``query_id``：人工已确认某候选，按 id 精确拉取并存入该用例。
        - 不带：先精确/子串匹配；命中则直接拉取，否则返回相似候选（不落库），
          ``matched=false`` + ``candidates[]`` 交前端让人确认。
        """
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        case = suite.by_id(cid)
        if case is None:
            raise HTTPException(404, "case not found")
        try:
            if body.query_id:
                live = pull_by_query_id(config, body.query_id)
                if live is None:
                    raise HTTPException(404, "未找到指定的查询记录（query_id 不存在）")
            else:
                live = pull_latest_for_question(config, case.question, domain=body.domain)
                if live is None:
                    candidates = search_similar_questions(
                        config, case.question, domain=body.domain, limit=5
                    )
                    return {"case_id": cid, "matched": False, "candidates": candidates}
        except ServingDBUnavailable as exc:
            raise HTTPException(503, str(exc))
        ranked = [
            RetrievedItem(rank=it.rank, text=it.text, source=it.source_path)
            for it in sorted(live.items, key=lambda x: x.rank)
        ]
        rset = store.get_retrieval_set(sid)
        if body.agent_name:
            rset.agent_name = body.agent_name
        rset.items[cid] = ranked
        store.save_retrieval_set(rset)
        return {
            "case_id": cid,
            "matched": True,
            "query_id": live.query_id,
            "matched_question": live.question,
            "queried_at": live.queried_at,
            "duration_ms": live.duration_ms,
            "source_domain": live.domain,
            "items": [it.model_dump() for it in ranked],
        }

    @app.get("/api/v1/suites/{sid}/retrieval:pull/stream")
    def pull_suite_retrieval_stream(
        sid: str,
        domain: str | None = None,
        agent_name: str | None = None,
        only_missing: bool = False,
    ):
        """批量取证 + SSE 实时进度：逐题从知识库（serving 库）拉最新一条真实检索。

        前端用 ``EventSource`` 订阅，事件字段 ``event`` 取值与 agent 跑批一致：
        ``start`` / ``case_start`` / ``case_done`` / ``case_error`` / ``done``。
        ``only_missing=true`` 时跳过已有检索片段的题；命中→落库，未命中→该题
        ``matched=false``（前端可单独补「相似候选确认」），单题异常不打断整批。
        """
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not suite.cases:
            raise HTTPException(400, "测试集为空，无题可取证")

        def _sse(obj: dict) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        def gen():
            rset = store.get_retrieval_set(sid)
            if agent_name:
                rset.agent_name = agent_name
            done_ids = {cid for cid, items in rset.items.items() if items}
            todo = [c for c in suite.cases if not (only_missing and c.id in done_ids)]
            skipped = len(suite.cases) - len(todo)
            yield _sse(
                {
                    "event": "start",
                    "total": len(suite.cases),
                    "todo": len(todo),
                    "skipped": skipped,
                }
            )
            ok = miss = err = 0
            for i, case in enumerate(todo):
                yield _sse(
                    {
                        "event": "case_start",
                        "i": i,
                        "n": len(todo),
                        "case_id": case.id,
                        "question": case.question,
                    }
                )
                try:
                    live = pull_latest_for_question(config, case.question, domain=domain)
                    if live is None:
                        miss += 1
                        yield _sse(
                            {
                                "event": "case_done",
                                "case_id": case.id,
                                "matched": False,
                                "retrieved_count": 0,
                                "items": [],
                            }
                        )
                        continue
                    ranked = [
                        RetrievedItem(rank=it.rank, text=it.text, source=it.source_path)
                        for it in sorted(live.items, key=lambda x: x.rank)
                    ]
                    rset.items[case.id] = ranked
                    store.save_retrieval_set(rset)
                    ok += 1
                    yield _sse(
                        {
                            "event": "case_done",
                            "case_id": case.id,
                            "matched": True,
                            "retrieved_count": len(ranked),
                            "items": [it.model_dump() for it in ranked],
                            "queried_at": live.queried_at,
                            "matched_question": live.question,
                        }
                    )
                except ServingDBUnavailable as exc:
                    err += 1
                    yield _sse(
                        {"event": "case_error", "case_id": case.id, "error": str(exc)}
                    )
                except Exception as exc:  # noqa: BLE001 - 单题失败不打断整批
                    err += 1
                    yield _sse(
                        {"event": "case_error", "case_id": case.id, "error": str(exc)}
                    )
            yield _sse(
                {
                    "event": "done",
                    "ok": ok,
                    "miss": miss,
                    "err": err,
                    "todo": len(todo),
                    "skipped": skipped,
                }
            )

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/suites/{sid}/cases/{cid}/retrieval:judge")
    def judge_case_retrieval(sid: str, cid: str):
        """对该用例已拉取的检索结果调用大模型评估（0–3 相关性），增量写入工作运行。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        case = suite.by_id(cid)
        if case is None:
            raise HTTPException(404, "case not found")
        rset = store.get_retrieval_set(sid)
        items = rset.for_case(cid)
        if not items:
            raise HTTPException(400, "请先「检索数据库」拉取该问题的检索数据再评估")
        result = orchestrator.judge_one_retrieval(
            client, case, items,
            facts=orchestrator.confirmed_gold_facts(store, case),  # D9：草稿不参与
        )

        rid = store.working_retrieval_run_id(sid)
        run = store.get_retrieval_run(rid)
        if run is None:
            run = RetrievalRun(
                run_id=rid,
                suite_id=sid,
                project_id=suite.project_id,
                agent_name=rset.agent_name,
                backend="eval-llm",
                k_values=sorted(config.retrieval_k) or [1, 3, 5, 10],
            )
        run.agent_name = rset.agent_name
        run.results = [r for r in run.results if r.case_id != cid]
        run.results.append(result)
        store.save_retrieval_run(run)
        # 逐题流程：每判一题就更新一条 partial 快照，全判完前换设备也能恢复进度
        m = compute_retrieval_metrics(run, scoring)
        mdict = _retrieval_metrics_dict(m)
        done = len(run.results) >= len(suite.cases) and len(suite.cases) > 0
        store.save_run_summary(
            _retrieval_summary(
                run, mdict, kind="perq", status="done" if done else "partial"
            )
        )
        return {"run_id": rid, "result": _retrieval_case_dict(result)}

    @app.get("/api/v1/suites/{sid}/retrieval/progress")
    def retrieval_progress(sid: str):
        """逐题工作流状态：每个用例是否已拉取/已评估 + 已有结果，供前端恢复。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        rset = store.get_retrieval_set(sid)
        rid = store.working_retrieval_run_id(sid)
        run = store.get_retrieval_run(rid)
        results = {r.case_id: r for r in (run.results if run else [])}
        cases = []
        pulled = judged = 0
        for c in suite.cases:
            items = rset.for_case(c.id)
            res = results.get(c.id)
            if items:
                pulled += 1
            if res is not None:
                judged += 1
            cases.append(
                {
                    "case_id": c.id,
                    "question": c.question,
                    "question_type": c.question_type.value,
                    "gold_count": len(orchestrator.gold_facts(c)),
                    "pulled": bool(items),
                    "pulled_count": len(items),
                    "items": [it.model_dump() for it in items],
                    "judged": res is not None,
                    "result": _retrieval_case_dict(res) if res is not None else None,
                }
            )
        return {
            "suite_id": sid,
            "run_id": rid,
            "agent_name": rset.agent_name,
            "total": len(suite.cases),
            "pulled": pulled,
            "judged": judged,
            "all_judged": judged == len(suite.cases) and len(suite.cases) > 0,
            "cases": cases,
        }

    @app.post("/api/v1/suites/{sid}/retrieval:report")
    def retrieval_final_report(sid: str):
        """逐题评估全部完成后，聚合工作运行生成最终检索层报告 + 指标。"""
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        rid = store.working_retrieval_run_id(sid)
        run = store.get_retrieval_run(rid)
        if run is None or not run.results:
            raise HTTPException(400, "尚无任何已评估的用例，无法生成报告")
        write_retrieval_reports(run, suite, store.reports_dir(run.project_id or "default"), scoring)
        m = compute_retrieval_metrics(run, scoring)
        mdict = _retrieval_metrics_dict(m)
        store.save_run_summary(_retrieval_summary(run, mdict, kind="perq", status="done"))
        return {"run_id": rid, "metrics": mdict}

    @app.get("/api/v1/retrieval-runs/{rid}/metrics")
    def retrieval_run_metrics(rid: str):
        run = store.get_retrieval_run(rid)
        if run is None:
            raise HTTPException(404, "retrieval run not found")
        return _retrieval_metrics_dict(compute_retrieval_metrics(run, scoring))

    @app.get("/api/v1/retrieval-runs/{rid}/report")
    def retrieval_run_report(rid: str, format: str = "html"):
        run = store.get_retrieval_run(rid)
        if run is None:
            raise HTTPException(404, "retrieval run not found")
        suite = store.get_suite(run.suite_id)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if format == "md":
            return PlainTextResponse(render_retrieval_markdown(run, suite, scoring))
        return HTMLResponse(render_retrieval_html(run, suite, scoring))

    # --- runs ---
    @app.get("/api/v1/runs/{rid}/metrics")
    def run_metrics(rid: str):
        run = store.get_run(rid)
        if run is None:
            raise HTTPException(404, "run not found")
        suite = store.get_suite(run.suite_id)
        m = compute_metrics(run, suite)
        return _metrics_dict(m)

    @app.get("/api/v1/runs/{rid}/report")
    def run_report(rid: str, format: str = "html"):
        run = store.get_run(rid)
        if run is None:
            raise HTTPException(404, "run not found")
        suite = store.get_suite(run.suite_id)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if format == "md":
            return PlainTextResponse(render_markdown(run, suite))
        return HTMLResponse(render_html(run, suite))

    # --- eval-web SPA ---
    @app.get("/")
    def index():
        idx = STATIC_DIR / "index.html"
        if idx.exists():
            return FileResponse(idx)
        return HTMLResponse("<h1>eval-api</h1><p>eval-web 静态资源未找到。</p>")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def run_server(host: str = "127.0.0.1", port: int = 8800, env_file: str | None = None):
    import uvicorn

    app = create_app(env_file)
    print(f"[serve] eval-api -> http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
