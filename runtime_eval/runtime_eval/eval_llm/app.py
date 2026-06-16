"""eval-llm FastAPI app: the large-model service.

    GET  /health           -> {status, provider}
    POST /generate-cases   -> {cases:[...], usage:{...}}
    POST /judge            -> {verdict, score, rationale, covered_points,
                               missed_points, usage:{...}}
    POST /judge-retrieval  -> {item_grades:[...], gold_covered_at:[...],
                               rationale, usage:{...}}

All provider selection + credentials live behind this service.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import agent_runner, service
from .config import LLMConfig, load_config
from .providers import build_provider
from .providers.base import LLMProvider
from .providers.factory import build_answer_provider


class GenerateCasesIn(BaseModel):
    document_text: str
    doc_ref: str
    types: list[str] | None = None
    per_type: int = 3
    persona: str | None = None


class JudgeIn(BaseModel):
    question: str
    question_type: str
    expected_answer: str
    key_points: list[str] = Field(default_factory=list)
    agent_answer: str = ""


class JudgeRetrievalIn(BaseModel):
    question: str
    expected_answer: str = ""
    gold_facts: list[str] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)


class RunAgentIn(BaseModel):
    """让 claude -p + MCP 自动检索知识库并回答单个问题。"""

    question: str
    system: str | None = None
    route: str = "kb"  # kb=用库 / closed_book=闭卷 / web=联网


class AnswerIn(BaseModel):
    """L2 作答：花名册里某个模型只读固定证据包整合一版最终答案。"""

    question: str
    evidence: list[str] = Field(default_factory=list)
    channel: str = "claude_cli"  # 调用通道：claude_cli / deepseek / minimax ...
    model: str = ""  # 该通道下点名的具体档位/模型名（空=用通道默认）


class JudgeAnswerIn(BaseModel):
    """L2 对照判：把模型答案 A 跟黄金最终答案 G 比，出覆盖/准确/矛盾。"""

    question: str
    expected_answer: str
    key_points: list[str] = Field(default_factory=list)
    answer: str = ""


def create_app(
    env_file: str | None = None,
    *,
    config: LLMConfig | None = None,
    provider: LLMProvider | None = None,
    answer_provider_factory=None,
) -> FastAPI:
    config = config or load_config(env_file)
    injected_provider = provider is not None
    provider = provider or build_provider(config)
    # L2 作答按「通道+模型」现造 provider。生产：按 channel 真去切换厂商
    # （build_answer_provider）。测试：注入了固定 provider 时直接复用它作答，
    # 不真起子进程/不真发 HTTP。也可显式传 answer_provider_factory 断言派发路由。
    if answer_provider_factory is None:
        if injected_provider:
            answer_provider_factory = lambda _cfg, _ch, _m: provider  # noqa: E731
        else:
            answer_provider_factory = build_answer_provider
    app = FastAPI(title="eval-llm", version="2.0.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "provider": provider.name}

    @app.post("/generate-cases")
    def generate_cases(body: GenerateCasesIn):
        try:
            return service.generate_cases(
                provider,
                document_text=body.document_text,
                doc_ref=body.doc_ref,
                types=body.types,
                per_type=body.per_type,
                persona=body.persona,
                temperature=config.temperature,
                max_tokens=config.anthropic_max_tokens,
            )
        except RuntimeError as exc:
            # 把 claude/底层模型的真实报错透传成可读的 502，避免上层只看到泛化 500。
            raise HTTPException(502, f"出题调用大模型失败：{exc}")

    @app.post("/generate-cases/preview")
    def generate_cases_preview(body: GenerateCasesIn):
        """只渲染「将发给大模型的提示词」，不真正调用模型，便于前端排查/确认。"""
        return service.preview_generate_prompt(
            document_text=body.document_text,
            doc_ref=body.doc_ref,
            types=body.types,
            per_type=body.per_type,
            persona=body.persona,
        )

    @app.post("/judge")
    def judge(body: JudgeIn):
        try:
            return service.judge_case(
                provider,
                question=body.question,
                question_type=body.question_type,
                expected_answer=body.expected_answer,
                key_points=body.key_points,
                agent_answer=body.agent_answer,
            )
        except RuntimeError as exc:
            raise HTTPException(502, f"评分调用大模型失败：{exc}")

    @app.post("/judge-retrieval")
    def judge_retrieval(body: JudgeRetrievalIn):
        try:
            return service.judge_retrieval(
                provider,
                question=body.question,
                expected_answer=body.expected_answer,
                gold_facts=body.gold_facts,
                items=body.items,
                temperature=config.temperature,
                max_tokens=config.anthropic_max_tokens,
            )
        except RuntimeError as exc:
            raise HTTPException(502, f"检索评分调用大模型失败：{exc}")

    @app.post("/run-agent")
    def run_agent(body: RunAgentIn):
        try:
            return agent_runner.run_agent(
                config, body.question, system=body.system, route=body.route
            )
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/answer")
    def answer(body: AnswerIn):
        try:
            answer_provider = answer_provider_factory(config, body.channel, body.model)
        except ValueError as exc:
            # 通道没配凭据/type 不支持等：配置类错误，回 400 让前端能看清。
            raise HTTPException(400, str(exc))
        try:
            return service.answer_case(
                answer_provider,
                question=body.question,
                evidence=body.evidence,
                temperature=config.temperature,
            )
        except RuntimeError as exc:
            raise HTTPException(502, f"作答调用大模型失败（通道 {body.channel}）：{exc}")

    @app.post("/judge-answer")
    def judge_answer(body: JudgeAnswerIn):
        try:
            return service.judge_answer_match(
                provider,
                question=body.question,
                expected_answer=body.expected_answer,
                key_points=body.key_points,
                answer=body.answer,
            )
        except RuntimeError as exc:
            raise HTTPException(502, f"答案对照判调用大模型失败：{exc}")

    return app


def run_server(host: str = "127.0.0.1", port: int = 8801, env_file: str | None = None):
    import uvicorn

    app = create_app(env_file)
    print(f"[serve] eval-llm -> http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
