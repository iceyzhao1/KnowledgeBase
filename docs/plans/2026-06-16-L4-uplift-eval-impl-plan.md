# L4 业务价值层（对照组增量）· 实现设计

- 日期：2026-06-16
- 状态：实现设计（待动手）
- 指标依据：`docs/architecture/2026-06-03-retrieval-scoring-system-design.md` 第三部分（§14–§15）
- 前提：**判官暂设为准确**（判官校准体系后续单独补，不在本期范围）
- 代码位置：`runtime_eval/`

---

## 1. 目标与范围

量化「**用知识库 vs 不用**」的增量价值，回答「这库到底值不值得建」。

本期范围（MVP）：
- 跑 **两路对照**：A 闭卷（通用模型裸答）、C 用库（现有 agent 跑批）。
- 用**同一个判官**给两路答案打分，**确定性地**算出增量指标 + 诊断桶 + 分层报告。
- B 联网路**后置**（claude CLI 的 WebSearch 在订阅模式下不一定可用，单独验证后再开）。

**不在本期**：pairwise 判官（本期胜率从 score 差确定性推导）、判官校准、L0。

---

## 2. 一图看懂

```
                       ┌─ A 闭卷：claude -p（不挂任何工具）──► 答案A ─┐
suite.cases ──三路──┤                                              ├─► 同一 judge_suite ─► EvalRun_A / EvalRun_C
                       └─ C 用库：claude -p + KB MCP（现有）──────► 答案C ┘
                                                                              │
                                                  compute_uplift(EvalRun_A, EvalRun_C, suite)
                                                                              │
                                          UpliftReport（增量/胜率/负增量率/不可替代率 + 分层 + 诊断桶）
```

核心洞察：**A、C 两路本质都是「`claude -p` + 不同工具」，只差挂不挂 MCP**；打分、聚合全部复用现成的 `judge_suite` / `compute_metrics`。真正要新写的只有「闭卷路」和「两个 run 相减算增量」。

---

## 3. 复用现状（拿来即用，零改动）

| 已有能力 | 文件 | L4 怎么用 |
|---|---|---|
| 用库跑批（C 路）| `orchestrator.run_agent_suite` | 直接当 C 路，产出 `ResponseSet(agent_name)` |
| 答案判官 `/judge` | `service.judge_case` / `LLMClient.judge` | 两路答案同一判官打分（score 0–1）|
| 判一个 suite | `orchestrator.judge_suite` | 每路 `ResponseSet` → `EvalRun` |
| 答案指标聚合 | `metrics.compute_metrics` | 每路单独算 per-type 准确率/均分，作增量底数 |
| 难度字段 | `TestCase.difficulty` | 难度加权聚合 |
| run 档案落库 | `RunSummary(layer=...)` | L4 结果存 `layer='value'`，零模型改动 |

---

## 4. 唯一的硬改动：让 agent 能「闭卷」跑

现状 `run_agent` 强制要求 `agent_mcp_config`（空就抛错），且永远传 `--mcp-config`。闭卷路要的是「不挂任何工具、纯靠模型自身知识作答」。

### 4.1 `eval_llm/agent_runner.py`

`run_agent` 与 `_build_argv` 增加 `route` 参数（`"kb" | "closed_book" | "web"`，默认 `"kb"` 保持现状）：

```python
def _build_argv(config, resolved, *, route="kb") -> list[str]:
    argv = [resolved, "-p", "--output-format", "stream-json", "--verbose"]
    if route == "kb":
        argv += ["--mcp-config", config.agent_mcp_config]
        if config.agent_allowed_tools.strip():
            argv += ["--allowedTools", config.agent_allowed_tools.strip()]
    elif route == "web":
        argv += ["--allowedTools", "WebSearch"]          # 后置：需先验证可用性
    # closed_book：不加 --mcp-config / --allowedTools，claude 无工具裸答
    ...（model / max_turns / permission / extra_args 照旧）
    return argv
```

`run_agent`：
- 仅 `route == "kb"` 时校验 `agent_mcp_config` 非空（闭卷/联网不需要 MCP）。
- 闭卷路系统提示换成「不要假设有检索工具，直接基于你已有的知识作答；不确定就说不确定，别编造」（新增 `DEFAULT_CLOSED_BOOK_SYSTEM`）。
- 闭卷路解析结果天然 `retrieved_items=[]`、`tool_calls=[]`，`parse_stream_json` 无需改。

### 4.2 透传 route 到 API

