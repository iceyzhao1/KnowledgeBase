# L2 答案对照层 · 多模型 × 黄金答案评测 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal（一句话）：** 把"证据包"焊死不动当输入，让花名册里的多个 LLM 各整合一版最终答案，再用同一个固定裁判把每版答案跟**黄金最终答案**比，打「覆盖度 + 准确度 + 矛盾」三个分（综合分 F1 排序），出一张模型排行榜 + 逐题下钻。

**Architecture（怎么搭）：** 复用现成的"闭卷作答"通道（`run_agent route="closed_book"`，不挂任何工具），把检索回来的证据包揉进问题提示里喂给模型，再用新加的 `--model` 覆盖参数让同一通道换不同档位的 Claude（haiku/sonnet/opus）。每版答案过**一道"答案对照裁判"**——一次 LLM 调用同时把答案跟黄金最终答案（`expected_answer` + `key_points`）比出三组判定：覆盖（命中/遗漏黄金要点）、准确（答案论断里 G 认可/多余）、矛盾（跟 G 说反的）。编排层仿照已落地的 L4 uplift 流式生成器，逐"模型 × 题"推 SSE 进度，跑完聚合排行榜（综合分 F1 排序）落一份 `RunSummary(layer="answer_match")` 快照。前端在评测台加一张 L2 卡片，和 L4 卡片并排。

**Tech Stack：** Python 3 / FastAPI（eval-api + eval-llm）、claude CLI 子进程（claude_cli provider，吃订阅不花 API 钱）、原生 JS SPA（EventSource 接 SSE）、pytest。

**关键约束（落地前先记牢）：**
- 全程不开 worktree，直接在 master 改。
- 打分锚点是**黄金最终答案 G**（`expected_answer` + `key_points`），不是证据包；证据包只作 agent 的输入。
- 作答只用 claude_cli 三档（haiku/sonnet/opus），serving 只检索不作答、不进花名册。
- 裁判固定用 config 里那个 judge 模型，只换选手不换裁判。
- 门禁：没跑过 L1 检索（拿不到 `RetrievalSet`）的题不能跑 L2，要清晰报错、不静默跳过。
- 排行榜按综合分 F1 排序；每个(模型,题)只 2 次 LLM（作答 + 一次对照判）。
- eval-api 跑在 8800 端口，由用户自己重启。

---

## 文件结构（这次会动哪些文件、各管什么）

**新建：**
- `runtime_eval/runtime_eval/eval_api/roster.py` —— 花名册：`RosterEntry` 数据类 + 默认三档 + 从环境变量加载/筛选。

**修改（按数据流从底到顶）：**
- `runtime_eval/runtime_eval/eval_llm/agent_runner.py` —— `_build_argv` / `run_agent` 加 `model` 形参，支持按请求换档。
- `runtime_eval/runtime_eval/eval_llm/app.py` —— `RunAgentIn` 加 `model` 字段；新增 `JudgeAnswerIn` + `POST /judge-answer`。
- `runtime_eval/runtime_eval/eval_llm/prompts.py` —— 新增答案对照裁判的 system / schema / prompt 构造。
- `runtime_eval/runtime_eval/eval_llm/service.py` —— 新增 `judge_answer_match(...)`。
- `runtime_eval/runtime_eval/eval_api/llm_client.py` —— `run_agent` 加 `model`；新增 `judge_answer_match(...)`。
- `runtime_eval/runtime_eval/eval_api/orchestrator.py` —— `_answer_one` 加 `model`/`question_text`；新增 `iter_answer_match` / `run_answer_match` + 辅助函数。
- `runtime_eval/runtime_eval/eval_api/app.py` —— `create_app` 加 `roster`；新增 `GET /eval-roster`、`GET /suites/{sid}/answer-match:run/stream`、`GET /suites/{sid}/answer-match`。
- `runtime_eval/runtime_eval/eval_web/static/app.js` —— L2 卡片 + `runAnswerMatch` / `renderAnswerMatch` / `loadAnswerMatch`。
- `runtime_eval/runtime_eval/eval_web/static/index.html` —— 版本号 bump。
- `runtime_eval/tests/test_eval_api.py` / `runtime_eval/tests/test_eval_llm.py` —— 各任务的测试。

---

## Task 1：花名册 + 按请求换模型

**为什么先做这个：** 这是地基。现状是模型由全局配置定死，一次请求换不了档。先把"按请求点名换模型"这条线从最底层（claude CLI 的 `--model`）一路打通到接口，并验证 claude_cli 换档真生效，后面多模型对照才有立足点。

**Files:**
- Create: `runtime_eval/runtime_eval/eval_api/roster.py`
- Modify: `runtime_eval/runtime_eval/eval_llm/agent_runner.py:364`（`_build_argv`）、`:392`（`run_agent`）
- Modify: `runtime_eval/runtime_eval/eval_llm/app.py:47`（`RunAgentIn`）、`:126`（`POST /run-agent`）
- Modify: `runtime_eval/runtime_eval/eval_api/llm_client.py:139`（`run_agent`）
- Modify: `runtime_eval/runtime_eval/eval_api/app.py:575`（`create_app`）、新增 `GET /eval-roster`
- Test: `runtime_eval/tests/test_eval_llm.py`、`runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1：写花名册的失败测试**

在 `runtime_eval/tests/test_eval_api.py` 末尾新增：

```python
def test_roster_defaults_and_enabled_filter():
    from runtime_eval.eval_api.roster import (
        RosterEntry,
        load_roster,
        enabled_roster,
    )

    # 默认花名册 = claude_cli 三档，全部参赛
    default = load_roster(None)
    ids = [e.id for e in default]
    assert ids == ["claude-haiku", "claude-sonnet", "claude-opus"]
    assert all(e.channel == "claude_cli" for e in default)
    assert all(e.enabled for e in default)

    # 显式 JSON 覆盖：只留两档、其中一档停赛
    raw = (
        '[{"id":"a","label":"A","channel":"claude_cli","model":"haiku","enabled":true},'
        '{"id":"b","label":"B","channel":"claude_cli","model":"sonnet","enabled":false}]'
    )
    parsed = load_roster(raw)
    assert [e.id for e in parsed] == ["a", "b"]
    assert [e.id for e in enabled_roster(parsed)] == ["a"]
```

- [ ] **Step 2：跑测试确认它失败**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_roster_defaults_and_enabled_filter -v`
Expected: FAIL，报 `ModuleNotFoundError: No module named 'runtime_eval.eval_api.roster'`

- [ ] **Step 3：写 roster.py**

新建 `runtime_eval/runtime_eval/eval_api/roster.py`：

