# 知识库分层评估框架 · 设计方案

> 状态：方案（未动手实现）　日期：2026-06-04
> 目标：把知识库评估拆成三个正交层级，明确各层数据来源与自动化边界。

---

## 1. 三层评估框架

| 层级 | 评什么 | 数据来源 | 黄金/人工 | 自动化诉求 |
| --- | --- | --- | --- | --- |
| **L1 检索层** | 召回本身好不好（命中/排序/精确/覆盖） | 从数据库拉检索片段 | **需人工对照黄金**（保持不变） | **批量化**，省人力 |
| **L2 MCP 工具层** | agent 调 tool 精不精（调没调、query 准不准、调几次） | agent 运行轨迹 | 评估逻辑后续定 | 先**采集存储** |
| **L3 应用层** | 最终回答体验 + token/轮数/时长/成本 | agent 运行轨迹 | 评估逻辑后续定 | 先**采集存储** |

**两条核心原则**

1. **L1 本质不变**：仍是「从数据库拉片段 + 人工对照黄金标注」。只是把整条链批量化、产品化，让人少做重复操作。
2. **L2/L3 当前只采集不打分**：agent 跑一趟产生的所有信息（回答、token、MCP 调用次数与每次的 query、决策轮数、时长、原始 trace）先**如实落库**，作为后续 L2/L3 评估的数据基础。打分逻辑以后再做。

---

## 2. 现状盘点（已具备的能力）

- **批量从 DB 拉取**：`db_source.pull_live_cases(domain/channel/since/limit)` 已能一次拉多条 query 及其排序后的召回片段；`search_similar_questions`（pg_trgm）可在措辞不一致时模糊匹配；`pull_by_query_id` 精确拉单条。
- **合成评测产物**：`build_live_artifacts` 把 LiveCase 转成 `TestSuite + RetrievalSet`，`evaluate_live_retrieval` 直接跑（当前 no-gold，仅精确率族）。
- **打分与报告**：`compute_retrieval_metrics` 已实现 S_q / PASS / 诊断桶 / 难度加权；`render_retrieval_markdown/html` 出报告（含无 gold 优雅降级）。
- **存储**：`Store` 是 project 命名空间下的 JSON 文件存储（suites / responses / runs / retrieval / retrieval_runs + 扁平 index），DB 可在同接口后替换。
- **黄金载体**：`TestCase.expected_evidence / key_points / expected_entities` → `orchestrator.gold_facts()` 汇成 gold_facts。
- **MCP server**：`mcp_server`（`cloud_core_network`）暴露 `search_knowledge`，可被 `claude -p --mcp-config` 挂载。

**缺口**：① 人工标黄金的界面/流程；② 黄金的复用/管理（黄金库）；③ agent 运行轨迹的采集与落库。

---

## 3. Part A — L1 检索层批量化（本次重点）

四个子能力，对应你勾选的四项痛点：

### A1 批量从数据库拉取（≈已有，补编排）
- 复用 `pull_live_cases`，加一个批量入口：按 domain/时间窗/query 清单一次拉 N 条 → 转成**草稿 suite + RetrievalSet**（`build_live_artifacts` 已能做），落库。
- 草稿 suite 的 TestCase 此时 **无 expected_evidence**（待标黄金）。

### A2 人工标黄金的界面/流程（新增，重点）
- **黄金的定义**：对每个 query，人工产出两类标注——
  - `gold_facts`：这个问题的正确答案**必须覆盖的事实/证据点**（写回 `TestCase.expected_evidence`）。
  - （可选）**人工片段相关性标签**：对召回的每个候选片段标 0–3，作为「人工基准」对照 LLM judge，校验裁判一致性。
- **界面**：eval-web 新增「黄金标注」页 —— 左列 query + 召回候选片段，右列填 gold_facts、逐片段打分；支持下一条/保存/跳过。
- **后端**：新增标注读写端点（读草稿 case、写回标注到 suite + 黄金库）。
- **产出**：把草稿 suite 升级为**带黄金的 suite**，可进入 A3 的 gold-aware 评估。

### A3 批量跑打分 + 出报告（≈已有，补编排）
- 已标黄金的 suite → `evaluate_retrieval`（此时 gold_count>0，召回率族 + S_q/PASS 全部生效）→ `compute_retrieval_metrics(scoring)` → `write_retrieval_reports`。
- 仅需一个「批量评估」入口：选一批已标 case → 跑 → 出整体报告（报告模板已就绪：`runtime_eval/docs/评估报告模板.md`）。

### A4 黄金的复用/管理（新增，黄金库）
- **新 store kind `gold`**：`workspace/<project_id>/gold/<fingerprint>.json`。
- **`GoldRecord` 字段**：`query_fingerprint`（归一化 query 指纹）、`query_text`、`gold_facts[]`、可选 `item_labels`、`annotator`、`updated_at`、`version`。
- **复用逻辑**：A1 拉新批次时，对每条 query 算指纹 → 命中黄金库则自动回填 `expected_evidence`（无需重标），未命中才进 A2 待标队列。
- **价值**：黄金从"一次性"变成可积累、可版本化的资产，跨 run 复用。

