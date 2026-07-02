"""API configuration and provider/service wiring."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path

from telecom_eval.judges.providers.claude_cli import ClaudeCLIConfig
from telecom_eval.judges.providers.eval_roster import EvalRosterConfig, list_eval_roster_models
from telecom_eval.judges.providers.factory import build_provider
from telecom_eval.judges.service import JudgeService

DEFAULT_DB_PATH = Path("data/evaluation/eval.db")
DEFAULT_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "runtime.json"


@dataclass
class EvalApiConfig:
    db_path: Path
    judge_provider: str = "mock"
    claude_bin: str = "claude"
    claude_model: str = ""
    claude_timeout: int = 600
    claude_extra_args: str = ""
    claude_proxy: str = ""
    eval_roster_json: str = ""
    eval_answer_channels_json: str = ""
    eval_roster_timeout: int = 120
    eval_judge_model_id: str = ""
    eval_answer_model_id: str = ""
    # 被测检索系统：fake（离线默认）或 http（真实 /api/v1/search）。
    subject_provider: str = "fake"
    search_base_url: str = "http://121.89.90.178:8081"
    search_domain: str = "cloud_core_network"
    search_timeout: float = 30.0
    search_trust_env: bool = False
    local_mock_corpus_path: Path = Path("data/knowledge_base/IP/测试集.md")
    runner_max_concurrent_runs: int = 2


def _load_runtime_config() -> dict:
    path = Path(os.environ.get("TELECOM_EVAL_CONFIG", str(DEFAULT_RUNTIME_CONFIG_PATH)))
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _load_dotenv() -> None:
    explicit_env = set(os.environ)
    values: dict[str, str] = {}
    values.update(_read_dotenv(Path(".env")))
    values.update(_read_dotenv(Path("telecom_eval") / ".env"))
    for key, value in values.items():
        if key not in explicit_env:
            os.environ[key] = value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_config(db_path: str | Path | None = None) -> EvalApiConfig:
    _load_dotenv()
    runtime = _load_runtime_config()
    api_config = runtime.get("api", {})
    subject_config = runtime.get("subject", {})
    judge_config = runtime.get("judge", {})
    runner_config = runtime.get("runner", {})
    claude_config = judge_config.get("claude_cli", {})
    resolved = Path(db_path) if db_path is not None else Path(
        os.environ.get("TELECOM_EVAL_DB_PATH", str(api_config.get("db_path") or DEFAULT_DB_PATH))
    )
    return EvalApiConfig(
        db_path=resolved,
        judge_provider=os.environ.get("TELECOM_EVAL_JUDGE_PROVIDER", judge_config.get("provider", "mock")),
        claude_bin=os.environ.get("TELECOM_EVAL_CLAUDE_BIN", claude_config.get("bin", "claude")),
        claude_model=os.environ.get("TELECOM_EVAL_CLAUDE_MODEL", claude_config.get("model", "")),
        claude_timeout=int(os.environ.get("TELECOM_EVAL_CLAUDE_TIMEOUT", str(claude_config.get("timeout", 600)))),
        claude_extra_args=os.environ.get("TELECOM_EVAL_CLAUDE_EXTRA_ARGS", claude_config.get("extra_args", "")),
        claude_proxy=os.environ.get("TELECOM_EVAL_CLAUDE_PROXY", claude_config.get("proxy", "")),
        eval_roster_json=os.environ.get("EVAL_ROSTER", ""),
        eval_answer_channels_json=os.environ.get("EVAL_ANSWER_CHANNELS", ""),
        eval_roster_timeout=int(os.environ.get("EVAL_LLM_TIMEOUT", "120")),
        eval_judge_model_id=os.environ.get("EVAL_JUDGE_MODEL_ID", ""),
        eval_answer_model_id=os.environ.get("EVAL_ANSWER_MODEL_ID", ""),
        subject_provider=os.environ.get("TELECOM_EVAL_SUBJECT_PROVIDER", subject_config.get("provider", "fake")),
        search_base_url=os.environ.get("TELECOM_EVAL_SEARCH_URL", subject_config.get("search_base_url", "http://121.89.90.178:8081")),
        search_domain=os.environ.get("TELECOM_EVAL_SEARCH_DOMAIN", subject_config.get("search_domain", "cloud_core_network")),
        search_timeout=float(os.environ.get("TELECOM_EVAL_SEARCH_TIMEOUT", str(subject_config.get("search_timeout", 30)))),
        search_trust_env=_env_bool("TELECOM_EVAL_SEARCH_TRUST_ENV", bool(subject_config.get("trust_env", False))),
        local_mock_corpus_path=Path(
            os.environ.get(
                "TELECOM_EVAL_LOCAL_MOCK_CORPUS",
                str(subject_config.get("local_mock_corpus_path", "data/knowledge_base/IP/测试集.md")),
            )
        ),
        runner_max_concurrent_runs=max(
            1,
            int(
                os.environ.get(
                    "TELECOM_EVAL_MAX_CONCURRENT_RUNS",
                    str(runner_config.get("max_concurrent_runs", 2)),
                )
            ),
        ),
    )


def build_retrieval_adapter_factory(config: EvalApiConfig):
    """返回 subject_id -> RetrievalSubjectAdapter 的工厂；fake 时返回 None（用默认 Fake）。"""
    if config.subject_provider == "local_mock":
        from telecom_eval.subjects.local_mock import LocalMarkdownRetrievalAdapter, LocalMarkdownSegmentStore

        store = LocalMarkdownSegmentStore(config.local_mock_corpus_path)

        def factory(subject_id: str, request=None) -> LocalMarkdownRetrievalAdapter:
            return LocalMarkdownRetrievalAdapter(subject_id=subject_id, store=store)

        return factory

    if config.subject_provider != "http":
        return None

    from telecom_eval.subjects.http import HttpRetrievalAdapter, HttpRetrievalConfig

    def factory(subject_id: str, request=None) -> HttpRetrievalAdapter:
        search_path = getattr(request, "subject_search_path", None) or "/api/v1/search"
        http_config = HttpRetrievalConfig(
            base_url=config.search_base_url,
            search_path=search_path,
            domain=config.search_domain,
            timeout=config.search_timeout,
            trust_env=config.search_trust_env,
        )
        return HttpRetrievalAdapter(subject_id=subject_id, config=http_config)

    return factory


def build_segment_resolver(config: EvalApiConfig):
    if config.subject_provider == "local_mock":
        from telecom_eval.subjects.local_mock import LocalMarkdownSegmentStore

        store = LocalMarkdownSegmentStore(config.local_mock_corpus_path)
        return store.resolve
    return None


def build_search_fn(config: EvalApiConfig):
    """返回 (query, domain, top_k) -> list[evidence dict] 的取证函数。

    案例编辑器挑原文用真实语料，因此始终走 HTTP 检索（用 config.search_* 配置），
    与评估用的 subject_provider 解耦。检索服务不可达时返回空列表。
    """
    from telecom_eval.subjects.http import HttpRetrievalAdapter, HttpRetrievalConfig

    adapter = HttpRetrievalAdapter(
        subject_id="authoring",
        config=HttpRetrievalConfig(
            base_url=config.search_base_url,
            domain=config.search_domain,
            timeout=config.search_timeout,
            trust_env=config.search_trust_env,
        ),
    )

    def search(query: str, domain: str | None = None, top_k: int = 10) -> list[dict]:
        items = adapter.search(query, domain=domain)
        return [item.model_dump() for item in items[: max(1, top_k)]]

    return search


def _roster_config(config: EvalApiConfig, *, preferred_id: str = "") -> EvalRosterConfig:
    _load_dotenv()
    return EvalRosterConfig(
        roster_json=config.eval_roster_json or os.environ.get("EVAL_ROSTER", ""),
        channels_json=config.eval_answer_channels_json or os.environ.get("EVAL_ANSWER_CHANNELS", ""),
        timeout=config.eval_roster_timeout,
        preferred_id=preferred_id,
    )


def available_llm_models(config: EvalApiConfig) -> list[dict[str, object]]:
    _load_dotenv()
    try:
        models = list_eval_roster_models(
            config.eval_roster_json or os.environ.get("EVAL_ROSTER", ""),
            config.eval_answer_channels_json or os.environ.get("EVAL_ANSWER_CHANNELS", ""),
        )
    except RuntimeError:
        return []
    return [
        {
            **model,
            "supports_answer": True,
            "supports_judge": True,
        }
        for model in models
    ]


def select_default_model_ids(config: EvalApiConfig) -> dict[str, str | None]:
    models = available_llm_models(config)
    ids = [str(model["id"]) for model in models]
    judge_id = config.eval_judge_model_id if config.eval_judge_model_id in ids else (ids[0] if ids else None)
    answer_id = config.eval_answer_model_id if config.eval_answer_model_id in ids else None
    if answer_id is None:
        answer_id = next((model_id for model_id in ids if model_id != judge_id), None)
    if answer_id is None:
        answer_id = judge_id
    return {"answer_model_id": answer_id, "judge_model_id": judge_id}


def build_model_catalog(config: EvalApiConfig) -> dict:
    defaults = select_default_model_ids(config)
    return {
        "models": available_llm_models(config),
        "default_answer_model_id": defaults["answer_model_id"],
        "default_judge_model_id": defaults["judge_model_id"],
    }


def build_eval_roster_provider(config: EvalApiConfig, *, model_id: str | None = None):
    provider = build_provider(
        "eval_roster",
        _roster_config(config, preferred_id=model_id or config.eval_judge_model_id),
    )
    return provider, getattr(provider, "model_name", None)


def build_judge_service(config: EvalApiConfig, store, *, model_id: str | None = None) -> JudgeService:
    model = None
    if model_id or config.judge_provider == "eval_roster":
        provider, model = build_eval_roster_provider(config, model_id=model_id or config.eval_judge_model_id)
    elif config.judge_provider == "claude_cli":
        provider = build_provider(
            "claude_cli",
            ClaudeCLIConfig(
                bin=config.claude_bin,
                model=config.claude_model,
                timeout=config.claude_timeout,
                extra_args=config.claude_extra_args,
                proxy=config.claude_proxy,
            ),
        )
        model = config.claude_model or None
    else:
        provider = build_provider("mock")
    return JudgeService(store=store, provider=provider, model=model)


def build_answer_adapter_factory(config: EvalApiConfig):
    from telecom_eval.subjects.llm_answer import EvidenceGroundedLLMAnswerAdapter

    if not available_llm_models(config):
        return None

    def factory(subject_id: str, request=None):
        model_id = getattr(request, "answer_model_id", None) or select_default_model_ids(config)["answer_model_id"]
        if not model_id:
            return None
        provider, model = build_eval_roster_provider(config, model_id=model_id)
        return EvidenceGroundedLLMAnswerAdapter(subject_id=subject_id, provider=provider, model=model or model_id)

    return factory


def build_judge_service_factory(config: EvalApiConfig, store):
    def factory(model_id: str | None = None) -> JudgeService:
        return build_judge_service(config, store, model_id=model_id)

    return factory