```python
"""参赛模型花名册：每个条目描述一个能基于证据包整合最终答案的模型。

首发只接 Claude 的三档（haiku/sonnet/opus），都走 claude_cli（吃订阅、不花 API 钱）。
serving 只检索不作答，不进花名册。新增厂商 = 实现一个 channel + 这里加一行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class RosterEntry:
    id: str                       # 稳定标识，如 "claude-sonnet"
    label: str                    # 显示名，如 "Claude Sonnet"
    channel: str = "claude_cli"   # 调用通道：claude_cli | llm_service | anthropic
    model: str = ""               # 该通道下的具体档位，如 claude_cli 的 --model 值
    enabled: bool = True          # 是否参赛


DEFAULT_ROSTER: list[RosterEntry] = [
    RosterEntry(id="claude-haiku", label="Claude Haiku", model="haiku"),
    RosterEntry(id="claude-sonnet", label="Claude Sonnet", model="sonnet"),
    RosterEntry(id="claude-opus", label="Claude Opus", model="opus"),
]


def load_roster(env_value: str | None) -> list[RosterEntry]:
    """从环境变量 EVAL_ROSTER 的 JSON 文本加载花名册；空/缺省 → 默认三档。

    JSON 形如 ``[{"id","label","channel","model","enabled"}, ...]``，
    缺字段按 RosterEntry 默认值补。解析失败抛 ValueError（别静默吞）。
    """

    text = (env_value or "").strip()
    if not text:
        return [RosterEntry(**vars(e)) for e in DEFAULT_ROSTER]
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"EVAL_ROSTER 不是合法 JSON：{exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("EVAL_ROSTER 必须是 JSON 数组")
    out: list[RosterEntry] = []
    for row in rows:
        out.append(
            RosterEntry(
                id=str(row["id"]),
                label=str(row.get("label") or row["id"]),
                channel=str(row.get("channel") or "claude_cli"),
                model=str(row.get("model") or ""),
                enabled=bool(row.get("enabled", True)),
            )
        )
    return out


def enabled_roster(entries: list[RosterEntry]) -> list[RosterEntry]:
    """只留参赛（enabled=True）的条目。"""

    return [e for e in entries if e.enabled]
```

- [ ] **Step 4：跑测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_roster_defaults_and_enabled_filter -v`
Expected: PASS

- [ ] **Step 5：写 `--model` 覆盖的失败测试**

在 `runtime_eval/tests/test_eval_llm.py` 末尾新增（验证按请求换档真能改到 argv）：

```python
def test_build_argv_model_override():
    from runtime_eval.eval_llm import agent_runner
    from runtime_eval.eval_llm.config import LLMConfig

    cfg = LLMConfig()
    cfg.agent_model = "sonnet"  # 全局默认档

    # 不传 model → 用全局默认
    argv_default = agent_runner._build_argv(cfg, "claude", route="closed_book")
    assert "--model" in argv_default
    assert argv_default[argv_default.index("--model") + 1] == "sonnet"

    # 传 model="opus" → 覆盖全局
    argv_override = agent_runner._build_argv(
        cfg, "claude", route="closed_book", model="opus"
    )
    assert argv_override[argv_override.index("--model") + 1] == "opus"
```

> 注：`LLMConfig()` 若不能无参构造，改用该测试文件里已有的 config fixture/构造方式（参考文件顶部 import 与既有用例）。

- [ ] **Step 6：跑测试确认失败**

Run: `python -m pytest runtime_eval/tests/test_eval_llm.py::test_build_argv_model_override -v`
Expected: FAIL，报 `_build_argv() got an unexpected keyword argument 'model'`

- [ ] **Step 7：给 `_build_argv` / `run_agent` 加 `model` 形参**

改 `runtime_eval/runtime_eval/eval_llm/agent_runner.py`。

`_build_argv` 签名与 `--model` 那段（原 364 行、383-384 行）：

```python
def _build_argv(
    config: LLMConfig,
    resolved: str,
    route: str = ROUTE_KB,
    model: str | None = None,
) -> list[str]:
```

把原来的：

```python
    if config.agent_model.strip():
        argv += ["--model", config.agent_model.strip()]
```

改成：

```python
    chosen_model = (model or config.agent_model).strip()
    if chosen_model:
        argv += ["--model", chosen_model]
```

`run_agent` 签名（原 392 行）加 `model`：

```python
def run_agent(
    config: LLMConfig,
    question: str,
    *,
    system: str | None = None,
    route: str = ROUTE_KB,
    model: str | None = None,
) -> dict:
```

把函数体里那行（原 416 行）：

```python
    argv = wrap_for_windows(resolved, _build_argv(config, resolved, route))
```

改成：

```python
    argv = wrap_for_windows(resolved, _build_argv(config, resolved, route, model=model))
```

- [ ] **Step 8：跑测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_llm.py::test_build_argv_model_override -v`
Expected: PASS

- [ ] **Step 9：把 `model` 透传到 eval-llm 接口**

改 `runtime_eval/runtime_eval/eval_llm/app.py`。

`RunAgentIn`（原 47 行）加字段：

```python
class RunAgentIn(BaseModel):
    question: str
    system: str | None = None
    route: str = "kb"
    model: str | None = None
```

`POST /run-agent`（原 126 行）那次调用加 `model=body.model`：

```python
    result = agent_runner.run_agent(
        config,
        body.question,
        system=body.system,
        route=body.route,
        model=body.model,
    )
```

> 若原调用是单行 `agent_runner.run_agent(config, body.question, system=body.system, route=body.route)`，整体替换为上面多行写法即可。

- [ ] **Step 10：把 `model` 透传到 eval-api 的 LLMClient**

改 `runtime_eval/runtime_eval/eval_api/llm_client.py` 的 `run_agent`（原 139 行）。

签名加 `model`：

```python
    def run_agent(
        self,
        *,
        question: str,
        system: str | None = None,
        route: str = "kb",
        model: str | None = None,
        timeout: float | None = None,
    ) -> dict:
```

请求体里带上 `model`（找到拼 payload 那段，把 model 一并塞进去）：

```python
        payload = {"question": question, "system": system, "route": route}
        if model:
            payload["model"] = model
```

> 注意：本类支持注入式 `poster`（测试用）。`model` 进了 payload 后，测试里的 `poster` 就能读到它——Task 3 的多模型测试要靠这个区分不同选手。

- [ ] **Step 11：create_app 接花名册 + 加 `GET /eval-roster`**

改 `runtime_eval/runtime_eval/eval_api/app.py`。

`create_app`（原 575 行）签名加 `roster`：

```python
def create_app(
    env_file: str | None = None,
    *,
    config: ApiConfig | None = None,
    store: Store | None = None,
    client: LLMClient | None = None,
    roster: list["RosterEntry"] | None = None,
):
```

文件顶部 import：

```python
from runtime_eval.eval_api.roster import RosterEntry, load_roster, enabled_roster
```

`create_app` 体内、`config`/`store`/`client` 落定之后，补一句默认加载：

```python
    if roster is None:
        roster = load_roster(os.environ.get("EVAL_ROSTER"))
```

> 若文件未 import `os`，在顶部加 `import os`。

仿照 `GET /api/v1/eval-criteria`（原 607 行）加只读端点：

```python
    @app.get("/api/v1/eval-roster")
    def get_eval_roster():
        return {
            "roster": [
                {
                    "id": e.id,
                    "label": e.label,
                    "channel": e.channel,
                    "model": e.model,
                    "enabled": e.enabled,
                }
                for e in roster
            ]
        }
```

- [ ] **Step 12：写 `/eval-roster` 接口测试**

在 `runtime_eval/tests/test_eval_api.py` 加：

