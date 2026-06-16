"""Configuration for the eval-api service.

Holds the storage root + per-type generation defaults + the eval-llm base URL.
No model credentials live here (those belong to eval-llm).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]


PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent  # runtime_eval/ subproject root
REPO_ROOT = PROJECT_DIR.parent


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _as_int_list(value: str | None, default: list[int]) -> list[int]:
    if not value:
        return list(default)
    out: list[int] = []
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            k = int(part)
        except ValueError:
            continue
        if k > 0 and k not in out:
            out.append(k)
    return sorted(out) or list(default)


@dataclass
class ApiConfig:
    """Resolved eval-api runtime configuration."""

    eval_llm_url: str = "http://localhost:8801"
    workspace_dir: Path = field(default_factory=lambda: PROJECT_DIR / "workspace")

    # --- 存储：唯一后端为 SQLite（本地单文件 DB），便于跨 run/case 聚合查询 ---
    db_path: Path | None = None  # None → workspace_dir/eval.db

    per_type: int = 3
    pass_threshold: float = 0.6
    request_timeout: int = 180

    # --- L2 答案对照层：参赛模型花名册（原始 EVAL_ROSTER JSON 文本，create_app 时解析）---
    # 空=用内置 Claude 三档（haiku/sonnet/opus 走 claude_cli，白嫖订阅）。
    roster_env: str = ""
    max_doc_chars: int = 24000
    retrieval_k: list[int] = field(default_factory=lambda: [1, 3, 5, 10])

    # --- serving DB (拉取真实查询日志做检索层评测；只读) ---
    serving_pg_host: str = ""
    serving_pg_port: int = 5432
    serving_pg_dbname: str = ""
    serving_pg_user: str = ""
    serving_pg_password: str = ""
    serving_pg_sslmode: str = "disable"
    # 逐题「检索数据库」按问题文本匹配日志时的最低 trigram 相似度（0~1）。
    # agent 常会改写问题（如「本最佳实践…」→「《xxx最佳实践》…」），精确/子串匹配
    # 命不中时，回退到 pg_trgm 相似度匹配，>= 此阈值才算命中。
    serving_match_threshold: float = 0.3

    # --- 单题综合分 S_q 与 PASS / 诊断（见 retrieval-scoring-system-design）---
    # S_q = w_find·Recall + w_rank·(0.5·MRR+0.5·NDCG)
    #       + w_quality·(α·Precision + (1−α)·ContextRecall)，在 K=score_k 上取值。
    score_w_find: float = 0.40
    score_w_rank: float = 0.25
    score_w_quality: float = 0.35
    score_quality_alpha: float = 0.40  # quality 内 Precision 占比，(1−α) 给 ContextRecall
    score_k: int = 10  # 综合分/PASS/诊断在哪个 K 上判定
    # PASS：Recall≥pass_recall 且 rank_first(0-based)≤pass_rank 且 ContextRecall≥pass_ctx_recall
    pass_recall: float = 0.5
    pass_rank: int = 3
    pass_ctx_recall: float = 0.8
    noisy_precision: float = 0.3  # Precision@K < 此值 → 高噪声桶
    # 难度权重（KB 聚合用）；评测集 difficulty 为 easy|normal|hard，normal 视作 medium。
    difficulty_weights: dict[str, float] = field(
        default_factory=lambda: {"easy": 1.0, "normal": 1.5, "medium": 1.5, "hard": 2.0}
    )

    def project_dir(self, project_id: str) -> Path:
        return self.workspace_dir / project_id

    def database_path(self) -> Path:
        """Resolved SQLite file path (defaults to workspace_dir/eval.db)."""

        return self.db_path or (self.workspace_dir / "eval.db")

    def serving_db_kwargs(self) -> dict[str, object] | None:
        """psycopg.connect kwargs for the serving DB, or None if not configured.

        Returns None (feature disabled) unless host + dbname + user are all set,
        so the rest of the framework runs with zero DB dependency by default.
        """

        if not (self.serving_pg_host and self.serving_pg_dbname and self.serving_pg_user):
            return None
        return {
            "host": self.serving_pg_host,
            "port": self.serving_pg_port,
            "dbname": self.serving_pg_dbname,
            "user": self.serving_pg_user,
            "password": self.serving_pg_password,
            "sslmode": self.serving_pg_sslmode,
        }


def load_config(env_file: str | os.PathLike[str] | None = None) -> ApiConfig:
    if load_dotenv is not None:
        candidates = (
            [Path(env_file)]
            if env_file is not None
            else [PROJECT_DIR / ".env", REPO_ROOT / ".env"]
        )
        # override=True: .env is the source of truth, so a stale exported var in
        # the shell / PyCharm run-config can't silently shadow it (see eval_llm).
        for candidate in candidates:
            if candidate.exists():
                load_dotenv(candidate, override=True)

    cfg = ApiConfig(
        eval_llm_url=os.getenv("EVAL_LLM_URL", "http://localhost:8801"),
        per_type=_as_int(os.getenv("EVAL_PER_TYPE"), 3),
        pass_threshold=_as_float(os.getenv("EVAL_PASS_THRESHOLD"), 0.6),
        request_timeout=_as_int(os.getenv("EVAL_REQUEST_TIMEOUT"), 180),
        roster_env=os.getenv("EVAL_ROSTER", ""),
        max_doc_chars=_as_int(os.getenv("EVAL_MAX_DOC_CHARS"), 24000),
        retrieval_k=_as_int_list(os.getenv("EVAL_RETRIEVAL_K"), [1, 3, 5, 10]),
        serving_pg_host=os.getenv("SERVING_PG_HOST", ""),
        serving_pg_port=_as_int(os.getenv("SERVING_PG_PORT"), 5432),
        serving_pg_dbname=os.getenv("SERVING_PG_DBNAME", ""),
        serving_pg_user=os.getenv("SERVING_PG_USER", ""),
        serving_pg_password=os.getenv("SERVING_PG_PASSWORD", ""),
        serving_pg_sslmode=os.getenv("SERVING_PG_SSLMODE", "disable"),
        serving_match_threshold=_as_float(os.getenv("SERVING_MATCH_THRESHOLD"), 0.3),
        score_w_find=_as_float(os.getenv("EVAL_SCORE_W_FIND"), 0.40),
        score_w_rank=_as_float(os.getenv("EVAL_SCORE_W_RANK"), 0.25),
        score_w_quality=_as_float(os.getenv("EVAL_SCORE_W_QUALITY"), 0.35),
        score_quality_alpha=_as_float(os.getenv("EVAL_SCORE_QUALITY_ALPHA"), 0.40),
        score_k=_as_int(os.getenv("EVAL_SCORE_K"), 10),
        pass_recall=_as_float(os.getenv("EVAL_PASS_RECALL"), 0.5),
        pass_rank=_as_int(os.getenv("EVAL_PASS_RANK"), 3),
        pass_ctx_recall=_as_float(os.getenv("EVAL_PASS_CTX_RECALL"), 0.8),
        noisy_precision=_as_float(os.getenv("EVAL_NOISY_PRECISION"), 0.3),
    )
    workspace_override = os.getenv("EVAL_WORKSPACE_DIR")
    if workspace_override:
        cfg.workspace_dir = Path(workspace_override)
    db_path_override = os.getenv("EVAL_DB_PATH")
    if db_path_override:
        cfg.db_path = Path(db_path_override)
    return cfg