- `eval_llm/app.py` 的 `/run-agent` body 加可选 `route`（默认 `kb`）。
- `eval_api/llm_client.py` 的 `run_agent(..., route="kb")` 透传。
- `orchestrator.run_agent_suite(..., route="kb", agent_name=...)`：route 传下去；**闭卷路不写 RetrievalSet**（只存 ResponseSet）。

> 改动范围极小：三个文件各加一个直通参数 + 一处条件分支。现有 KB 跑批行为完全不变（默认 `route="kb"`）。

---

## 5. 数据模型

不新增表，复用 `RunSummary`：

- 每路评测照常落 `EvalRun`（已有），`run_id` 用稳定 id 便于覆盖：
  - 闭卷：`run_id=f"run_cb_{suite_id}"`，`agent_name="closed-book"`
  - 用库：`run_id=f"run_kb_{suite_id}"`，`agent_name="kb"`
- L4 增量结果落 `RunSummary`：
  ```python
  RunSummary(
      run_id=f"uplift_{suite_id}", suite_id=..., project_id=...,
      layer="value", kind="uplift",
      metrics={
          "baseline_run_id": "run_cb_<sid>", "treatment_run_id": "run_kb_<sid>",
          "headline": {...}, "by_type": [...], "by_difficulty": [...],
          "cases": [ per-case 增量+桶 ],
          "params": {"tau_low":0.4,"tau_high":0.6,"delta":0.1,"theta_uplift":0.1,"max_regression":0.05},
      },
  )
  ```

---

## 6. L4 指标计算（确定性，从两个 EvalRun 相减）

新文件 `eval_api/uplift_metrics.py`。输入：`EvalRun`（基线 A）、`EvalRun`（处理 C）、`TestSuite`（取难度/类型）。按 `case_id` 对齐。

记 `sa = Score_A`、`sc = Score_C`（均来自 `CaseResult.score ∈ [0,1]`）。参数默认 `τ_low=0.4, τ_high=0.6, δ=0.1`。

**单题诊断桶**（按此优先级判定，互斥）：

| 优先级 | 桶 | 条件 |
|---|---|---|
| 1 | **帮倒忙** regression | `sc < sa − δ` |
| 2 | **不可替代** exclusive | `sa < τ_low 且 sc ≥ τ_high` |
| 3 | **双输** both_fail | `sa < τ_low 且 sc < τ_low` |
| 4 | **锦上添花** boost | `sc > sa + δ` |
| 5 | **平局** tie | 其余（含通用知识：两路都高） |

**聚合指标**（`w_d`：easy=1.0 / medium=1.5 / hard=2.0；N=对齐的题数）：

```
净增量(难度加权)  NetUplift   = Σ w_d·(sc − sa) / Σ w_d
胜率              WinRate     = #{sc > sa + δ} / N
负增量率          RegressionRate = #{sc < sa − δ} / N
不可替代率        Exclusivity = #{exclusive 桶} / N
对联网增量        (B 路开启后补) = NetUplift(C vs B)
```

**headline 四件套** = `NetUplift / WinRate / RegressionRate / Exclusivity`。

**价值 PASS（库值得建）**：`NetUplift ≥ θ_uplift(0.1) 且 RegressionRate ≤ max_regression(0.05)`。

**分层呈现（铁律）**：`by_type`（六类）、`by_difficulty`（三档）各自给 `mean(sc − sa)` + 计数。通用题增量≈0 正常，**卖点在 hard/私有题的增量**，绝不能只看总分。

> 复用 `compute_metrics` 拿到每路的 per-type 均分，但增量必须**按 case_id 对齐后逐题相减**再聚合（不能用两路的均值直接相减，否则丢失逐题配对信息、算不出诊断桶）。

---

## 7. 编排

`orchestrator.py` 新增：

```python
def run_value_uplift(store, client, config, suite, *,
                     routes=("closed_book", "kb"),
                     pass_threshold=None) -> RunSummary:
    runs = {}
    for route in routes:
        agent_name = {"closed_book":"closed-book","kb":"kb","web":"web"}[route]
        rid = {"closed_book":f"run_cb_{suite.suite_id}",
               "kb":f"run_kb_{suite.suite_id}",
               "web":f"run_web_{suite.suite_id}"}[route]
        run_agent_suite(store, client, config, suite, agent_name=agent_name, route=route)
        rs = store.get_responses(suite.suite_id)        # 注意：按 route 分别存/读
        runs[route] = judge_suite(store, client, config, suite, rs, run_id=rid,
                                  pass_threshold=pass_threshold)
    report = compute_uplift(runs["closed_book"], runs["kb"], suite, config)
    store.save_run_summary(report)
    return report
```