```python
def test_eval_roster_endpoint_returns_default_three():
    from fastapi.testclient import TestClient
    from runtime_eval.eval_api.app import create_app

    app = create_app(config=_min_config(), store=_mem_store(), client=_noop_client())
    cli = TestClient(app)
    r = cli.get("/api/v1/eval-roster")
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["roster"]]
    assert ids == ["claude-haiku", "claude-sonnet", "claude-opus"]
```

> `_min_config` / `_mem_store` / `_noop_client`：复用本测试文件里已有的等价构造辅助（参考 `_uplift_stream_client` 或 `create_app(...)` 的既有用法；若无现成 helper，直接照那段 inline 构造）。

- [ ] **Step 13：跑测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_eval_roster_endpoint_returns_default_three -v`
Expected: PASS

- [ ] **Step 14：真机验证 claude_cli 换档生效（不写码，手动跑一次）**

按设计文档 §10 风险栏"先验证再编排"原则，用真实命令确认三档都能调、不额外烧额度。

Run（PowerShell，逐条）：
```
claude -p --model haiku "用一句话说你是哪个模型档位"
claude -p --model sonnet "用一句话说你是哪个模型档位"
claude -p --model opus "用一句话说你是哪个模型档位"
```
Expected: 三条都能正常返回文本、不报额度/鉴权错。若某档不可用，记录下来并在花名册里把它 `enabled=false`，不阻塞后续任务。

- [ ] **Step 15：Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/roster.py runtime_eval/runtime_eval/eval_llm/agent_runner.py runtime_eval/runtime_eval/eval_llm/app.py runtime_eval/runtime_eval/eval_api/llm_client.py runtime_eval/runtime_eval/eval_api/app.py runtime_eval/tests/test_eval_api.py runtime_eval/tests/test_eval_llm.py
git commit -m "feat(eval-L2): 花名册 + 按请求换模型档位（claude_cli --model 覆盖）"
```

---

## Task 2：答案对照裁判（A vs 黄金最终答案，一次出三组判定）

**为什么做这个：** 这层的核心打分件。一道裁判一次 LLM 调用，把模型答案 A 跟黄金最终答案 G（`expected_answer` + `key_points`）比，出三组清单：覆盖（G 要点命中/遗漏）、准确（A 论断里 G 认可/多余）、矛盾（A 跟 G 说反的）。跟编排解耦，单独可测。

**三个分怎么算（大白话）：**
- 覆盖度 = 命中要点 / (命中 + 遗漏)。防漏。
- 准确度 = G 认可的论断 / (认可 + 多余 + 矛盾)。防掺水/跑题。
- 矛盾 = 跟 G 说反的论断清单 + 条数（硬伤，单拎出来高亮）。
- 先做简单版（整段拆论断 + 列各清单），不追求逐字对齐。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_llm/prompts.py:118`（参考 `build_judge_prompt` / `JUDGE_SCHEMA`）
- Modify: `runtime_eval/runtime_eval/eval_llm/service.py:139`（参考 `judge_case`）
- Modify: `runtime_eval/runtime_eval/eval_llm/app.py`（新增 `JudgeAnswerIn` + `POST /judge-answer`）
- Modify: `runtime_eval/runtime_eval/eval_api/llm_client.py:101`（参考 `judge`，新增 `judge_answer_match`）
- Test: `runtime_eval/tests/test_eval_llm.py`

- [ ] **Step 1：写答案对照裁判的失败测试**

在 `runtime_eval/tests/test_eval_llm.py` 末尾新增（用 mock provider，给一段"漏要点 + 含与黄金矛盾断言"的答案，断言遗漏非空、矛盾非空、两个分都 < 1）：

```python
def test_judge_answer_match_scores_coverage_precision_contradiction():
    from runtime_eval.eval_llm import service
    from runtime_eval.eval_llm.providers import MockProvider  # 见文件既有 import 路径

    # mock 裁判：回一个固定 JSON——漏 1 个要点、1 条多余、1 条与黄金矛盾
    fake_json = (
        '{"covered_points":["要点A"],"missed_points":["要点B"],'
        '"correct_claims":["地球绕太阳转"],"extra_claims":["顺带提了月相"],'
        '"contradictions":["说太阳绕地球转"],"rationale":"x"}'
    )
    provider = MockProvider(reply=fake_json)  # 若 MockProvider 构造不同，按文件既有用法改

    out = service.judge_answer_match(
        provider,
        question="太阳系常识",
        answer="地球绕太阳转，顺带提了月相，太阳绕地球转。",
        expected_answer="地球绕太阳转；月球绕地球转。",
        key_points=["要点A", "要点B"],
    )
    assert out["covered_points"] == ["要点A"]
    assert out["missed_points"] == ["要点B"]
    assert out["contradictions"] == ["说太阳绕地球转"]
    assert out["coverage_score"] == 0.5            # 1 命中 / 2 要点
    # 准确度 = 认可 1 / (认可1 + 多余1 + 矛盾1) = 1/3
    assert abs(out["precision_score"] - 1 / 3) < 1e-6
    # F1(0.5, 1/3) = 2*0.5*(1/3)/(0.5+1/3) = 0.4
    assert abs(out["f1_score"] - 0.4) < 1e-6
```

> `MockProvider` 的真实构造方式以 `runtime_eval/runtime_eval/eval_llm/` 里现有 mock provider 为准（看 `service.judge_case` 的现有测试怎么造 provider，照搬）。核心断言不变。

- [ ] **Step 2：跑测试确认失败**

Run: `python -m pytest runtime_eval/tests/test_eval_llm.py::test_judge_answer_match_scores_coverage_precision_contradiction -v`
Expected: FAIL，报 `module 'runtime_eval.eval_llm.service' has no attribute 'judge_answer_match'`

- [ ] **Step 3：写答案对照裁判 prompt**

改 `runtime_eval/runtime_eval/eval_llm/prompts.py`，仿照 `build_judge_prompt`/`JUDGE_SCHEMA`（原 118 行起）新增：

```python
ANSWER_MATCH_JUDGE_SYSTEM = (
    "你是严格的答案对照裁判。给你一道题、一份【黄金最终答案】和该题的【评分要点】，"
    "以及待评的【模型答案】。你只依据黄金答案与评分要点判定，不要带入你自己的知识。"
    "请同时给出三组判定："
    "①覆盖——评分要点里，模型答案命中了哪些（covered）、漏了哪些（missed）；"
    "②准确——把模型答案拆成事实论断，哪些是黄金答案认可的（correct）、"
    "哪些是黄金答案之外的多余/跑题内容（extra）；"
    "③矛盾——模型答案里跟黄金答案直接说反的论断（contradictions）。"
    "只输出 JSON，不要多余文字。"
)

ANSWER_MATCH_JUDGE_SCHEMA = {
    "covered_points": "list[str]，命中的评分要点",
    "missed_points": "list[str]，漏掉的评分要点",
    "correct_claims": "list[str]，被黄金答案认可的论断",
    "extra_claims": "list[str]，黄金答案之外的多余/跑题论断",
    "contradictions": "list[str]，跟黄金答案直接矛盾的论断",
    "rationale": "str，一句话说明判定依据",
}


