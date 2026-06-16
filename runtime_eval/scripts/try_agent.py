"""手动验证：问一个问题 → claude -p 调用 MCP → 检索云核心网知识库 → 作答。

用法（在 runtime_eval 目录下）：
    python -m scripts.try_agent "什么是业务感知"
    python -m scripts.try_agent            # 不带参数则用默认问题

它会读 runtime_eval/.env 里的 EVAL_AGENT_* 配置，跑通后把
「最终答案 + 用了哪些工具 + 检索到的片段」打印出来，并存一份
try_agent_out.json（UTF-8，避免 Windows 控制台中文乱码）方便查看。
"""

from __future__ import annotations

import io
import json
import sys

# Windows 控制台默认 GBK，会把中文 print 成乱码；强制 stdout 用 UTF-8。
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from runtime_eval.eval_llm.agent_runner import run_agent
from runtime_eval.eval_llm.config import load_config


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "什么是业务感知"
    config = load_config()

    print(f"[1] 读取配置：provider={config.provider}")
    print(f"    MCP 配置文件 = {config.agent_mcp_config or '(未配置!)'}")
    print(f"    放行工具     = {config.agent_allowed_tools or '(不限制)'}")
    print(f"[2] 提问：{question}")
    print("[3] 正在让 claude 调 MCP 检索知识库并作答（首次拉起 MCP 可能稍慢）…\n")

    try:
        out = run_agent(config, question)
    except RuntimeError as exc:
        print(f"✗ 跑批失败：{exc}")
        return 1

    print("=" * 60)
    print("最终答案：")
    print(out["answer"] or "(空)")
    print("=" * 60)

    tool_calls = out.get("tool_calls") or []
    print(f"\n用了 {len(tool_calls)} 次工具，共 {out.get('num_turns', 0)} 轮：")
    for tc in tool_calls:
        print(f"  - {tc['name']}  query={tc.get('query') or ''!r}")

    items = out.get("retrieved_items") or []
    print(f"\n检索到 {len(items)} 条片段：")
    for it in items[:10]:
        src = it.get("source") or ""
        text = (it.get("text") or "").replace("\n", " ")
        print(f"  #{it.get('rank')}: {text[:80]}…   出处={src}")

    usage = out.get("usage") or {}
    print(f"\ntoken 消耗：{usage.get('total_tokens', 0)}")

    with open("try_agent_out.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n完整结果已写入 try_agent_out.json（中文不乱码）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
