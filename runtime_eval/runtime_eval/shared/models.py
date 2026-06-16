"""Core data models shared across the three services.

The pipeline produces JSON artifacts that flow between stages and services:

    suites/<suite_id>.json       -> TestSuite      (generated via eval-llm)
    responses/<suite_id>.json    -> ResponseSet    (uploaded by human via eval-web)
    runs/<run_id>.json           -> EvalRun        (produced by judge orchestration)

Plus the project-scoped catalogue models added for the multi-service layout:

    Project   -> a tenant/workspace namespace owning documents and suites
    Document  -> an uploaded + parsed source file inside a project

Everything is a plain pydantic model so it round-trips to/from JSON cleanly and
can be served by the eval-api FastAPI app without extra serializers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QuestionType(str, Enum):
    """Question taxonomy aligned with the serving intent classifier so that
    report buckets map onto the system's own query-understanding categories.

    Ordered factoid → conceptual → procedural → constraint → troubleshooting →
    navigational; this is also the order report buckets are listed in.
    """

    FACTOID = "factoid"  # 事实型：参数取值、定义、阈值
    CONCEPTUAL = "conceptual"  # 概念型：是什么、原理、作用
    PROCEDURAL = "procedural"  # 操作型：如何配置、操作步骤
    CONSTRAINT = "constraint"  # 约束型：限制、前提、约束条件、取值边界
    TROUBLESHOOTING = "troubleshooting"  # 故障型：报错、异常、定位
    NAVIGATIONAL = "navigational"  # 导航型：在哪份文档/哪一节

    @classmethod
    def all(cls) -> list["QuestionType"]:
        return list(cls)

    @classmethod
    def coerce(cls, value: str) -> "QuestionType":
        """Map a raw string (with a few synonyms) onto a known type.

        Raises ``ValueError`` if the value is not a recognised type, so callers
        importing external suites get a clear, actionable error.
        """

        key = (value or "").strip().lower()
        aliases = {
            "fact": cls.FACTOID,
            "concept": cls.CONCEPTUAL,
            "procedure": cls.PROCEDURAL,
            "howto": cls.PROCEDURAL,
            "how_to": cls.PROCEDURAL,
            "limit": cls.CONSTRAINT,
            "limitation": cls.CONSTRAINT,
            "precondition": cls.CONSTRAINT,
            "troubleshoot": cls.TROUBLESHOOTING,
            "navigation": cls.NAVIGATIONAL,
            "nav": cls.NAVIGATIONAL,
        }
        try:
            return cls(key)
        except ValueError:
            if key in aliases:
                return aliases[key]
            raise ValueError(
                f"未知 question_type：{value!r}（允许："
                + "/".join(t.value for t in cls)
                + "）"
            )


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class SourceRef(BaseModel):
    """Where in the corpus the expected answer is grounded (the gold source)."""

    doc: str  # document-relative id / file path
    section: str | None = None  # heading / section title if known
    quote: str | None = None  # short supporting excerpt


class TestCase(BaseModel):
    id: str
    question: str
    question_type: QuestionType
    expected_answer: str
    key_points: list[str] = Field(default_factory=list)  # 评分要点（生成层裁判用）
    # --- retrieval-layer gold labels (检索层评测用) ---
    expected_evidence: list[str] = Field(default_factory=list)  # 黄金证据 nuggets/事实
    expected_entities: list[str] = Field(default_factory=list)  # 黄金关键实体
    source: SourceRef
    difficulty: str = "normal"  # easy | normal | hard
    notes: str | None = None


class TestSuite(BaseModel):
    suite_id: str
    project_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    backend: str = "unknown"  # provider used to generate (reported by eval-llm)
    name: str = ""  # 用户可读显示名（导入时填；空则前端回落到 corpus_files/backend）
    corpus_files: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    generation_usage: TokenUsage = Field(default_factory=TokenUsage)
    cases: list[TestCase] = Field(default_factory=list)

    def by_id(self, case_id: str) -> TestCase | None:
        return next((c for c in self.cases if c.id == case_id), None)


class AgentResponse(BaseModel):
    """One manually-uploaded answer from the被测 copilot agent."""

    case_id: str
    answer: str
    latency_ms: float | None = None  # 查询时长
    token_usage: TokenUsage = Field(default_factory=TokenUsage)  # agent 自身消耗
    raw: dict[str, Any] | None = None  # optional raw payload for audit


class ResponseSet(BaseModel):
    suite_id: str
    agent_name: str = "copilot-agent"
    created_at: str = Field(default_factory=_now_iso)
    responses: list[AgentResponse] = Field(default_factory=list)

    def by_case(self, case_id: str) -> AgentResponse | None:
        return next((r for r in self.responses if r.case_id == case_id), None)


class Verdict(str, Enum):
    CORRECT = "correct"  # 答案正确且完整
    PARTIAL = "partial"  # 部分正确 / 缺关键点
    INCORRECT = "incorrect"  # 错误或答非所问
    MISSING = "missing"  # 没有上传回答


class CaseResult(BaseModel):
    case_id: str
    question_type: QuestionType
    verdict: Verdict
    score: float  # 0.0 - 1.0
    rationale: str = ""
    covered_points: list[str] = Field(default_factory=list)
    missed_points: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    agent_token_usage: TokenUsage = Field(default_factory=TokenUsage)
    judge_token_usage: TokenUsage = Field(default_factory=TokenUsage)


class EvalRun(BaseModel):
    run_id: str
    suite_id: str
    project_id: str | None = None
    agent_name: str = "copilot-agent"
    created_at: str = Field(default_factory=_now_iso)
    backend: str = "unknown"
    pass_threshold: float = 0.6  # score >= threshold 计为通过
    results: list[CaseResult] = Field(default_factory=list)


# --- retrieval-layer evaluation (检索层评测) -------------------------------


class RetrievedItem(BaseModel):
    """One evidence item the被测 retriever returned, in rank order."""

    rank: int  # 1-based position in the retriever output
    text: str
    source: str | None = None  # 出处（章节/文档），可选


class RetrievalSet(BaseModel):
    """Manually-uploaded retrieved evidence, keyed by case id (ranked lists)."""

    suite_id: str
    agent_name: str = "retriever"
    created_at: str = Field(default_factory=_now_iso)
    items: dict[str, list[RetrievedItem]] = Field(default_factory=dict)

    def for_case(self, case_id: str) -> list[RetrievedItem]:
        return self.items.get(case_id, [])


class RetrievalCaseResult(BaseModel):
    """Raw per-case labels produced by the LLM relevance judge.

    Aggregate IR metrics (HitRate@K / Recall@K / MRR@K / NDCG@K / Context
    Precision / Context Recall) are computed deterministically from these labels,
    so multiple K can be reported off a single judge pass.
    """

    case_id: str
    question_type: QuestionType
    difficulty: str = "normal"  # easy | normal | hard，难度加权聚合用
    retrieved_count: int = 0
    gold_count: int = 0
    # grade per retrieved item, in rank order (0..3); >0 means relevant.
    item_grades: list[int] = Field(default_factory=list)
    # for each gold fact: 1-based rank of first item covering it, 0 if uncovered.
    gold_covered_at: list[int] = Field(default_factory=list)
    rationale: str = ""
    judge_token_usage: TokenUsage = Field(default_factory=TokenUsage)


class RetrievalRun(BaseModel):
    run_id: str
    suite_id: str
    project_id: str | None = None
    agent_name: str = "retriever"
    created_at: str = Field(default_factory=_now_iso)
    backend: str = "eval-llm"
    k_values: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])
    results: list[RetrievalCaseResult] = Field(default_factory=list)


# --- project-scoped catalogue (multi-service layout) -----------------------


class DocumentSection(BaseModel):
    """A parsed section of an uploaded document (heading + body)."""

    title: str | None = None
    text: str = ""


class Document(BaseModel):
    document_id: str
    project_id: str
    name: str  # original filename / logical name
    rel_path: str  # stable doc id used in SourceRef.doc
    created_at: str = Field(default_factory=_now_iso)
    char_count: int = 0
    sections: list[DocumentSection] = Field(default_factory=list)
    text: str = ""  # full parsed plain text fed to eval-llm

    def section_titles(self) -> list[str]:
        return [s.title for s in self.sections if s.title]


class Project(BaseModel):
    project_id: str
    name: str
    created_at: str = Field(default_factory=_now_iso)


# --- run summaries (评测档案：每次 run 的指标落库，前端从服务端读) ----------


class RunSummary(BaseModel):
    """一次评测 run 的指标快照，落进 SQLite 供"评估报告/看板"读取。

    设计：完整指标拍扁进 ``metrics`` dict（不同 layer 字段不同，宽松存）；
    几个常用列（layer/kind/status/k）提升为表列便于跨行筛选。逐题流程会先以
    ``status='partial'`` 写入、判完再覆盖为 ``'done'``，所以换设备也能恢复进度。
    """

    run_id: str  # 主键；逐题流程用稳定 id（如 rrun_<suite_id>）便于覆盖
    project_id: str | None = None
    suite_id: str
    layer: str = "retrieval"  # 'retrieval'（检索层）| 'response'（生成层）
    kind: str = "batch"  # 'perq'（逐题）| 'batch'（整批）| 'live'（即时检索）
    created_at: str = Field(default_factory=_now_iso)
    k: int | None = None  # 检索层报告的主 K（通过线参数）
    status: str = "done"  # 'partial'（跑了一半）| 'done'（已完成）
    metrics: dict[str, Any] = Field(default_factory=dict)  # 拍扁的指标快照


# --- gold library (黄金库 A4：可复用的人工标注) ---------------------------


class GoldRecord(BaseModel):
    """一条可复用的黄金标注，按问题指纹索引。

    批量拉取的相同/相似问题可直接命中黄金库，免去重复标注（A4）。指纹由问题文本
    归一化（去空白 + 小写）后取 SHA-1 前 16 位，跨 suite / 项目稳定。
    """

    fingerprint: str  # 主键：归一化问题的哈希
    project_id: str | None = None
    question: str
    question_type: QuestionType = QuestionType.FACTOID
    expected_answer: str = ""
    key_points: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)  # 黄金证据 nuggets
    expected_entities: list[str] = Field(default_factory=list)  # 黄金关键实体
    source: SourceRef | None = None
    difficulty: str = "normal"  # easy | normal | hard
    notes: str | None = None
    # 来源：manual（人工）| llm_generated（出题顺带）| imported（导入）。读旧数据给默认值。
    source_kind: str = "manual"
    # 状态：confirmed（已确认，参与打分）| draft（草稿，不参与打分，D9）。旧数据默认已确认。
    status: str = "confirmed"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    @staticmethod
    def make_fingerprint(question: str) -> str:
        """归一化问题文本 → 稳定指纹（去空白、小写、SHA-1 前 16 位）。"""

        import hashlib
        import re

        norm = re.sub(r"\s+", "", (question or "").strip().lower())
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

    def apply_to_case(self, case: "TestCase") -> "TestCase":
        """把黄金字段填充到一个用例上（批量拉取后命中复用时用）。"""

        case.expected_answer = self.expected_answer or case.expected_answer
        case.key_points = list(self.key_points) or case.key_points
        case.expected_evidence = list(self.expected_evidence) or case.expected_evidence
        case.expected_entities = list(self.expected_entities) or case.expected_entities
        if self.source is not None:
            case.source = self.source
        case.difficulty = self.difficulty or case.difficulty
        case.question_type = self.question_type
        return case