> **存储要点**：`run_agent_suite` 当前一个 suite 只存一份 `ResponseSet`（按 suite_id）。两路会互相覆盖。**解决**：给 `ResponseSet`/`save_responses`/`get_responses` 加 `agent_name` 维度（key 改 `<suite_id>__<agent_name>`），或让 `run_agent_suite` 直接返回内存里的 `ResponseSet` 交给 `judge_suite`，**不落覆盖**。本期取后者（更小改动）：`run_agent_suite` 返回 `ResponseSet`，编排里直接喂给 `judge_suite`，每路 `EvalRun` 用各自稳定 `run_id` 落库即可区分。

---

## 8. API 端点（`eval_api/app.py`）

```
POST /api/v1/suites/{sid}/uplift:run     body:{ routes?:["closed_book","kb"], threshold?:0.6 }
     -> 跑两路 + 判分 + 算增量，返回 UpliftReport（metrics）
GET  /api/v1/suites/{sid}/uplift          -> 读已存的 RunSummary(layer='value')
GET  /api/v1/suites/{sid}/uplift/report?format=html|md
```

复用现有依赖注入（`Store` / `LLMClient` / `ApiConfig`）。长耗时（两路各跑一遍 agent）→ 沿用现有逐题驱动/放宽超时的模式。

---

## 9. 前端（`eval_web`）

新增「增量价值」视图（或并进现有报告页一个 tab）：
- **headline 四件套**大数字卡：净增量 / 胜率 / 负增量率 / 不可替代率 + 价值 PASS 徽标。
- **分层条形图**：按题型、按难度的 `mean(sc−sa)`（正绿负红），一眼看出「库在哪类问题上不可替代」。
- **诊断桶分布**：五桶计数（重点高亮「帮倒忙」「双输」，点进去能下钻到 L1/L0）。
- **逐题对照表**：问题 | 难度 | Score_A | Score_C | 增量 | 桶 | 两路答案对比（展开）。

---

## 10. 报告渲染（`eval_api/report.py`）

加一个 `render_uplift_markdown/html`：headline 四件套 → 分层增量表 → 诊断桶分布 → 逐题对照。模板对齐现有检索/生成报告风格。

---

## 11. 阶段拆分

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P1 闭卷路** | `agent_runner` 加 `route`；`run_agent`/`app`/`llm_client` 透传 | 能跑闭卷裸答 |
| **P2 增量计算** | `uplift_metrics.py`（桶 + 聚合，纯函数）+ 单测 | 给两个 EvalRun 算出报告 |
| **P3 编排+API** | `run_value_uplift` + 三个端点 | 一键跑两路出增量 |
| **P4 报告+前端** | `render_uplift_*` + 「增量价值」视图 | 可视化 headline + 分层 |
| **P5 联网路（后置）** | 验证 claude CLI WebSearch 可用性后开 `route="web"` | 三路对照 |

P1+P2 是骨架（且 P2 是纯函数、可先单测），P3 起串起端到端。

---

## 12. 验证

- **P2 单测**：手搓两个 `EvalRun`（覆盖五个桶的边界：`sc<sa−δ` / `sa<τ_low&sc≥τ_high` / 都低 / `sc>sa+δ` / 持平），断言桶归属与四件套数值。难度加权用 easy/hard 各一题验证权重。
- **端到端**：拿现有评测集（如 `数据中心网络搬迁到SDN最佳实践_eval_100.yaml`）跑一次两路，人工抽查：私有领域题 C 明显高于 A（不可替代）、通用常识题两路接近（平局）。
- **回归**：默认 `route="kb"` 下现有 KB 跑批/检索层评测行为不变（跑一遍现有测试）。

---

## 13. 风险

- **成本/时长**：闭卷路虽不调 MCP，但仍走一次 `claude -p`，N 题翻倍耗时与订阅额度。先小批量（10~20 题）试。
- **判官公平性**：本期假设判官准确；但 A、C 答案风格不同（闭卷更泛、用库更具体引用），判官可能有风格偏好——这正是后续「判官校准」要兜的，先记一笔。
- **闭卷答案「看似合理实则编造」**：通用模型对私有领域常一本正经胡说，判官按事实包 F 打分应能压低其分，但需在端到端抽查时确认判官没被「流畅但错误」的答案骗到。