---

## 4. Part B — Agent 运行轨迹采集（服务 L2/L3，先存不评）

- **采集器 `agent_runner`**：用 `claude -p` + 知识库 MCP 自主作答（复用 `claude_cli.py` 的子进程封装：strip `ANTHROPIC_*` 走订阅、临时目录隔离）。命令：
  ```
  claude -p --mcp-config mcp.json \
    --allowedTools "mcp__cloud_core_network__search_knowledge" \
    --output-format stream-json --verbose
  ```
- **产物 = 结构化轨迹，不是直接打分**。新模型 + 新 store kind `agent_traces`（仿 `ResponseSet` 落 `workspace/<pid>/agent_traces/<suite>.json`）。
- **`AgentTrace` 字段**：
  - `case_id` / `question`
  - `answer`（最终回答）
  - `token_usage`（input/output/cache）
  - `mcp_calls: list[{tool, query, ok}]`（L2 数据基础：调了几次、每次 query 是什么）
  - `num_turns`（决策轮数）、`latency_ms`、`cost_usd`
  - `raw_events`（stream-json 原始事件，审计/回溯用）
- **解析来源**：stream-json 逐行事件 —— `tool_use` 块里 `mcp__` 开头的计入 `mcp_calls`；末尾 `result` 事件取 `usage / num_turns / total_cost_usd / duration`。
- **边界**：本阶段只「跑 + 存」。L2（工具调用精确度）、L3（回答体验）的评分规则后续基于这批 trace 再设计。

---

## 5. 存储一览（全部 JSON，复用现有 Store）

```
workspace/<project_id>/
  suites/<suite>.json           # 现有（草稿 / 带黄金 suite）
  retrieval/<suite>.json        # 现有（召回片段 RetrievalSet）
  retrieval_runs/<run>.json     # 现有（检索层打分结果）
  reports/<run>.{md,html}       # 现有
  gold/<fingerprint>.json       # 新增 A4：可复用黄金库
  agent_traces/<suite>.json     # 新增 Part B：agent 运行轨迹
```

---

## 6. 数据流

**L1 检索层（批量化）**
```
DB ──pull_live_cases──> 草稿 suite + RetrievalSet
   ──算指纹──> [命中黄金库? ── 是 ─> 自动回填 expected_evidence
                          └─ 否 ─> 人工标注界面(A2) ─> 写回 suite + 黄金库]
   ──evaluate_retrieval──> RetrievalRun ──compute_retrieval_metrics──> 报告
```

**L2/L3（采集）**
```
suite.cases ──agent_runner(claude -p + MCP)──> AgentTrace ──> agent_traces store
                                              （后续 L2/L3 评估再消费）
```

---

## 7. 改动清单与阶段

**阶段 0 · 存储迁移 SQLite（地基，建议先做）**
0. `SqliteStore`（同 `Store` 接口）+ 建表 + JSON→DB 迁移脚本 + `EVAL_DB_PATH`/`EVAL_STORE_BACKEND` 配置。先做可让后续 A4 黄金库、Part B 轨迹直接落库。

**阶段 1 · L1 批量化（重点）**
1. `db_source` / 新编排：批量拉 → 草稿 suite 入口。
2. 模型：`GoldRecord`；`Store` 加 `save_gold/get_gold/match_gold`（按指纹）。
3. eval-web：「黄金标注」页；eval-api：标注读写端点 + 指纹回填。
4. 「批量评估」入口：选已标 case → 跑 → 出报告。

**阶段 2 · Agent Trace 采集**
5. 模型：`AgentTrace` / `AgentTraceSet`；`Store` 加 `save/get_agent_traces`。
6. `agent_runner`：stream-json 调 claude -p 挂 MCP，解析落库。
7. `mcp.json` 模板（stdio 自启 `python -m mcp_server`）。
8. **Part C 对话页**：eval-api `POST /chat`（复用 agent_runner 单次）+ SPA「对话」视图（单轮、展示 MCP 调用）。
8b. **Part E 看板**：eval-api `GET /board`、`GET /suites/{id}/board`（join suite+retrieval+run 聚合）+ SPA「看板」视图（可设为首页，支持筛选/展开）。

**阶段 3 · L2/L3 评估（后续，本方案暂不展开）**
9. 基于 agent_traces 设计工具调用精确度 / 回答体验的评分规则。

---

## 7b. Part C — 前端对话页（单轮问答）

让你能直接和「claude -p + 知识库 MCP」这个 agent 对话，直观看它**是否/如何**调用 MCP。

- **后端**：eval-api 新增 `POST /chat`，body `{question, domain?}` → 调 `agent_runner` 的单次封装（与 Part B 共用同一个 runner，只是不落 trace store、即时返回）→ 响应：
  ```json
  {
    "answer": "...",
    "mcp_calls": [{"query": "...", "items": [{"text": "...", "source": "..."}]}],
    "token_usage": {...}, "num_turns": 2, "latency_ms": 1234
  }
  ```
