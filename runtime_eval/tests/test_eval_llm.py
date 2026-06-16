"""eval-llm service tests using the deterministic mock provider + TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient

from runtime_eval.eval_llm.app import create_app
from runtime_eval.eval_llm.config import LLMConfig
from runtime_eval.eval_llm.providers.mock import MockProvider


def _client() -> TestClient:
    config = LLMConfig(provider="mock")
    app = create_app(config=config, provider=MockProvider(config))
    return TestClient(app)


def test_health():
    r = _client().get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mock"


def test_generate_cases():
    r = _client().post(
        "/generate-cases",
        json={
            "document_text": "APN 配置说明：默认 APN 为 cmnet，超时阈值 30s。",
            "doc_ref": "sample/APN.md",
            "types": ["factoid", "procedural"],
            "per_type": 1,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cases"], "mock 应至少出题"
    assert body["usage"]["total_tokens"] > 0
    qtypes = {c["question_type"] for c in body["cases"]}
    assert qtypes <= {"factoid", "procedural"}
    for c in body["cases"]:
        assert c["question"] and c["expected_answer"]
        assert c["source"]["doc"]
        # 构建测试用例时即给出黄金事实全集（检索层 Recall 依赖）
        assert c["expected_evidence"], "生成的用例应携带黄金证据事实全集"
        assert c["expected_entities"], "生成的用例应携带黄金关键实体"


def test_judge_high_and_low():
    client = _client()
    # high: agent answer contains expected prefix -> mock scores 1.0
    expected = "这是 factoid 问题的期望答案示例。"
    r_high = client.post(
        "/judge",
        json={
            "question": "Q",
            "question_type": "factoid",
            "expected_answer": expected,
            "key_points": ["要点A", "要点B"],
            "agent_answer": expected + " 额外补充。",
        },
    )
    assert r_high.status_code == 200
    high = r_high.json()
    assert high["verdict"] == "correct"
    assert high["score"] == 1.0
    assert high["usage"]["total_tokens"] > 0

    # missing answer -> incorrect / 0.0
    r_low = client.post(
        "/judge",
        json={
            "question": "Q",
            "question_type": "factoid",
            "expected_answer": expected,
            "key_points": ["要点A"],
            "agent_answer": "",
        },
    )
    assert r_low.status_code == 200
    low = r_low.json()
    assert low["score"] == 0.0
    assert low["verdict"] == "incorrect"


def test_judge_retrieval():
    client = _client()
    r = client.post(
        "/judge-retrieval",
        json={
            "question": "默认 APN 是什么？",
            "expected_answer": "默认 APN 为 cmnet。",
            "gold_facts": ["默认 APN 为 cmnet", "cmnet"],
            "items": [
                "无关内容。",
                "默认 APN 为 cmnet，连接超时阈值 30 秒。",
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    # item 2 contains both gold facts -> grade 3; item 1 unrelated -> 0
    assert body["item_grades"] == [0, 3]
    # both gold facts first covered by item 2 (rank 2)
    assert body["gold_covered_at"] == [2, 2]
    assert body["usage"]["total_tokens"] > 0


# --- claude_cli provider (parsing/usage only; no live CLI call) -------------

def test_claude_cli_parse_result_and_usage():
    import pytest

    from runtime_eval.eval_llm.providers import claude_cli as cc

    sample = (
        '{"type":"result","subtype":"success","is_error":false,'
        '"result":"{\\"ok\\": true}",'
        '"usage":{"input_tokens":2,"cache_creation_input_tokens":600,'
        '"cache_read_input_tokens":1400,"output_tokens":14}}'
    )
    data = cc._parse_result(sample)
    assert data["result"] == '{"ok": true}'
    usage = cc._usage(data["usage"])
    # prompt = input + cache_creation + cache_read
    assert usage.prompt_tokens == 2002
    assert usage.completion_tokens == 14
    assert usage.total_tokens == 2016

    # tolerate trailing noise: pick the last JSON object
    noisy = "warn: something\n" + sample
    assert cc._parse_result(noisy)["result"] == '{"ok": true}'

    with pytest.raises(RuntimeError):
        cc._parse_result("")


def test_claude_cli_missing_bin():
    import pytest

    from runtime_eval.eval_llm.config import LLMConfig
    from runtime_eval.eval_llm.providers.claude_cli import ClaudeCLIProvider

    cfg = LLMConfig(provider="claude_cli", claude_cli_bin="claude-not-installed-xyz")
    with pytest.raises(RuntimeError, match="claude"):
        ClaudeCLIProvider(cfg)


def test_claude_cli_strips_auth_env(monkeypatch):
    """The CLI must run with ANTHROPIC_* stripped so it uses `claude login`
    credentials, not a stray/empty API key (which caused a 403)."""
    import subprocess

    from runtime_eval.eval_llm.config import LLMConfig
    from runtime_eval.eval_llm.providers import claude_cli as cc

    # Pretend claude is installed, and pollute the parent env.
    monkeypatch.setattr(cc.shutil, "which", lambda _bin: "/usr/bin/claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    # Host-managed markers injected when eval-llm is launched from inside the
    # Claude Code/Desktop session — these route the child to the host's OAuth
    # channel it can't reach, causing "403 Request not allowed". Must be stripped.
    monkeypatch.setenv("CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST", "1")
    monkeypatch.setenv("CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH", "1")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("PATH", "/usr/bin")  # a normal var that must survive

    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"result":"hi","is_error":false,"usage":{}}', stderr=""
        )

    monkeypatch.setattr(cc.subprocess, "run", fake_run)

    provider = cc.ClaudeCLIProvider(LLMConfig(provider="claude_cli"))
    out = provider.chat(system="s", user="u")
    assert out.text == "hi"
    env = captured["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST" not in env
    assert "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH" not in env
    assert "CLAUDECODE" not in env
    assert env.get("PATH") == "/usr/bin"  # unrelated vars preserved


def test_claude_cli_injects_proxy(monkeypatch):
    """配了 claude_cli_proxy 时，子进程必须带上 HTTP(S)_PROXY，
    否则国内直连 api.anthropic.com 会被地域封锁回 403。"""
    import subprocess

    from runtime_eval.eval_llm.config import LLMConfig
    from runtime_eval.eval_llm.providers import claude_cli as cc

    monkeypatch.setattr(cc.shutil, "which", lambda _bin: "/usr/bin/claude")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"result":"hi","is_error":false,"usage":{}}', stderr=""
        )

    monkeypatch.setattr(cc.subprocess, "run", fake_run)

    config = LLMConfig(provider="claude_cli", claude_cli_proxy="http://127.0.0.1:7892")
    cc.ClaudeCLIProvider(config).chat(system="s", user="u")
    env = captured["env"]
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7892"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7892"
    assert env["https_proxy"] == "http://127.0.0.1:7892"


def test_clean_child_env_no_proxy_by_default(monkeypatch):
    """不传 proxy 时不应凭空注入代理变量。"""
    from runtime_eval.eval_llm.providers import claude_cli as cc

    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    env = cc.clean_child_env()
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


# --- agent_runner：stream-json 解析 + run_agent ----------------------------

import json as _json


def _stream_lines() -> str:
    """一段模拟 `claude -p --output-format stream-json --verbose` 的输出。

    含：系统事件 → assistant 调用检索工具 → 工具返回 JSON 结果 →
    assistant 文本 → result 终态（带最终答案 + usage）。
    """
    events = [
        {"type": "system", "subtype": "init"},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "kb_search",
                        "input": {"query": "默认 APN 是什么"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {
                                "type": "text",
                                "text": _json.dumps(
                                    {
                                        "results": [
                                            {"text": "默认 APN 为 cmnet。", "source": "apn.md"},
                                            {"text": "连接超时阈值 30 秒。", "source": "apn.md"},
                                        ]
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                        ],
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "默认 APN 为 cmnet，连接超时阈值 30 秒。",
            "num_turns": 2,
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 200,
                "output_tokens": 20,
            },
        },
    ]
    return "\n".join(_json.dumps(e, ensure_ascii=False) for e in events)


def test_parse_stream_json():
    from runtime_eval.eval_llm import agent_runner as ar

    res = ar.parse_stream_json(_stream_lines())
    # 最终答案来自 result 事件
    assert res.answer == "默认 APN 为 cmnet，连接超时阈值 30 秒。"
    # 工具调用被捕获，查询词被抽取
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0].name == "kb_search"
    assert res.tool_calls[0].query == "默认 APN 是什么"
    # 检索片段被解析并去重重排
    assert [it["text"] for it in res.retrieved_items] == [
        "默认 APN 为 cmnet。",
        "连接超时阈值 30 秒。",
    ]
    assert res.retrieved_items[0]["source"] == "apn.md"
    assert res.retrieved_items[0]["rank"] == 1
    # usage = input + cache_creation + cache_read 作为 prompt
    assert res.usage.prompt_tokens == 310
    assert res.usage.completion_tokens == 20
    assert res.num_turns == 2


def test_parse_stream_json_text_fallback():
    """没有 result 事件时，用累积的 assistant 文本兜底；纯文本工具结果按段切分。"""
    from runtime_eval.eval_llm import agent_runner as ar

    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "x"}}
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "片段一\n\n片段二"}
                ]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "这是最终回答。"}]},
        },
    ]
    raw = "\n".join(_json.dumps(e, ensure_ascii=False) for e in events)
    res = ar.parse_stream_json(raw)
    assert res.answer == "这是最终回答。"
    assert [it["text"] for it in res.retrieved_items] == ["片段一", "片段二"]
    assert res.tool_calls[0].query == "x"


def test_parse_stream_json_cloud_core_shape():
    """贴合真实 cloud_core_network MCP 的 search_knowledge 返回：
    item 顶层有 text，出处在 sourceRefs.document_key；含 raw_segment 重复条目应被去重。"""
    from runtime_eval.eval_llm import agent_runner as ar

    tool_result = {
        "items": [
            {
                "id": "u1",
                "kind": "retrieval_unit",
                "text": "常见业务流量模型说明。",
                "title": "常见业务流量模型",
                "sourceRefs": {"document_key": "doc:/数据中心网络搬迁到SDN最佳实践.chm"},
                "evidenceRole": "background",
            },
            {  # raw_segment 重复条目（text 相同）→ 应被去重
                "id": "s1",
                "kind": "raw_segment",
                "text": "常见业务流量模型说明。",
                "title": "数据中心网络搬迁到SDN最佳实践",
                "metadata": {},
            },
        ],
        "relations": [],
        "sources": [],
    }
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "search_knowledge", "input": {"query": "业务流量模型"}}
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": [{"type": "text", "text": _json.dumps(tool_result, ensure_ascii=False)}]}
        ]}},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "常见业务流量模型包括…", "num_turns": 2, "usage": {}},
    ]
    res = ar.parse_stream_json("\n".join(_json.dumps(e, ensure_ascii=False) for e in events))
    assert len(res.retrieved_items) == 1  # 去重后仅一条
    assert res.retrieved_items[0]["text"] == "常见业务流量模型说明。"
    # 出处取自 sourceRefs.document_key（而非 item.title）
    assert res.retrieved_items[0]["source"] == "doc:/数据中心网络搬迁到SDN最佳实践.chm"
    assert res.tool_calls[0].name == "search_knowledge"


def test_run_agent_requires_mcp_config():
    import pytest

    from runtime_eval.eval_llm import agent_runner as ar

    cfg = LLMConfig(provider="claude_cli", agent_mcp_config="")
    with pytest.raises(RuntimeError, match="MCP"):
        ar.run_agent(cfg, "随便问个问题")


def test_run_agent_invokes_cli(monkeypatch, tmp_path):
    """run_agent 应组装含 --mcp-config 的命令并解析 stream-json 输出。"""
    import subprocess

    from runtime_eval.eval_llm import agent_runner as ar

    mcp = tmp_path / "mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    cfg = LLMConfig(
        provider="claude_cli",
        agent_mcp_config=str(mcp),
        agent_allowed_tools="kb_search",
    )

    monkeypatch.setattr(ar, "resolve_claude_bin", lambda _b: "/usr/bin/claude")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, stdout=_stream_lines(), stderr="")

    monkeypatch.setattr(ar.subprocess, "run", fake_run)
    out = ar.run_agent(cfg, "默认 APN 是什么？")

    assert "--mcp-config" in captured["argv"]
    assert str(mcp) in captured["argv"]
    assert "--allowedTools" in captured["argv"]
    assert "stream-json" in captured["argv"]
    assert out["answer"] == "默认 APN 为 cmnet，连接超时阈值 30 秒。"
    assert len(out["retrieved_items"]) == 2
    # 默认系统提示被折进 prompt
    assert "默认 APN 是什么？" in captured["input"]


def test_run_agent_closed_book_omits_mcp(monkeypatch):
    """闭卷路：不挂 MCP、不放行工具，且无需配 MCP 也能跑。"""
    import subprocess

    from runtime_eval.eval_llm import agent_runner as ar

    cfg = LLMConfig(
        provider="claude_cli",
        agent_mcp_config="",  # 闭卷路不需要 MCP
        agent_allowed_tools="kb_search",
    )
    monkeypatch.setattr(ar, "resolve_claude_bin", lambda _b: "/usr/bin/claude")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, stdout=_stream_lines(), stderr="")

    monkeypatch.setattr(ar.subprocess, "run", fake_run)
    out = ar.run_agent(cfg, "默认 APN 是什么？", route="closed_book")

    assert "--mcp-config" not in captured["argv"]
    assert "--allowedTools" not in captured["argv"]
    assert "stream-json" in captured["argv"]
    # 用的是闭卷系统提示，而非「先查 MCP」那条
    assert "没有任何检索工具" in captured["input"]
    assert out["answer"]


def test_run_agent_web_route_allows_websearch(monkeypatch):
    """联网路：放行 WebSearch，不挂 MCP。"""
    import subprocess

    from runtime_eval.eval_llm import agent_runner as ar

    cfg = LLMConfig(provider="claude_cli", agent_mcp_config="")
    monkeypatch.setattr(ar, "resolve_claude_bin", lambda _b: "/usr/bin/claude")
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=_stream_lines(), stderr="")

    monkeypatch.setattr(ar.subprocess, "run", fake_run)
    ar.run_agent(cfg, "Q", route="web")

    assert "--mcp-config" not in captured["argv"]
    assert "WebSearch" in captured["argv"]


def test_run_agent_endpoint_400_without_mcp():
    """/run-agent 未配 MCP 时返回 400（而非 500）。"""
    config = LLMConfig(provider="mock", agent_mcp_config="")
    app = create_app(config=config, provider=MockProvider(config))
    r = TestClient(app).post("/run-agent", json={"question": "Q"})
    assert r.status_code == 400
    assert "MCP" in r.json()["detail"]


# --- L2 答案对照层：作答端点 / 通道派发 / OpenAI 兼容 provider ---------------


def test_answer_endpoint_returns_answer_and_usage():
    """/answer 用 mock provider 时返回整合答案 + token 用量。"""
    config = LLMConfig(provider="mock")
    app = create_app(config=config, provider=MockProvider(config))
    r = TestClient(app).post(
        "/answer",
        json={
            "question": "默认 APN 是什么？",
            "evidence": ["默认 APN 为 cmnet", "超时阈值 30s"],
            "channel": "claude_cli",
            "model": "sonnet",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "cmnet" in body["answer"]
    assert body["usage"]["total_tokens"] > 0


def test_answer_provider_factory_receives_channel_and_model():
    """create_app 注入假工厂时，/answer 应把 channel+model 透传给工厂派发。"""
    seen = {}

    def fake_factory(cfg, channel, model):
        seen["channel"] = channel
        seen["model"] = model
        return MockProvider(cfg)

    config = LLMConfig(provider="mock")
    app = create_app(
        config=config, provider=MockProvider(config), answer_provider_factory=fake_factory
    )
    r = TestClient(app).post(
        "/answer",
        json={"question": "Q", "evidence": ["E"], "channel": "deepseek", "model": "deepseek-chat"},
    )
    assert r.status_code == 200
    assert seen == {"channel": "deepseek", "model": "deepseek-chat"}


def test_build_answer_provider_claude_cli_overrides_model(monkeypatch):
    """claude_cli 通道：按花名册点名的档位覆盖 --model。"""
    from runtime_eval.eval_llm.providers import factory

    captured = {}

    class FakeCLI:
        name = "claude_cli"

        def __init__(self, config):
            captured["model"] = config.claude_cli_model

    monkeypatch.setattr(
        "runtime_eval.eval_llm.providers.claude_cli.ClaudeCLIProvider", FakeCLI
    )
    config = LLMConfig(provider="claude_cli", claude_cli_model="opus")
    prov = factory.build_answer_provider(config, "claude_cli", "haiku")
    assert isinstance(prov, FakeCLI)
    assert captured["model"] == "haiku"


def test_build_answer_provider_openai_compat_from_channel():
    """openai_compat 通道：从 answer_channels 取 base_url/api_key，花名册 model 优先。"""
    from runtime_eval.eval_llm.providers import factory
    from runtime_eval.eval_llm.providers.openai_compat import OpenAICompatProvider

    config = LLMConfig(
        provider="mock",
        answer_channels={
            "deepseek": {
                "type": "openai_compat",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model": "deepseek-chat",
            }
        },
    )
    prov = factory.build_answer_provider(config, "deepseek", "")
    assert isinstance(prov, OpenAICompatProvider)
    assert prov.base_url == "https://api.deepseek.com/v1"
    assert prov.model == "deepseek-chat"
    # 花名册显式 model 覆盖通道默认
    prov2 = factory.build_answer_provider(config, "deepseek", "deepseek-reasoner")
    assert prov2.model == "deepseek-reasoner"


def test_build_answer_provider_unknown_channel_raises():
    """没配凭据的通道应抛 ValueError（端点据此回 400）。"""
    from runtime_eval.eval_llm.providers import factory

    config = LLMConfig(provider="mock")
    try:
        factory.build_answer_provider(config, "minimax", "abab6")
    except ValueError as exc:
        assert "minimax" in str(exc)
    else:
        raise AssertionError("未配凭据的通道应抛 ValueError")


def test_openai_compat_provider_posts_and_parses(monkeypatch):
    """OpenAICompatProvider.chat：发到 {base_url}/chat/completions、带 Bearer、解析 content+usage。"""
    from runtime_eval.eval_llm.providers.openai_compat import OpenAICompatProvider

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "最终答案文本"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    prov = OpenAICompatProvider(
        base_url="https://api.deepseek.com/v1/",
        api_key="sk-test",
        model="deepseek-chat",
        label="deepseek",
    )
    result = prov.chat(system="你是助手", user="问题", json_mode=True)

    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["json"]["model"] == "deepseek-chat"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert result.text == "最终答案文本"
    assert result.usage.total_tokens == 15


def test_judge_answer_match_coverage_and_contradiction():
    """漏了要点 + 含与黄金矛盾断言的答案：missed 非空、contradictions 非空、覆盖/准确下降。"""
    config = LLMConfig(provider="mock")
    app = create_app(config=config, provider=MockProvider(config))
    r = TestClient(app).post(
        "/judge-answer",
        json={
            "question": "默认 APN 与超时阈值？",
            "expected_answer": "默认 APN 为 cmnet，超时阈值 30s。",
            "key_points": ["cmnet", "30s"],
            # 命中 cmnet（覆盖其一），漏掉 30s；并含一句"矛盾"硬伤
            "answer": "默认 APN 为 cmnet。这里故意与黄金矛盾的说法。",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "cmnet" in body["covered_points"]
    assert "30s" in body["missed_points"]
    assert body["contradictions"], "应识别出矛盾硬伤"
    assert body["has_hard_error"] is True
    assert 0.0 < body["coverage"] < 1.0  # 命中一半要点
    assert body["precision"] < 1.0  # 有矛盾论断拉低准确度
    assert body["f1"] >= 0.0


def test_judge_answer_match_perfect_answer():
    """命中全部要点、无多余无矛盾：覆盖=准确=F1=1，无硬伤。"""
    config = LLMConfig(provider="mock")
    app = create_app(config=config, provider=MockProvider(config))
    r = TestClient(app).post(
        "/judge-answer",
        json={
            "question": "默认 APN 与超时阈值？",
            "expected_answer": "默认 APN 为 cmnet，超时阈值 30s。",
            "key_points": ["cmnet", "30s"],
            "answer": "cmnet。30s。",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["missed_points"] == []
    assert body["contradictions"] == []
    assert body["has_hard_error"] is False
    assert body["coverage"] == 1.0
    assert body["precision"] == 1.0
    assert body["f1"] == 1.0


def test_openai_compat_provider_requires_base_url_and_model():
    """缺 base_url 或 model 时构造即抛 RuntimeError。"""
    from runtime_eval.eval_llm.providers.openai_compat import OpenAICompatProvider

    try:
        OpenAICompatProvider(base_url="", api_key="k", model="m")
    except RuntimeError:
        pass
    else:
        raise AssertionError("缺 base_url 应抛 RuntimeError")
    try:
        OpenAICompatProvider(base_url="http://x", api_key="k", model="")
    except RuntimeError:
        pass
    else:
        raise AssertionError("缺 model 应抛 RuntimeError")


# --- 综合总评：提示词构造 + 服务 + 端点 -----------------------------------


def test_build_overall_summary_prompt_includes_layers_and_missing():
    from runtime_eval.eval_llm.prompts import build_overall_summary_prompt

    system, user = build_overall_summary_prompt(
        suite_meta={"name": "演示测试集", "total_cases": 12},
        l1={"pass_rate": 0.75, "kb_score": 0.81},
        l2={"models": [{"id": "good", "label": "Good", "f1": 0.9}]},
        l4=None,  # L4 未评测
    )
    # system 要求中文、大白话、先结论再分点、给改进建议
    assert "中文" in system and "改进" in system
    # user 带三层关键数 + 测试集元信息
    assert "演示测试集" in user and "12" in user
    assert "0.75" in user or "75" in user  # 检索通过率
    assert "Good" in user and "0.9" in user  # 答案质量最佳模型
    # 缺失层如实标注「未评测」
    assert "未评测" in user
    assert "MODE: OVERALL_SUMMARY" in user


def test_mock_provider_overall_summary_echoes_presence():
    from runtime_eval.eval_llm.config import LLMConfig
    from runtime_eval.eval_llm.prompts import build_overall_summary_prompt
    from runtime_eval.eval_llm.providers.mock import MockProvider

    _, user = build_overall_summary_prompt(
        suite_meta={"name": "T", "total_cases": 3},
        l1={"pass_rate": 0.5},
        l2=None,
        l4=None,
    )
    out = MockProvider(LLMConfig(provider="mock")).chat(system="s", user=user)
    # mock 返回中文文本（非 JSON），且能反映「有 1 层在、2 层未评测」
    assert "评测" in out.text
    assert out.usage.total_tokens > 0
