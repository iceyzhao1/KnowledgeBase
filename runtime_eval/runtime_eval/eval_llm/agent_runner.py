"""Agent 跑批：用 ``claude -p`` + 现成 MCP 自动检索知识库并作答。

把测试集里的每个问题丢给已登录的 claude CLI，挂上用户自带的 MCP（``--mcp-config``）
让 claude 自己调用工具去检索知识库，最后给出回答。这样就不必人工逐题「检索数据库」。

用 ``--output-format stream-json --verbose`` 跑，claude 会把每一步（调用了哪个工具、
工具返回了什么、最终答案）以「一行一个 JSON 事件」的形式吐出来。我们解析这些事件，
既拿到 **最终答案**（应用层 L3），也拿到 **检索片段**（检索层 L1）。

``parse_stream_json`` 是纯函数（不碰子进程），方便单测；``run_agent`` 负责真正起进程。
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field

from ..shared.models import TokenUsage
from .config import LLMConfig
from .providers.claude_cli import clean_child_env, resolve_claude_bin, wrap_for_windows

# 三条评测路：kb=用知识库(C) / closed_book=闭卷裸答(A) / web=联网搜索(B)。
ROUTE_KB = "kb"
ROUTE_CLOSED_BOOK = "closed_book"
ROUTE_WEB = "web"

# 没配 EVAL_AGENT_SYSTEM 时用的默认系统提示：指导 agent 先检索再作答。
DEFAULT_AGENT_SYSTEM = (
    "你是一个严格基于知识库作答的助手。回答用户问题前，必须先调用可用的检索工具"
    "（MCP）从知识库里查找相关资料，再依据查到的内容作答。"
    "若知识库中没有相关信息，请如实说明「知识库中未找到相关内容」，不要编造。"
    "回答使用简洁中文。"
)

# 闭卷路（baseline A）：禁掉任何工具，只能靠模型自身知识裸答。
CLOSED_BOOK_SYSTEM = (
    "你是一个知识渊博的助手。请仅凭你自身已有的知识直接回答用户问题，"
    "你没有任何检索工具，也不能查阅外部资料或知识库。"
    "若你不确定或不知道答案，请如实说明，不要编造。回答使用简洁中文。"
)

# 联网路（baseline B）：用 claude 内置 WebSearch 工具查公网。
WEB_SYSTEM = (
    "你是一个可以联网搜索的助手。回答用户问题前，请先用 WebSearch 工具检索网络资料，"
    "再依据查到的内容作答。若搜不到相关信息，请如实说明，不要编造。回答使用简洁中文。"
)


def _default_system(route: str) -> str:
    if route == ROUTE_CLOSED_BOOK:
        return CLOSED_BOOK_SYSTEM
    if route == ROUTE_WEB:
        return WEB_SYSTEM
    return DEFAULT_AGENT_SYSTEM


@dataclass
class ToolCall:
    """agent 一次工具调用：调用了什么、查询词是什么、返回了什么、解析出的片段。"""

    name: str
    input: dict = field(default_factory=dict)
    query: str = ""
    result_text: str = ""
    items: list[dict] = field(default_factory=list)


@dataclass
class AgentRunResult:
    """一道题跑完 agent 后的结构化结果。"""

    answer: str = ""
    retrieved_items: list[dict] = field(default_factory=list)  # [{rank,text,source}]
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    num_turns: int = 0
    is_error: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "retrieved_items": self.retrieved_items,
            "tool_calls": [
                {
                    "name": tc.name,
                    "input": tc.input,
                    "query": tc.query,
                    "result_text": tc.result_text,
                    "items": tc.items,
                }
                for tc in self.tool_calls
            ],
            "usage": self.usage.model_dump(),
            "num_turns": self.num_turns,
            "is_error": self.is_error,
            "error": self.error,
        }


# --- 解析 stream-json 事件 -------------------------------------------------


def _flatten_tool_result(content: object) -> str:
    """把工具返回的 content 摊平成纯文本。

    content 可能是字符串，或 ``[{type:text,text:...}, ...]`` 这样的块列表。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
                elif "text" in block and block.get("text"):
                    parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _extract_query(tool_input: object) -> str:
    """从工具入参里挑出最像「查询词」的字段。"""

    if not isinstance(tool_input, dict):
        return ""
    for key in ("query", "q", "question", "text", "keyword", "keywords", "search"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def extract_items(text: str) -> list[dict]:
    """把一段工具返回文本解析成检索片段列表 ``[{text, source}]``。

    优先按 JSON 解析（命中 results/items/chunks/documents/data 等常见结构）；
    解析不出结构时，退化为按空行切分的纯文本片段；再不行就整段作为一条。
    """

    text = (text or "").strip()
    if not text:
        return []

    parsed = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None

    if parsed is not None:
        rows = _rows_from_json(parsed)
        if rows:
            return rows

    # 退化：按空行切分成多段；没有空行就整段一条。
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    if len(chunks) > 1:
        return [{"text": c, "source": None} for c in chunks]
    return [{"text": text, "source": None}]


def _rows_from_json(parsed: object) -> list[dict]:
    """从已解析的 JSON 里抽出片段列表。"""

    seq: list | None = None
    if isinstance(parsed, list):
        seq = parsed
    elif isinstance(parsed, dict):
        for key in ("results", "items", "chunks", "documents", "data", "hits", "matches"):
            val = parsed.get(key)
            if isinstance(val, list):
                seq = val
                break
        if seq is None:
            # 单个对象当作一条
            row = _row_from_obj(parsed)
            return [row] if row else []

    if seq is None:
        return []
    rows: list[dict] = []
    for el in seq:
        row = _row_from_obj(el)
        if row:
            rows.append(row)
    return rows


_TEXT_KEYS = ("text", "content", "chunk", "snippet", "passage", "body", "page_content")
# 文档级出处优先（先认这些再退而求其次用 title/section）。
_DOC_KEYS = (
    "source", "source_path", "path", "doc", "document",
    "documentKey", "document_key", "relativePath", "relative_path", "url", "file",
)
_TITLE_KEYS = ("title", "section")
# 出处可能藏在这些子字典里（云核心网 MCP 的 search_knowledge 返回即如此）。
_SOURCE_SUBDICTS = ("sourceRefs", "source_refs", "citation", "metadata")


def _pick(d: dict, keys) -> str | None:
    for key in keys:
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _row_from_obj(obj: object) -> dict | None:
    """把单个检索元素映射成 ``{text, source}``。

    兼容 cloud_core_network MCP 的 ``search_knowledge`` 返回：item 顶层有 ``text``，
    但文档出处在 ``sourceRefs.document_key`` 等子字典里，故先找文档级出处，
    再退回 item 的 ``title``/``section``。
    """

    if isinstance(obj, str):
        s = obj.strip()
        return {"text": s, "source": None} if s else None
    if not isinstance(obj, dict):
        return None
    text = _pick(obj, _TEXT_KEYS)
    if not text:
        return None
    # 1) 顶层文档级出处
    source = _pick(obj, _DOC_KEYS)
    # 2) 子字典里的文档级出处
    if source is None:
        for sub in _SOURCE_SUBDICTS:
            d = obj.get(sub)
            if isinstance(d, dict):
                source = _pick(d, _DOC_KEYS)
                if source:
                    break
    # 3) 退回标题/小节名
    if source is None:
        source = _pick(obj, _TITLE_KEYS)
    return {"text": text, "source": source}


def _usage_from_result(raw: object) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    prompt = (
        int(raw.get("input_tokens", 0) or 0)
        + int(raw.get("cache_creation_input_tokens", 0) or 0)
        + int(raw.get("cache_read_input_tokens", 0) or 0)
    )
    completion = int(raw.get("output_tokens", 0) or 0)
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def parse_stream_json(lines: list[str] | str) -> AgentRunResult:
    """解析 ``claude -p --output-format stream-json --verbose`` 的输出。

    纯函数：接收「整段 stdout」或「已切好的行列表」，返回结构化结果。
    """

    if isinstance(lines, str):
        raw_lines = lines.splitlines()
    else:
        raw_lines = lines

    events: list[dict] = []
    for line in raw_lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(ev, dict):
            events.append(ev)

    # tool_use 块按 id 暂存；tool_result 回来后配对。
    pending: dict[str, ToolCall] = {}
    order: list[str] = []
    assistant_text_parts: list[str] = []
    result = AgentRunResult()

    for ev in events:
        etype = ev.get("type")
        if etype == "assistant":
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    assistant_text_parts.append(str(block["text"]))
                elif btype == "tool_use":
                    tid = str(block.get("id") or f"tool_{len(order)}")
                    tinput = block.get("input") if isinstance(block.get("input"), dict) else {}
                    tc = ToolCall(
                        name=str(block.get("name") or "tool"),
                        input=tinput,
                        query=_extract_query(tinput),
                    )
                    pending[tid] = tc
                    order.append(tid)
        elif etype == "user":
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    tid = str(block.get("tool_use_id") or "")
                    tc = pending.get(tid)
                    if tc is None:
                        continue
                    rtext = _flatten_tool_result(block.get("content"))
                    tc.result_text = rtext
                    tc.items = extract_items(rtext)
        elif etype == "result":
            if ev.get("result"):
                result.answer = str(ev.get("result"))
            result.usage = _usage_from_result(ev.get("usage"))
            result.num_turns = int(ev.get("num_turns", 0) or 0)
            subtype = str(ev.get("subtype") or "")
            if ev.get("is_error") or subtype not in ("", "success"):
                result.is_error = True
                # 把失败子类型 + 错误文本记下来，便于上层报准原因。
                err = subtype or "error"
                detail = ev.get("error") or ev.get("result") or ""
                result.error = f"{err}：{detail}".strip("：") if detail else err

    # 最终答案：优先 result 事件，否则用累积的 assistant 文本兜底。
    if not result.answer and assistant_text_parts:
        result.answer = "\n".join(assistant_text_parts).strip()

    # 汇总工具调用 + 合并检索片段（按文本去重，保留首次顺序，重排 rank）。
    tool_calls = [pending[tid] for tid in order]
    result.tool_calls = tool_calls
    merged: list[dict] = []
    seen: set[str] = set()
    for tc in tool_calls:
        for it in tc.items:
            key = (it.get("text") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append({"rank": len(merged) + 1, "text": key, "source": it.get("source")})
    result.retrieved_items = merged
    return result


# --- 真正起进程跑 agent ----------------------------------------------------


def _build_argv(config: LLMConfig, resolved: str, route: str = ROUTE_KB) -> list[str]:
    argv = [
        resolved,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if route == ROUTE_KB:
        # 用库：挂用户自带 MCP + 放行配置里的检索工具。
        argv += ["--mcp-config", config.agent_mcp_config]
        if config.agent_allowed_tools.strip():
            argv += ["--allowedTools", config.agent_allowed_tools.strip()]
    elif route == ROUTE_WEB:
        # 联网：只放行内置 WebSearch，不挂 MCP。
        argv += ["--allowedTools", "WebSearch"]
    # closed_book：不挂 MCP、不放行任何工具 —— claude 只能裸答。
    if config.agent_permission_mode.strip():
        argv += ["--permission-mode", config.agent_permission_mode.strip()]
    if config.agent_model.strip():
        argv += ["--model", config.agent_model.strip()]
    if config.agent_max_turns and config.agent_max_turns > 0:
        argv += ["--max-turns", str(config.agent_max_turns)]
    if config.agent_extra_args.strip():
        argv += shlex.split(config.agent_extra_args)
    return argv


def run_agent(
    config: LLMConfig,
    question: str,
    *,
    system: str | None = None,
    route: str = ROUTE_KB,
) -> dict:
    """对单个问题跑一次 agent，返回 ``AgentRunResult.to_dict()``。

    ``route`` 选评测路：``kb`` 用知识库（默认，需 ``EVAL_AGENT_MCP_CONFIG``）、
    ``closed_book`` 闭卷裸答（不挂工具）、``web`` 联网搜索。
    仅 ``kb`` 路要求配 MCP，否则抛 ``RuntimeError``。
    """

    question = (question or "").strip()
    if not question:
        raise RuntimeError("问题为空，无法跑 agent")
    if route == ROUTE_KB and not config.agent_mcp_config.strip():
        raise RuntimeError(
            "未配置 MCP：请在 .env 设置 EVAL_AGENT_MCP_CONFIG 指向你的 MCP 配置文件，"
            "才能让 claude 自动检索知识库。"
        )

    resolved = resolve_claude_bin(config.claude_cli_bin or "claude")
    argv = wrap_for_windows(resolved, _build_argv(config, resolved, route))

    # 显式 system 最优先；其次仅 kb 路回退到 config 里那条「先查 MCP」的提示；
    # closed_book/web 路用各自的 route 默认提示（避免误导模型去用不存在的工具）。
    config_default = config.agent_system_prompt if route == ROUTE_KB else ""
    sys_prompt = (system if system is not None else config_default) or _default_system(route)
    prompt = f"{sys_prompt.strip()}\n\n问题：{question}" if sys_prompt.strip() else question

    try:
        proc = subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=tempfile.gettempdir(),
            env=clean_child_env(proxy=config.claude_cli_proxy),
            timeout=config.agent_timeout,
        )
    except FileNotFoundError as exc:  # pragma: no cover - PATH race
        raise RuntimeError(f"无法启动 claude CLI：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"agent 调用超时（>{config.agent_timeout}s），可调大 EVAL_AGENT_TIMEOUT。"
        ) from exc

    # 退出码非 0：claude 可能已吐了 stream-json（含 result 终态事件），
    # 先解析它拿到真正的失败原因（如 error_max_turns / error_during_execution），
    # 这比直接截 stdout 前 500 字（往往是没用的 init 事件）有用得多。
    if proc.returncode != 0:
        parsed = parse_stream_json(proc.stdout)
        reason = parsed.error or ""
        # 没有 result 事件时，回退到 stderr / stdout 尾部（更可能含真正报错）。
        if not reason:
            tail = (proc.stderr or "").strip() or (proc.stdout or "").strip()[-500:]
            reason = tail or "无输出"
        # 即便报错，若已经检索到片段也带回去，方便诊断「检索成功但后续失败」。
        raise RuntimeError(
            f"claude CLI 退出码 {proc.returncode}：{reason}"
        )

    result = parse_stream_json(proc.stdout)
    if result.is_error and not result.answer:
        raise RuntimeError(f"agent 返回错误：{result.error or '未知'}")
    return result.to_dict()