def build_answer_match_prompt(
    *,
    question: str,
    answer: str,
    expected_answer: str,
    key_points: list[str],
) -> str:
    kp = "\n".join(f"- {p}" for p in key_points) or "（无）"
    schema_lines = "\n".join(f"  - {k}: {v}" for k, v in ANSWER_MATCH_JUDGE_SCHEMA.items())
    return (
        f"【问题】\n{question}\n\n"
        f"【黄金最终答案】\n{expected_answer or '（无）'}\n\n"
        f"【评分要点】\n{kp}\n\n"
        f"【模型答案】\n{answer}\n\n"
        f"按下面字段输出 JSON：\n{schema_lines}\n"
    )
```

- [ ] **Step 4：写 `judge_answer_match` 服务函数**

改 `runtime_eval/runtime_eval/eval_llm/service.py`，仿照 `judge_case`（原 139 行）新增：

```python
def judge_answer_match(
    provider,
    *,
    question: str,
    answer: str,
    expected_answer: str,
    key_points: list[str],
    temperature: float = 0.0,
    max_tokens: int = 1536,
) -> dict:
    """答案对照裁判：把模型答案跟黄金最终答案比，一次出覆盖/准确/矛盾三组判定与三个分。

    返回 ``{covered_points, missed_points, correct_claims, extra_claims,
    contradictions, coverage_score, precision_score, f1_score, rationale, usage}``。
    - 覆盖度 = 命中 / (命中 + 遗漏)；分母 0 记 1.0。
    - 准确度 = 认可 / (认可 + 多余 + 矛盾)；分母 0 记 1.0。
    - F1 = 2·P·R / (P + R)；P+R 为 0 记 0.0。
    """

    prompt = prompts.build_answer_match_prompt(
        question=question,
        answer=answer,
        expected_answer=expected_answer,
        key_points=key_points,
    )
    raw = provider.complete(
        system=prompts.ANSWER_MATCH_JUDGE_SYSTEM,
        prompt=prompt,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    data = _parse_json_reply(raw.text)  # 复用本文件 judge_case 用的同名解析助手
    covered = [str(x) for x in (data.get("covered_points") or [])]
    missed = [str(x) for x in (data.get("missed_points") or [])]
    correct = [str(x) for x in (data.get("correct_claims") or [])]
    extra = [str(x) for x in (data.get("extra_claims") or [])]
    contra = [str(x) for x in (data.get("contradictions") or [])]

    cov_total = len(covered) + len(missed)
    coverage = 1.0 if cov_total == 0 else len(covered) / cov_total
    prec_total = len(correct) + len(extra) + len(contra)
    precision = 1.0 if prec_total == 0 else len(correct) / prec_total
    f1 = 0.0 if (coverage + precision) == 0 else 2 * coverage * precision / (coverage + precision)

    return {
        "covered_points": covered,
        "missed_points": missed,
        "correct_claims": correct,
        "extra_claims": extra,
        "contradictions": contra,
        "coverage_score": coverage,
        "precision_score": precision,
        "f1_score": f1,
        "rationale": str(data.get("rationale") or ""),
        "usage": raw.usage if hasattr(raw, "usage") else {},
    }
```

> 三处对接现有代码（照 `judge_case` 怎么写就怎么写）：①`provider.complete(...)` 的真实签名/返回类型；②JSON 解析助手的真实函数名（`judge_case` 里用的那个）；③`usage` 怎么从 provider 返回里取。把这三处替换成本文件既有写法，算分逻辑不变。

- [ ] **Step 5：跑服务层测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_llm.py::test_judge_answer_match_scores_coverage_precision_contradiction -v`
Expected: PASS

- [ ] **Step 6：加 eval-llm 的 `/judge-answer` 端点**

改 `runtime_eval/runtime_eval/eval_llm/app.py`，仿照已有 judge 端点新增入参模型与路由：

```python
class JudgeAnswerIn(BaseModel):
    question: str
    answer: str
    expected_answer: str = ""
    key_points: list[str] = []


@app.post("/judge-answer")
def judge_answer_endpoint(body: JudgeAnswerIn):
    provider = _get_provider(config)  # 用本文件里 judge 端点同款拿 provider 的写法
    return service.judge_answer_match(
        provider,
        question=body.question,
        answer=body.answer,
        expected_answer=body.expected_answer,
        key_points=body.key_points,
    )
```

> `_get_provider(config)` 换成本文件 `POST /judge` 端点里实际拿 provider 的那行写法。

- [ ] **Step 7：加 eval-api LLMClient 的 `judge_answer_match`**

改 `runtime_eval/runtime_eval/eval_api/llm_client.py`，仿照 `judge`（原 101 行）新增：

```python
    def judge_answer_match(
        self,
        *,
        question: str,
        answer: str,
        expected_answer: str,
        key_points: list[str],
    ) -> dict:
        return self._post(
            "/judge-answer",
            {
                "question": question,
                "answer": answer,
                "expected_answer": expected_answer,
                "key_points": key_points,
            },
        )
```

> `self._post(path, payload)` 换成本类 `judge` 方法实际用的发请求写法（含注入式 `poster` 的兼容路径）。返回直接是 `judge_answer_match` 那个 dict。

- [ ] **Step 8：跑全量 eval-llm 测试确认没回归**

Run: `python -m pytest runtime_eval/tests/test_eval_llm.py -v`
Expected: 全 PASS

- [ ] **Step 9：Commit**

```bash
git add runtime_eval/runtime_eval/eval_llm/prompts.py runtime_eval/runtime_eval/eval_llm/service.py runtime_eval/runtime_eval/eval_llm/app.py runtime_eval/runtime_eval/eval_api/llm_client.py runtime_eval/tests/test_eval_llm.py
git commit -m "feat(eval-L2): 答案对照裁判（A vs 黄金答案 → 覆盖/准确/矛盾 + F1）"
```

---

## Task 3：编排 + SSE + 门禁

**为什么做这个：** 把地基（换模型）和裁判（答案对照）串成一条完整流水：对花名册里每个模型、每道题，依次"揉证据作答 → 对照判"，逐步推 SSE 进度，跑完聚合排行榜（综合分 F1 排序）落库。门禁卡在最前面——没证据的题直接报错。

**作答怎么揉证据（大白话）：** 不重新检索。把这题 L1 检索回来的片段（`RetrievalSet.for_case`）拼成一段"参考资料"，塞进问题提示里，用 `route="closed_book"`（不挂任何工具）喂给模型，并用 Task 1 的 `model=<花名册档位>` 指定选手。这样模型就是"看着我们给的卷宗答题"，比的是它综合证据的本事。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/orchestrator.py:404`（`_answer_one` 加 `model`/`question_text`）；新增 `iter_answer_match` / `run_answer_match` + 辅助
- Modify: `runtime_eval/runtime_eval/eval_api/app.py:1016`（仿 uplift SSE 端点）、`:1046`（仿 uplift 读取端点）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1：给 `_answer_one` 加 `model` / `question_text`**

改 `runtime_eval/runtime_eval/eval_api/orchestrator.py` 的 `_answer_one`（原 404 行）：

```python
def _answer_one(
    client: LLMClient,
    case: TestCase,
    *,
    route: str,
    system: str | None = None,
    model: str | None = None,
    question_text: str | None = None,
) -> AgentResponse:
    """跑单题单路作答，返回内存里的 AgentResponse（不落库）。

    ``model`` 按请求指定选手档位；``question_text`` 用揉了证据的问题覆盖原题
    （None 时回退 case.question）。
    """

    started = time.perf_counter()
    out = client.run_agent(
        question=question_text if question_text is not None else case.question,
        system=system,
        route=route,
        model=model,
    )
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = TokenUsage(**(out.get("usage") or {}))
    return AgentResponse(
        case_id=case.id,
        answer=str(out.get("answer") or ""),
        latency_ms=latency_ms,
        token_usage=usage,
        raw={
            "route": route,
            "model": model,
            "tool_calls": out.get("tool_calls") or [],
            "num_turns": out.get("num_turns", 0),
        },
    )
```

- [ ] **Step 2：写编排的失败测试（事件序列 + 排行榜 + 落库）**

在 `runtime_eval/tests/test_eval_api.py` 新增一个 answer-match 流式 client + 测试。先加 helper（仿 `_uplift_stream_client` / `_uplift_suite`）：

```python
def _ans_stream_client(tmp_path):
    """造一个能跑 L2 的 app：注入 poster 处理 /run-agent /judge-answer。

    - /run-agent：按 payload['model'] 给不同选手不同答案（区分选手用）。
    - /judge-answer：含 "瞎编" → 1 条矛盾、准确度<1；否则全命中全认可、无矛盾。
    """
    from runtime_eval.eval_api.app import create_app
    from runtime_eval.eval_api.llm_client import LLMClient
    from runtime_eval.eval_api.roster import RosterEntry

    def poster(path, payload):
        if path == "/run-agent":
            m = payload.get("model") or "?"
            return {"answer": f"{m} 的答案", "usage": {"input_tokens": 5, "output_tokens": 7}}
        if path == "/judge-answer":
            ans = payload.get("answer", "")
            if "瞎编" in ans:
                return {"covered_points": ["k1"], "missed_points": [],
                        "correct_claims": ["a"], "extra_claims": [], "contradictions": ["错的"],
                        "coverage_score": 1.0, "precision_score": 0.5, "f1_score": 2 / 3,
                        "rationale": "x", "usage": {}}
            return {"covered_points": ["k1"], "missed_points": [],
                    "correct_claims": ["a"], "extra_claims": [], "contradictions": [],
                    "coverage_score": 1.0, "precision_score": 1.0, "f1_score": 1.0,
                    "rationale": "ok", "usage": {}}
        if path == "/health":
            return {"ok": True}
        raise AssertionError(f"unexpected path {path}")

    config, store, suite = _ans_suite(tmp_path)  # 见下：建套题 + 给每题塞 RetrievalSet
    roster = [
        RosterEntry(id="m-a", label="A", model="haiku"),
        RosterEntry(id="m-b", label="B", model="sonnet"),
    ]
    app = create_app(config=config, store=store, client=LLMClient("http://x", poster=poster), roster=roster)
    return app, store, suite


def _ans_suite(tmp_path):
    # 复用本文件已有的建 config/store/suite 的辅助；关键是给每个 case 落一份 RetrievalSet。
    config, store, suite = _make_suite(tmp_path, suite_id="s_ans", n_cases=2)  # 按既有 helper 改名
    from runtime_eval.shared.models import RetrievalSet, RetrievedItem
    for case in suite.cases:
        rset = RetrievalSet(suite_id=suite.suite_id, case_id=case.id)
        rset.items.append(RetrievedItem(rank=1, text="这是证据片段", source="doc#1"))
        store.save_retrieval_set(rset)  # 换成本仓库 store 实际的保存方法名
    return config, store, suite
```

测试本体：

```python
def test_answer_match_run_stream_emits_progress_and_persists(tmp_path):
    from fastapi.testclient import TestClient

    app, store, suite = _ans_stream_client(tmp_path)
    cli = TestClient(app)
    r = cli.get(f"/api/v1/suites/{suite.suite_id}/answer-match:run/stream")
    assert r.status_code == 200

    events = _parse_sse(r.text)  # 复用本文件已有的 SSE 解析助手
    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    assert kinds[-1] == "done"
    # 每个选手都该出现「作答 / 对照判」两段
    stages = {e.get("stage") for e in events if e["event"] == "case_start"}
    assert stages == {"m-a_answer", "m-a_judge", "m-b_answer", "m-b_judge"}

    done = events[-1]
    leaderboard = done["metrics"]["leaderboard"]
    assert {row["id"] for row in leaderboard} == {"m-a", "m-b"}
    # 答案不含"瞎编" → 覆盖/准确/F1 都满分、硬伤 0
    assert all(row["avg_f1"] == 1.0 for row in leaderboard)
    assert all(row["avg_coverage"] == 1.0 for row in leaderboard)
    assert all(row["avg_precision"] == 1.0 for row in leaderboard)
    assert all(row["contradictions"] == 0 for row in leaderboard)

    # 落库快照可读回
    snap = store.get_run_summary(f"ans_{suite.suite_id}")
    assert snap is not None
    assert snap.layer == "answer_match"
    assert snap.kind == "multi_model"
```

> helper 里几处"换成本仓库实际方法名"的占位（`_make_suite`、`store.save_retrieval_set`、`store.get_run_summary`、`_parse_sse`），实现时照本测试文件 + `shared/models.py` + store 的既有写法对齐。逻辑断言不变。

- [ ] **Step 3：跑测试确认失败**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_answer_match_run_stream_emits_progress_and_persists -v`
Expected: FAIL（端点不存在 → 404，或 `iter_answer_match` 未定义）

- [ ] **Step 4：写门禁 + 辅助函数**

改 `runtime_eval/runtime_eval/eval_api/orchestrator.py`，在 uplift 相关函数附近新增：

```python
GROUNDED_ANSWER_SYSTEM = (
    "你只能依据【参考资料】回答问题，不要编造资料里没有的内容；"
    "资料不足就如实说不知道。"
)


def _grounded_question(case: TestCase, items: list) -> str:
    """把证据片段揉进问题，拼成"看资料答题"的提示。"""

    ev = "\n".join(f"[资料{i + 1}] {it.text}" for i, it in enumerate(items)) or "（无）"
    return f"【参考资料】\n{ev}\n\n【问题】\n{case.question}"


def _require_evidence(store: Store, suite: TestSuite) -> dict[str, list]:
    """门禁：每题都得有 RetrievalSet 片段，缺一题就抛错（别静默跳过）。"""

    evidence: dict[str, list] = {}
    missing: list[str] = []
    for case in suite.cases:
        rset = RetrievalSet.for_case(store, suite.suite_id, case.id)  # 换成本仓库实际取法
        items = list(rset.items) if rset else []
        if not items:
            missing.append(case.id)
        evidence[case.id] = items
    if missing:
        raise RuntimeError(
            f"这些题还没跑过 L1 检索、拿不到证据包，不能跑 L2：{', '.join(missing)}。请先去补检索。"
        )
    return evidence
```

> `RetrievalSet.for_case(...)` 换成本仓库实际签名（设计文档记的是 `RetrievalSet.for_case(case_id)`，落地时核对它是类方法还是要传 store；取片段列表的属性名以 `shared/models.py` 为准）。

- [ ] **Step 5：写 `iter_answer_match` 生成器**

接着在 orchestrator.py 新增（仿 `iter_value_uplift` 的事件协议）：

```python
def iter_answer_match(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    roster: list,
) -> Iterator[dict]:
    """L2 答案对照（流式）：花名册每个模型 × 每题，作答→对照判。

    门禁先卡：缺证据直接抛错。逐"模型×阶段"yield 进度，跑完聚合排行榜（综合分 F1 排序）
    落 ``RunSummary(run_id=f"ans_{sid}", layer="answer_match", kind="multi_model")``。
    事件：start / case_start / case_done / case_error / done。
    """

    sid = suite.suite_id
    cases = suite.cases
    n = len(cases)
    players = [e for e in roster if e.enabled]

    evidence = _require_evidence(store, suite)  # 门禁

    yield {"event": "start", "total": n, "models": len(players), "stages": 2}

    by_model: dict[str, dict] = {}
    leaderboard: list[dict] = []

    for e in players:
        cases_detail: list[dict] = []
        cov_sum = 0.0
        prec_sum = 0.0
        f1_sum = 0.0
        contra_total = 0
        tok = TokenUsage()
        lat = 0.0
        answered = 0
        for i, case in enumerate(cases):
            items = evidence[case.id]
            # —— 作答 ——
            yield {"event": "case_start", "stage": f"{e.id}_answer", "label": f"{e.label}·作答",
                   "model": e.id, "i": i, "n": n, "case_id": case.id, "question": case.question}
            try:
                resp = _answer_one(
                    client, case, route="closed_book", system=GROUNDED_ANSWER_SYSTEM,
                    model=e.model, question_text=_grounded_question(case, items),
                )
            except Exception as exc:  # noqa: BLE001
                yield {"event": "case_error", "stage": f"{e.id}_answer", "model": e.id,
                       "case_id": case.id, "error": str(exc)}
                continue
            tok = tok + resp.token_usage
            lat += resp.latency_ms
            answered += 1
            # —— 对照判 ——
            yield {"event": "case_start", "stage": f"{e.id}_judge", "label": f"{e.label}·对照判",
                   "model": e.id, "i": i, "n": n, "case_id": case.id, "question": case.question}
            jm = client.judge_answer_match(
                question=case.question, answer=resp.answer,
                expected_answer=case.expected_answer, key_points=list(case.key_points),
            )
            cov = float(jm.get("coverage_score") or 0.0)
            prec = float(jm.get("precision_score") or 0.0)
            f1 = float(jm.get("f1_score") or 0.0)
            contras = jm.get("contradictions") or []
            cov_sum += cov
            prec_sum += prec
            f1_sum += f1
            contra_total += len(contras)
            cases_detail.append({
                "case_id": case.id, "question": case.question, "answer": resp.answer,
                "coverage_score": cov, "precision_score": prec, "f1_score": f1,
                "covered_points": jm.get("covered_points") or [],
                "missed_points": jm.get("missed_points") or [],
                "extra_claims": jm.get("extra_claims") or [],
                "contradictions": contras,
            })
            yield {"event": "case_done", "stage": f"{e.id}_judge", "model": e.id,
                   "i": i, "n": n, "case_id": case.id,
                   "coverage_score": cov, "precision_score": prec, "f1_score": f1,
                   "contradictions": len(contras)}
        denom = answered or 1
        row = {
            "id": e.id, "label": e.label, "model": e.model,
            "avg_coverage": cov_sum / denom, "avg_precision": prec_sum / denom,
            "avg_f1": f1_sum / denom, "contradictions": contra_total,
            "tokens": tok.total if hasattr(tok, "total") else (tok.input_tokens + tok.output_tokens),
            "latency_ms": lat, "answered": answered,
        }
        leaderboard.append(row)
        by_model[e.id] = {"cases": cases_detail}

    leaderboard.sort(key=lambda r: r["avg_f1"], reverse=True)
    metrics = {
        "n": n,
        "roster": [{"id": e.id, "label": e.label, "model": e.model} for e in players],
        "leaderboard": leaderboard,
        "by_model": by_model,
    }
    summary = RunSummary(
        run_id=f"ans_{sid}", project_id=suite.project_id, suite_id=sid,
        layer="answer_match", kind="multi_model", status="done", metrics=metrics,
    )
    store.save_run_summary(summary)  # 换成本仓库实际保存方法名
    yield {"event": "done", "run_id": summary.run_id, "metrics": metrics}


def run_answer_match(
    store: Store,
    client: LLMClient,
    config: ApiConfig,
    suite: TestSuite,
    roster: list,
) -> RunSummary:
    """阻塞版：drain iter_answer_match 取回落库快照（给非流式/测试用）。"""

    for _ in iter_answer_match(store, client, config, suite, roster):
        pass
    return store.get_run_summary(f"ans_{suite.suite_id}")
```

> 几处对齐占位：`TokenUsage.total`（没有就用 input+output）、`RunSummary(...)` 字段（核对 `shared/models.py` 的必填项与默认）、`store.save_run_summary` / `store.get_run_summary` / `RetrievalSet.for_case` 的真实方法名。逻辑（门禁→对照判→F1排行→落库）不变。

- [ ] **Step 6：加 eval-api 的两个端点**

改 `runtime_eval/runtime_eval/eval_api/app.py`，仿 uplift SSE 端点（原 1016 行）+ 读取端点（原 1046 行）：

```python
    @app.get("/api/v1/suites/{sid}/answer-match:run/stream")
    def answer_match_run_stream(sid: str):
        suite = store.get_suite(sid)  # 换成本文件取 suite 的现有写法
        if suite is None:
            raise HTTPException(status_code=404, detail="suite 不存在")

        def gen():
            try:
                for ev in orchestrator.iter_answer_match(store, client, config, suite, roster):
                    yield _sse(ev)  # 复用 uplift 端点同款 _sse(obj) 助手
            except Exception as exc:  # noqa: BLE001
                yield _sse({"event": "fatal", "error": str(exc)})

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/v1/suites/{sid}/answer-match")
    def get_answer_match(sid: str):
        snap = store.get_run_summary(f"ans_{sid}")
        if snap is None:
            return {"status": "empty"}
        return {"status": snap.status, "run_id": snap.run_id, "metrics": snap.metrics}
```

> `_sse`、`StreamingResponse`、`HTTPException`、`store.get_suite` 都用本文件 uplift 端点已经在用的同名写法（确保 import 已有）。

- [ ] **Step 7：跑测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_answer_match_run_stream_emits_progress_and_persists -v`
Expected: PASS

- [ ] **Step 8：写门禁的失败路径测试**

在 `runtime_eval/tests/test_eval_api.py` 加（缺证据 → fatal 事件，不静默）：

```python
def test_answer_match_gate_blocks_when_no_evidence(tmp_path):
    from fastapi.testclient import TestClient

    app, store, suite = _ans_stream_client(tmp_path)
    # 抹掉证据，模拟没跑过 L1
    store.clear_retrieval_sets(suite.suite_id)  # 换成本仓库实际清法；或重建一个无证据的 suite
    cli = TestClient(app)
    r = cli.get(f"/api/v1/suites/{suite.suite_id}/answer-match:run/stream")
    events = _parse_sse(r.text)
    assert any(e["event"] == "fatal" and "检索" in e.get("error", "") for e in events)
```

> 若 store 没有"清证据"的方法，改成在 helper 里加个 `with_evidence=False` 开关建一个无证据的 suite。要点：门禁路径 yield `fatal`、错误信息提示去补检索。

- [ ] **Step 9：跑门禁测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_answer_match_gate_blocks_when_no_evidence -v`
Expected: PASS

- [ ] **Step 10：Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/orchestrator.py runtime_eval/runtime_eval/eval_api/app.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(eval-L2): 多模型答案对照编排 + SSE 流式进度 + 证据门禁"
```

---

## Task 4：缓存（同证据+模型+题 命中复用）

**为什么做这个：** 多模型 × 全题 × 2 次 LLM 调用仍然慢。证据包、模型、题目三者都没变时，直接复用上一次 `ans_<sid>` 快照里的那条（模型,题）结果，不重跑、不重复烧模型。

**缓存键（大白话）：** 给"这版证据 + 哪个选手 + 哪道题"算一个指纹。指纹一样 → 上次的答案和各项分照搬；证据或题目一变 → 指纹变、这条作废、得重跑。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/orchestrator.py`（`iter_answer_match` 内嵌命中逻辑 + 指纹助手）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1：写缓存命中的失败测试**

在 `runtime_eval/tests/test_eval_api.py` 加（同套题跑两次，第二次不应再调 /run-agent）：

```python
def test_answer_match_cache_reuses_unchanged_model_case(tmp_path):
    from fastapi.testclient import TestClient

    app, store, suite = _ans_stream_client(tmp_path)
    cli = TestClient(app)

    # 第一次：正常跑，落快照
    cli.get(f"/api/v1/suites/{suite.suite_id}/answer-match:run/stream")

    # 第二次：复用同一 store（带上次快照）+ 带计数 poster，断言不再调作答
    app2, store2, suite2, counter = _ans_stream_client_counting(tmp_path, store, suite)
    cli2 = TestClient(app2)
    r = cli2.get(f"/api/v1/suites/{suite2.suite_id}/answer-match:run/stream")
    events = _parse_sse(r.text)
    assert all(e.get("cached") for e in events if e["event"] == "case_done")
    assert counter["run_agent"] == 0  # 计数器：见 helper
```

> `_ans_stream_client_counting`：复用同一个 store（带上一次的 `ans_<sid>` 快照）、同一套题与证据，poster 里给 `/run-agent` 加一个计数器（用闭包 dict）并返回它。核心断言：第二次全命中、不再调作答。

- [ ] **Step 2：跑测试确认失败**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_answer_match_cache_reuses_unchanged_model_case -v`
Expected: FAIL（第二次仍调了 /run-agent，或没有 cached 标记）

- [ ] **Step 3：加指纹助手 + 命中逻辑**

改 `runtime_eval/runtime_eval/eval_api/orchestrator.py`。文件顶部确保 `import hashlib`，新增助手：

```python
def _fingerprint(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _evidence_fp(items: list) -> str:
    return _fingerprint(*[it.text for it in items])


def _load_ans_cache(store: Store, sid: str) -> dict[tuple[str, str], dict]:
    """读上次 ans_<sid> 快照，建 (model_id, 证据指纹+题指纹) → 明细 的索引。"""

    snap = store.get_run_summary(f"ans_{sid}")
    if snap is None:
        return {}
    cache: dict[tuple[str, str], dict] = {}
    for mid, blob in (snap.metrics.get("by_model") or {}).items():
        for d in blob.get("cases", []):
            ck = d.get("_cache_key", "")
            if ck:
                cache[(mid, ck)] = d
    return cache
```

在 `iter_answer_match` 里：进入模型循环前 `cache = _load_ans_cache(store, sid)`；每题进入作答前先算 `ck = _fingerprint(_evidence_fp(items), case.question)`，查 `cache.get((e.id, ck))`：

```python
            ck = _fingerprint(_evidence_fp(items), case.question)
            hit = cache.get((e.id, ck))
            if hit is not None:
                cov = float(hit.get("coverage_score") or 0.0)
                prec = float(hit.get("precision_score") or 0.0)
                f1 = float(hit.get("f1_score") or 0.0)
                contras = hit.get("contradictions") or []
                cov_sum += cov
                prec_sum += prec
                f1_sum += f1
                contra_total += len(contras)
                answered += 1
                detail = dict(hit)
                detail["_cache_key"] = ck
                cases_detail.append(detail)
                yield {"event": "case_done", "stage": f"{e.id}_judge", "model": e.id,
                       "i": i, "n": n, "case_id": case.id, "cached": True,
                       "coverage_score": cov, "precision_score": prec, "f1_score": f1,
                       "contradictions": len(contras)}
                continue
```

并在原本"作答→对照判"算完那条 `cases_detail.append({...})` 里，给字典加一项 `"_cache_key": ck`，并在最后那个 `case_done` 事件里带 `"cached": False`。

> `_cache_key` 是落进快照、供下次比对的内部字段；前端渲染只读白名单字段，自然不显示下划线开头的键。

- [ ] **Step 4：跑缓存测试 + 原编排测试确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py::test_answer_match_cache_reuses_unchanged_model_case runtime_eval/tests/test_eval_api.py::test_answer_match_run_stream_emits_progress_and_persists -v`
Expected: 都 PASS（第一次跑 cached=False，第二次全 cached=True）

- [ ] **Step 5：Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/orchestrator.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(eval-L2): 缓存——同(证据+模型+题)指纹命中复用，不重跑"
```

---

## Task 5：前端 L2 卡片 + 排行榜 + 逐题下钻

**为什么做这个：** 把后端能力露出到评测台。一张和 L4 并排的 L2 卡片：顶部列参赛选手 + 「跑答案对照」按钮 + SSE 进度行；主体是排行榜表（综合分排序）；点行展开看该选手逐题（题目/答案/覆盖+遗漏/准确+多余/矛盾硬伤高亮）。

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（`flowStepReport` 加卡片 + 函数）
- Modify: `runtime_eval/runtime_eval/eval_web/static/index.html:66`（版本号 bump）
- Test: `runtime_eval/tests/test_eval_api.py`（SPA 接线断言）

- [ ] **Step 1：写 SPA 接线断言（失败测试）**

在 `runtime_eval/tests/test_eval_api.py` 的 SPA 断言块（around 611-666，`app_js = client.get("/static/app.js")`）追加：

```python
    assert "answer-match:run/stream" in app_js.text
    assert "eval-roster" in app_js.text
    assert "renderAnswerMatch" in app_js.text
```

- [ ] **Step 2：跑测试确认失败**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py -k spa -v`
Expected: FAIL（app.js 里还没这些串）

> `-k spa` 换成本仓库那条 SPA 断言用例的实际名字（看 611 行附近的 `def test_...`）。

- [ ] **Step 3：在 `flowStepReport` 加 L2 卡片标记**

改 `runtime_eval/runtime_eval/eval_web/static/app.js`，在 `upliftCard`（735-744 行）markup 后并排追加一段（仿其结构）：

```javascript
  const ansCard = `
    <section class="card" id="ansCard">
      <div class="card-h">
        <div class="card-t">L2 答案对照（多模型 × 黄金答案）</div>
        <div class="spacer"></div>
        <button class="btn" id="ansRunBtn">跑答案对照</button>
        <button class="btn ghost" id="ansReloadBtn">读最近结果</button>
      </div>
      <div class="muted" id="ansRoster">参赛选手加载中…</div>
      <div class="progline" id="ansProg"></div>
      <div id="ansBody"></div>
    </section>`;
```

把 `ansCard` 拼进该函数返回的 HTML 里（紧挨 `upliftCard` 之后）。

- [ ] **Step 4：写 `loadRoster` / `runAnswerMatch` / `renderAnswerMatch` / `loadAnswerMatch` + 接线**

在 app.js 里 `runUplift`（775 行）/`renderUplift`（814 行）/`loadUplift`（761 行）附近新增（仿 EventSource 与表格写法）：

```javascript
async function loadRoster(){
  try{
    const r = await fetch('/api/v1/eval-roster');
    const j = await r.json();
    const names = (j.roster||[]).filter(e=>e.enabled).map(e=>esc(e.label)).join('、');
    const el = document.getElementById('ansRoster');
    if(el) el.textContent = '参赛选手：' + (names || '（空）');
  }catch(e){ /* 忽略 */ }
}

function runAnswerMatch(){
  const sid = currentSuiteId();           // 换成本文件取当前 suite id 的现有写法
  if(!sid){ toast('先选一个套题'); return; }
  const prog = document.getElementById('ansProg');
  const url = `/api/v1/suites/${sid}/answer-match:run/stream`;
  const es = new EventSource(url);
  function finish(){ es.close(); loadAnswerMatch(); }
  es.onmessage = (ev)=>{
    let d; try{ d = JSON.parse(ev.data); }catch(_){ return; }
    if(d.event === 'case_start'){
      const short = (d.question||'').slice(0, 24);
      prog.innerHTML = `<span class="spin"></span> ${esc(d.label||'')}　第 ${d.i+1}/${d.n} 题：${esc(short)}…`;
    }else if(d.event === 'done'){ prog.textContent = '完成'; finish(); }
    else if(d.event === 'fatal'){ prog.textContent = '失败：' + esc(d.error||''); es.close(); }
  };
  es.onerror = ()=>{ es.close(); };
}

async function loadAnswerMatch(){
  const sid = currentSuiteId();
  if(!sid) return;
  const r = await fetch(`/api/v1/suites/${sid}/answer-match`);
  const j = await r.json();
  if(j.status === 'empty'){ document.getElementById('ansBody').innerHTML = '<div class="muted">还没跑过</div>'; return; }
  renderAnswerMatch(j.metrics);
}

function renderAnswerMatch(m){
  const rows = (m.leaderboard||[]).map(r=>`
    <tr class="ans-row" data-mid="${esc(r.id)}">
      <td>${esc(r.label)}</td>
      <td>${(r.avg_f1*100).toFixed(0)}%</td>
      <td>${(r.avg_coverage*100).toFixed(0)}%</td>
      <td>${(r.avg_precision*100).toFixed(0)}%</td>
      <td>${r.contradictions||0}</td>
      <td>${r.tokens||0}</td>
      <td>${Math.round(r.latency_ms||0)} ms</td>
    </tr>
    <tr class="ans-detail" data-mid="${esc(r.id)}" style="display:none"><td colspan="7"></td></tr>`).join('');
  document.getElementById('ansBody').innerHTML = `
    <table class="tbl">
      <thead><tr><th>模型</th><th>综合分</th><th>覆盖度</th><th>准确度</th><th>硬伤</th><th>token</th><th>时长</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  // 点行展开逐题
  document.querySelectorAll('.ans-row').forEach(tr=>{
    tr.onclick = ()=>{
      const mid = tr.getAttribute('data-mid');
      const det = document.querySelector(`.ans-detail[data-mid="${mid}"]`);
      if(!det) return;
      if(det.style.display !== 'none'){ det.style.display='none'; return; }
      const blob = (m.by_model||{})[mid] || {cases:[]};
      det.querySelector('td').innerHTML = blob.cases.map(c=>`
        <div class="ans-case">
          <div class="q">题：${esc(c.question)}</div>
          <div class="a">答：${esc(c.answer)}</div>
          <div class="s">覆盖 ${(c.coverage_score*100).toFixed(0)}%　遗漏点：${(c.missed_points||[]).map(esc).join('；')||'无'}</div>
          <div class="s">准确 ${(c.precision_score*100).toFixed(0)}%　多余点：${(c.extra_claims||[]).map(esc).join('；')||'无'}</div>
          <div class="s danger">矛盾(硬伤)：${(c.contradictions||[]).map(esc).join('；')||'无'}</div>
        </div>`).join('');
      det.style.display = '';
    };
  });
}
```

在 `flowStepReport` 里 uplift 接线那段（`urb.onclick=runUplift` 附近）追加：

```javascript
  const arb = document.getElementById('ansRunBtn');
  const axb = document.getElementById('ansReloadBtn');
  if(arb) arb.onclick = runAnswerMatch;
  if(axb) axb.onclick = loadAnswerMatch;
  loadRoster();
  loadAnswerMatch();
```

> `currentSuiteId()`、`toast(...)`、`esc(...)`、`.card`/`.tbl`/`.spin`/`.danger` 等都换成/沿用本文件已有的同名工具与 class（`esc` 在 54 行、进度行 class 看 `runUplift`；`.danger` 若无，加一条红字样式或复用现有告警 class）。

- [ ] **Step 5：bump 静态版本号**

改 `runtime_eval/runtime_eval/eval_web/static/index.html` 第 66 行：

```html
<script src="/static/app.js?v=20260616-answer-match-L2"></script>
```

- [ ] **Step 6：跑 SPA 断言确认通过**

Run: `python -m pytest runtime_eval/tests/test_eval_api.py -k spa -v`
Expected: PASS

- [ ] **Step 7：JS 语法自检**

Run: `node --check runtime_eval/runtime_eval/eval_web/static/app.js`
Expected: 无输出（语法 OK）

- [ ] **Step 8：跑全量回归确认绿灯**

Run: `python -m pytest runtime_eval/tests/ -q`
Expected: 全 PASS

- [ ] **Step 9：Commit**

```bash
git add runtime_eval/runtime_eval/eval_web/static/app.js runtime_eval/runtime_eval/eval_web/static/index.html runtime_eval/tests/test_eval_api.py
git commit -m "feat(eval-L2): 前端 L2 卡片——答案对照排行榜 + 逐题下钻 + SSE 进度"
```

---

## 收尾验证（全部任务做完后）

- [ ] 全量测试绿灯：`python -m pytest runtime_eval/tests/ -q`
- [ ] 用户自己重启 eval-api（8800 端口），浏览器打开评测台：L2 卡片出现、参赛选手列出三档、点「跑答案对照」进度行实时刷、跑完出排行榜（综合分排序）、点行能展开逐题（含遗漏/多余/矛盾硬伤高亮）。
- [ ] 小批量真机试跑一套带证据、带黄金答案的题，确认 claude_cli 三档都答得出、对照判稳定；不稳就按设计文档 §10 先用整段判简单版，后续再迭代逐论断。
```