- **前端**：SPA 加「对话」视图 —— 输入框 + 消息流；当 `mcp_calls` 非空，把「检索 query + 返回片段」折叠展示在回答下方，让 agent 的检索行为可见。
- **粒度**：先做**单轮无状态**（每次独立调用 `claude -p`），不维护会话历史。多轮（`--resume` / 拼历史）留作后续。
- **复用**：对话页与 Part B 批量采集**共用同一个 `agent_runner`**，差别仅在「是否落库 + 是否循环」。

## 7bb. Part E — 总览看板（Dashboard）

一张把「用例 → 检索 → 评估 → 分数」全链路状态串起来的看板，让你一眼看清每个测试用例当前进展。

- **数据来源（全部按 `case_id` 聚合现有产物，无需新数据）**：
  - 测试用例 ← `suite.cases`（问题 / 类型 / 难度）
  - 数据库检索如何 ← `RetrievalSet.for_case(case_id)`（是否已拉到片段、`retrieved_count`、首条来源）
  - 是否有评估 ← `RetrievalRun` 是否含该 case 的 `RetrievalCaseResult`
  - 评估打分如何 ← `compute_retrieval_metrics` 的 per-case：`s_q` / `passed` / `bucket` / 子指标
- **两级视图**：
  1. **Suite 列表看板**：有哪些测试集 → 每个的用例数、已检索数、已评估数、平均 KB 分、通过率、是否有黄金。
  2. **Case 明细看板**（点进某 suite）：每行一个用例 ——

     | 问题 | 类型 | 难度 | 检索状态 | 评估状态 | S_q | PASS | 诊断桶 |
     | --- | --- | --- | --- | --- | --- | --- | --- |
     | … | factoid | hard | 已检索(8 片段) | 已评估 | 0.74 | ✅ | 健康 |
     | … | conceptual | normal | 未检索 | 未评估 | — | — | — |

- **状态机**：每个 case 三态串联 —— `待检索 → 已检索/已标黄金 → 已评估(含分数)`；看板用颜色/标记呈现卡在哪一步。
- **后端**：eval-api 加 `GET /board`（suite 列表汇总）与 `GET /suites/{id}/board`（case 明细聚合）；逻辑就是把 suite + retrieval set + retrieval run 三者 join。
- **前端**：SPA 加「看板」视图（默认首页），支持按类型/难度/诊断桶/状态筛选，点行展开看检索片段与裁判评语。
- **与 SQLite 的协同**：Part D 把 `kb_score / pass_rate / overall_accuracy` 抽成独立列，正是为这种跨 case/跨 run 的聚合查询服务，看板查询直接受益。

## 7c. Part D — 存储迁移到 SQLite

把现有 JSON 文件存储换成**本地单文件 SQLite**，`Store` 接口不变（调用方零改动）。

- **位置**：`workspace/eval.db`，路径由 `EVAL_DB_PATH` 配置（默认即此）。
- **建表策略（混合文档表）**：每类产物一张表，主键 = id，`payload` 列存 `model_dump_json()`（贴合现有序列化，迁移成本最低），再把**常用查询/聚合字段抽成独立列**便于 SQL：
  ```
  projects(project_id PK, payload)
  documents(document_id PK, project_id, payload)
  suites(suite_id PK, project_id, backend, created_at, payload)
  responses(suite_id PK, project_id, payload)
  runs(run_id PK, project_id, suite_id, overall_accuracy, payload)
  retrieval_runs(run_id PK, project_id, suite_id, kb_score, pass_rate, payload)
  gold(fingerprint PK, project_id, query_text, payload)         -- A4 黄金库
  agent_traces(suite_id PK, project_id, payload)                -- Part B 轨迹
  reports 仍写文件（md/html），DB 只存路径
  ```
- **收益**：单文件、事务安全、可建索引、可跨 run 用 SQL 聚合（如「按 suite 看历次 kb_score 趋势」），同时仍能直接读 `payload` 还原 pydantic 模型。
- **实现方式**：新增 `SqliteStore`（与现 `Store` 同方法签名），用 `EVAL_STORE_BACKEND=json|sqlite` 切换；或直接让 `Store` 内部走 sqlite。
- **迁移**：写一次性脚本把 `workspace/<pid>/**.json` 灌进 `eval.db`，旧文件保留作备份。
- **取舍**：SQLite 适合本地/单机「临时数据库」诉求；若将来要多人共享，A4/Part D 的同一接口可再换 PostgreSQL。

## 8. 风险与待验证

- **待验证 #1（地基）**：`claude -p` 的 `stream-json + --verbose + mcp` 组合是否如期工作、事件格式是否含 `tool_use` 与 `usage`。动手第一步先用真实命令验证。
- **黄金指纹策略**：归一化规则（去空格/标点/大小写、是否同义改写归并）直接影响复用命中率，需先定简单版（精确归一化）再迭代。
- **人工标注一致性**：可选的「人工片段标签」既是基准也是校验 LLM judge 的依据，建议至少抽样标注。
- **成本/超时**：Part B 多轮工具调用慢且吃订阅额度，`claude_cli_timeout` 调大、先小批量试跑。
- **指标归属**：Part B 的 token/调用量属于「claude + 知识库 MCP」这个自建 agent，不等于第三方 copilot agent 的真实成本。
