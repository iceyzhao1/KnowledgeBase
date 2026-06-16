# 通用知识库测试框架 · 架构设计文档（v2）

- 日期：2026-06-02
- 作者：Claude
- 关联任务：TASK-20260421-v11-agent-serving
- 关联计划：`docs/plans/2026-06-02-runtime-eval-framework-impl-plan.md`
- 代码位置（v1 单体实现）：`runtime_eval/`
- 状态：v2 设计稿（前后端 + 大模型三服务拆分），覆盖 v1 单体设计

---

## 修订说明

| 版本 | 日期       | 变更                                                                 |
| ---- | ---------- | -------------------------------------------------------------------- |
| v1   | 2026-06-02 | 单体框架：CLI + 内嵌 FastAPI 前端 + 可插拔 LLM 后端，绑定本仓库场景。 |
| v2   | 2026-06-02 | **改为通用框架**：拆分为**前端服务 / 后端服务 / 大模型服务**三个独立可部署服务；引入"测试项目(Project)"多场景模型；新增**被测查询 token/时长采集**专题与标准回执契约。本文档覆盖 v1。 |

> v1 把出题、上传、裁判、报告、前端揉在一个进程里，与本仓库语料/路径耦合。v2 的目标
> 是做一个**与具体业务场景无关**的通用知识库问答测试平台：任何团队上传自己的测试
> 文档、对接自己的被测系统，都能用同一套服务完成"出题 → 取答 → 评测 → 报告"。

---

## 1. 设计目标与范围

### 1.1 目标

构建一个**通用**的知识库问答测试框架，不绑定任何特定知识库/Agent：

1. 用户上传测试文档 → 系统解析为大模型可读格式 → 大模型阅读后产出**测试用例（查询
   问题 + 示例答案）** 返回给用户；
2. 用户拿测试用例去问**自己的**被测知识库系统，得到回答（一段自然语言搜索结果），
   把回答提供给框架；
3. 大模型依据用例的示例答案，判断被测系统查询到的内容是否正确，框架产出**准确率
   等指标报告**返回给用户；
4. 报告需含**查询时的 token 消耗与查询时长**等运行态成本指标。

### 1.2 通用性要求（v2 新增）

- **被测系统可替换**：通过"被测适配器"对接任意 HTTP/SDK 形式的问答系统，或纯人工
  上传回答，框架核心不感知被测实现。
- **大模型可替换**：出题与裁判用的大模型经独立"大模型服务"抽象，支持 Anthropic /
  OpenAI 兼容 / 自建网关等。
- **多场景隔离**：以"测试项目(Project)"为单位隔离不同业务场景的文档、用例与被测配置。
- **独立部署**：三服务各自容器化，可分别扩缩容、独立升级。

### 1.3 非目标

- 不负责把语料写入任何业务数据库（入库是被测系统侧职责）。
- 不替被测系统做检索/答案合成；框架只"出题、收答、判分"。

---

## 2. 为什么拆成三个服务

| 维度       | 单体(v1) 的问题                          | 三服务(v2) 的收益                                  |
| ---------- | ---------------------------------------- | -------------------------------------------------- |
| 复用       | 前端与编排耦合，难以嵌入别的平台         | 前端可换皮/嵌入；后端可被 CI、脚本、其它系统直接调 |
| 大模型隔离 | LLM 调用混在业务逻辑里                    | 大模型服务统一鉴权/限流/计费/多 Provider，后端纯净 |
| 扩缩容     | 出题(批量重)与前端(轻)同进程             | 后端按批量负载扩，前端按并发扩，互不拖累           |
| 安全       | API key 散落在主进程                      | key 只在大模型服务，前后端不接触密钥               |
| 演进       | 改 LLM 供应商要动主程序                  | 只换大模型服务实现，前后端零改动                   |
| 通用性     | 绑定本仓库语料/路径                      | Project 模型 + 适配器，任意场景接入                 |

---

## 3. 总体架构

```
                        ┌────────────────────────────────────────────────────────────┐
                        │                          用户浏览器                          │
                        └───────────────────────────────┬────────────────────────────┘
                                                         │ HTTPS (REST/JSON)
                        ┌────────────────────────────────▼────────────────────────────┐
   ①前端服务 eval-web   │  上传文档 · 查看/编辑用例 · 上传回答 · 触发评测 · 看报告      │
   (SPA + 轻量静态服务) │  纯展示与交互，不含业务逻辑，全部数据经后端 API              │
                        └────────────────────────────────┬────────────────────────────┘
                                                         │ 内网 REST/JSON
                        ┌────────────────────────────────▼────────────────────────────┐
                        │                     ②后端服务 eval-api                       │
                        │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────┐ │
                        │  │ 文档解析 │ │ 用例编排 │ │ 评测编排 │ │ 指标聚合 / 报告  │ │
                        │  └──────────┘ └────┬─────┘ └────┬─────┘ └──────────────────┘ │
                        │  ┌────────────────────────────────────────────────────────┐ │
                        │  │ 持久化(DB)：Project / Document / Suite / Case /          │ │
                        │  │            Response / Run / Report                       │ │
                        │  └────────────────────────────────────────────────────────┘ │
                        │  ┌────────────────┐         ┌───────────────────────────────┐│
                        │  │ 被测适配器      │         │ 成本采集器 Cost Collector     ││
                        │  │ (可选自动取答)  │         │ (token/时长，见 §8)           ││
                        │  └───────┬─────────┘         └───────────────┬───────────────┘│
                        └──────────┼─────────────────────────┬─────────┼───────────────┘
                                   │ 出题/裁判               │         │ 回查用量
                  ┌────────────────▼─────────────┐           │         │
   ③大模型服务    │        eval-llm               │           │         │
   (Provider 抽象)│  /generate-cases  /judge      │           │         │
                  │  Provider: anthropic |        │           │         │
                  │  openai-compatible | gateway  │           │         │
                  └───────────────────────────────┘           │         │
                                                              ▼         ▼
                                              ┌───────────────────┐ ┌──────────────────────┐
                                              │  被测知识库系统    │ │ 被测侧 LLM 网关/观测  │
                                              │  (用户自有, 外部)  │ │ (如本仓库 llm_service)│
                                              └───────────────────┘ └──────────────────────┘
```

三服务边界清晰：

- **eval-web**：只做展示与交互，无业务逻辑、无密钥。
- **eval-api**：编排中枢，持有全部状态与流程；解析文档、调大模型服务出题/裁判、
  聚合指标出报告；通过被测适配器与成本采集器对接外部被测系统。
- **eval-llm**：大模型能力抽象，统一封装出题/裁判两类调用，隔离 Provider 与密钥。

---

## 4. 三个服务详解

### 4.1 ①前端服务 eval-web

**职责**：用户全部交互入口，纯前端。

页面/能力：

1. **项目管理**：创建/切换测试项目（场景隔离）。
2. **文档上传**：拖拽上传测试文档（pdf/docx/md/txt/html…），展示解析结果预览。
3. **出题**：选文档、设定每类题量与类型，触发出题；展示返回的测试用例（问题 +
   示例答案 + 评分要点 + 出处），支持人工增删改。
4. **回答上传**：逐题粘贴被测系统回答（自然语言文本），并填/导入查询时长、token。
5. **评测**：触发评测；展示总体与分型准确率、时长、token 成本。
6. **报告**：查看/下载 HTML、Markdown 报告，跨运行对比。

**技术**：SPA（可用现有原生 JS，或 Vue/React），由 Nginx/静态服务托管；只调
eval-api 的 REST，不直连大模型服务、不接触密钥。

### 4.2 ②后端服务 eval-api（核心）

**职责**：编排中枢 + 状态持有者。子模块：

| 模块            | 职责                                                                 |
| --------------- | -------------------------------------------------------------------- |
| 文档解析        | 上传文档 → 归一化为 markdown/纯文本 + 结构（标题树），见 §9          |
| 用例编排        | 切分/截断文档 → 调 eval-llm `/generate-cases` → 落库为 Suite/Case   |
| 回答管理        | 接收并存储被测回答(ResponseSet)；支持人工上传或适配器自动取答        |
| 评测编排        | 取 Suite+Response → 调 eval-llm `/judge` → 落库为 Run               |
| 成本采集器      | 采集每次查询的 token/时长（§8）：回执解析 / 网关回查 / 人工录入      |
| 指标聚合        | 计算分型准确率、平均分、时长 P50/P95、token 成本                     |
| 报告            | 渲染 Markdown + HTML，支持归档与对比                                 |
| 被测适配器(可选)| 把"取回答"自动化：按配置 HTTP 调用被测系统并掐表                     |
| 持久化          | DB 存 Project/Document/Suite/Case/Response/Run/Report                |

**技术**：Python + FastAPI（与仓库技术栈一致）；DB 用 PostgreSQL（生产）/ SQLite
（轻量）。无状态进程，状态全在 DB，便于水平扩展。

### 4.3 ③大模型服务 eval-llm

**职责**：把"出题"和"裁判"两类大模型调用抽象成稳定的领域接口，隔离 Provider 与密钥。

接口（领域语义，而非裸 chat）：

- `POST /generate-cases`：输入文档文本 + 类型/题量 + 操作员人设，输出结构化测试用例
  + token 用量。
- `POST /judge`：输入问题 + 示例答案/要点 + 被测回答，输出 verdict/score/命中要点 +
  token 用量。
- （可选）`POST /chat`：通用回退。

**Provider 抽象**：`anthropic` / `openai-compatible` / `gateway`（对接组织已有 LLM
网关，如本仓库 `llm_service`）。**密钥只存在于此服务**。

> 为什么用"领域接口"而非透传 chat：把出题/裁判的 prompt 模板、JSON schema、温度、
> 重试、解析容错收敛在大模型服务内，后端只表达意图，便于统一治理与 A/B 模板。

---

## 5. 两条核心数据流

### 5.1 出题流（文档 → 测试用例）

```
用户 ─上传文档─► eval-web ─POST /projects/{p}/documents─► eval-api
                                          │ 解析归一化(§9)
                                          ▼
用户 ─触发出题─► eval-web ─POST /projects/{p}/suites:generate─► eval-api
                                          │ 切分/截断
                                          ▼
                          eval-api ─POST /generate-cases─► eval-llm ─► Provider
                                          ◄── cases + usage ──┘
                                          │ 落库 Suite/Case，记录出题 token
                          eval-web ◄── 测试用例列表 ── eval-api
用户在前端增删改用例（可选）
```

### 5.2 评测流（回答 → 报告）

```
用户拿用例去问"自有被测系统"，得到自然语言回答
        │
        ├─(A)人工上传：eval-web ─POST /suites/{s}/responses─► eval-api
        └─(B)自动取答：eval-api 被测适配器 ─HTTP─► 被测系统（同时掐表得时长）
                                          │
                          成本采集器补齐 token/时长（§8）
                                          ▼
用户 ─触发评测─► eval-web ─POST /suites/{s}:judge─► eval-api
                                          │
                          eval-api ─POST /judge（逐题）─► eval-llm ─► Provider
                                          ◄── verdict/score/usage ──┘
                                          │ 落库 Run；聚合指标；渲染报告
                          eval-web ◄── 报告(准确率/时长/token) ── eval-api
```

---

## 6. 服务间接口契约（REST/JSON）

### 6.1 前端 → 后端（eval-api）

```
# 项目
POST   /api/v1/projects                      创建测试项目
GET    /api/v1/projects                      列出项目

# 文档
POST   /api/v1/projects/{pid}/documents      上传文档(multipart) -> 解析入库
GET    /api/v1/projects/{pid}/documents      列出文档(含解析状态)
GET    /api/v1/documents/{did}/preview       解析后文本预览

# 出题
POST   /api/v1/projects/{pid}/suites:generate
       body: { document_ids:[...], types:[...], per_type:3, persona?:"..." }
       -> { suite_id, cases:[...], generation_usage }
GET    /api/v1/suites/{sid}                  取用例
PATCH  /api/v1/suites/{sid}/cases/{cid}      人工修订用例

# 回答
POST   /api/v1/suites/{sid}/responses
       body: { agent_name, answers:[ { case_id, answer, latency_ms?, token_usage? } ] }
GET    /api/v1/suites/{sid}/responses

# 评测与报告
POST   /api/v1/suites/{sid}:judge            body: { threshold?:0.6 } -> { run_id, metrics }
GET    /api/v1/runs/{rid}/metrics            指标 JSON
GET    /api/v1/runs/{rid}/report?format=html|md
```

### 6.2 后端 → 大模型服务（eval-llm）

```
POST /generate-cases
  body: { document_text, doc_ref, types:[...], per_type, persona }
  resp: { cases:[ {question, question_type, expected_answer, key_points, source, difficulty} ],
          usage:{prompt,completion,total} }

POST /judge
  body: { question, question_type, expected_answer, key_points, agent_answer }
  resp: { verdict, score, rationale, covered_points, missed_points,
          usage:{prompt,completion,total} }
```

---

## 7. 数据模型（多场景）

```
Project(1) ──< Document(n)
Project(1) ──< Suite(n) ──< Case(n)
Suite(1)   ──< ResponseSet(n) ──< AgentResponse(n)   # 同一 suite 可多次/多 Agent 测
Suite(1)   ──< Run(n) ──< CaseResult(n)              # 每次评测一个 Run
Run(1)     ──  Report
```

- **Project**：场景隔离单元，含被测系统配置、默认 Provider 等。
- **Document**：原始文件 + 解析后文本 + 结构。
- **Case**：问题/类型/示例答案/评分要点/出处(SourceRef)/难度（沿用 v1 模型）。
- **AgentResponse**：被测回答文本 + `latency_ms` + `token_usage`（来源见 §8）。
- **CaseResult**：verdict/score/命中缺失要点/双方 token（被测 vs 框架裁判分列）。

问题类型沿用 v1：`factoid/conceptual/procedural/troubleshooting/navigational`。

---

## 8. 【专题】查询的 token 与时长从哪里获取

> 这是你提出的核心问题。先给结论，再给方案与落地。

### 8.1 结论

- **查询时长（latency）**：**推荐由 eval-api 在发起查询时直接掐表（wall-clock）**。
  这是端到端真实耗时（含检索 + 重排 + 答案合成），且**完全不需要被测系统配合**。
  若被测仅人工触发（无自动适配器），则由用户从自己日志里读出后**人工录入**。
- **Token 消耗**：**无法从框架外部观测**——你看不到别人进程里烧了多少 token。它**只能
  由被测侧产生**，框架有三种获取路径（按推荐度排序）：
  1. **被测回执自带 usage（首选）**——被测系统在回答里返回 `usage`；
  2. **统一 LLM 网关回查**——被测的所有 LLM 调用都过一个网关（本仓库的
     `llm_service` 已逐任务记录 token 与 latency），框架用 `trace_id` 关联回查求和；
  3. **人工录入（兜底）**——用户从被测侧账单/日志读出后填入。

### 8.2 三种来源对比

| 来源                 | token | 时长 | 需被测改造 | 精度         | 适用                         |
| -------------------- | ----- | ---- | ---------- | ------------ | ---------------------------- |
| ①被测回执 envelope   | ✔     | ✔    | 中(加字段) | 高(逐请求)   | 被测可改、API 化             |
| ②统一 LLM 网关回查   | ✔     | △    | 低(传 id)  | 高(逐调用聚合)| 被测的 LLM 调用集中走网关     |
| ③eval-api 掐表       | ✘     | ✔    | 无         | 高(端到端)   | 时长的最佳来源，token 拿不到 |
| ④人工录入            | ✔     | ✔    | 无         | 低(依赖人)   | 任何情况兜底                 |

**推荐组合：时长用 ③（掐表）+ token 用 ①或②**。三者都拿不到时退 ④。

### 8.3 方案一：被测回执标准契约（首选，框架定义）

约定被测系统（或其前置适配层）在回答时返回统一**回执 envelope**：

```jsonc
{
  "answer": "……一段自然语言搜索结果……",
  "trace_id": "q-20260602-abc123",           // 关联本次查询的所有底层调用
  "metrics": {
    "latency_ms": 1320,                        // 被测自报端到端耗时(可选, 框架也会掐表)
    "token_usage": { "prompt": 900, "completion": 300, "total": 1200 },
    "stage_breakdown": [                       // 可选：分阶段成本
      { "stage": "query_understanding", "tokens": 200, "latency_ms": 180 },
      { "stage": "rerank",              "tokens": 150, "latency_ms": 90 },
      { "stage": "answer_synthesis",    "tokens": 850, "latency_ms": 1050 }
    ]
  }
}
```

绝大多数检索/Agent 框架本就能产出 usage（Anthropic/OpenAI 响应带 `usage`，LangChain
有 callback）。被测只需把这些聚合进 envelope。eval-api 的被测适配器解析此结构即可。

### 8.4 方案二：统一 LLM 网关回查（本仓库落地）

当被测不便改回执，但其 LLM 调用集中经过一个网关时：本仓库的 **`llm_service`** 已经
**逐任务记录** `prompt_tokens / completion_tokens / total_tokens / latency_ms`
（见 `llm_service/models.py` 的 `AttemptDetail`、`ExecuteResponse`）。机制：

```
eval-api 生成 trace_id ──► 注入到对被测的查询请求
        │
被测系统处理查询，其每次 LLM 调用都把 trace_id 写进
        llm_service 任务的 metadata.caller_context.request_id
        │
查询返回后，eval-api 成本采集器 ──查询──► llm_service：
        "给我 trace_id=X 的所有任务 token 与 latency"
        ◄── 求和 ──► 得到本次查询的 token 合计（latency 仍以 eval-api 掐表为准）
```

落地需要两点约定：
1. 被测系统把 eval-api 传入的 `trace_id` 透传到它对 `llm_service` 的每次调用
   （写入 `metadata.caller_context.request_id` 或新增 `trace_id` 字段）；
2. `llm_service` 暴露一个"按 trace_id/request_id 聚合用量"的查询端点（当前有
   逐任务记录，需补一个聚合查询 API）。

### 8.5 方案三：eval-api 掐表（时长的最佳来源）

无论被测是否自报，只要走**自动适配器**，eval-api 在发出请求前后取单调时钟差即得
端到端时长——这比被测自报更可信（含网络与排队）。伪代码：

```python
t0 = time.monotonic()
resp = target_adapter.query(question, trace_id=tid)   # 调用被测系统
latency_ms = (time.monotonic() - t0) * 1000
usage = parse_envelope(resp) or gateway.usage_by_trace(tid) or None  # token 走§8.3/8.4
```

### 8.6 当前阶段（人工上传）如何采集

你描述的当前形态是"用户提供回答"。此时：

- **时长**：用户从被测系统自己的日志/响应里读出，前端逐题填入 `latency_ms`；
- **token**：同理由用户填入 `token_usage`；
- 待被测对接自动适配器后，自动切换到 §8.3/§8.5 的自动采集，前端字段变为只读回显。

### 8.7 被测查询服务"本地启动"对采集的影响（关键前提）

> 补充前提：被测知识库查询服务会**在本地启动**（与 eval-api、`llm_service` 同机/同
> 内网）。这把自动采集从"未来演进"提前为"当下即可落地"，成为推荐主路径：

- **时长（立即可用）**：eval-api 的被测适配器直接 HTTP 调用本地被测服务并**掐表**
  （§8.5），端到端时长当场拿到，无需人工录入。
- **token（立即可用）**：本地被测系统的 LLM 调用走的就是**本地 `llm_service`**——
  eval-api 用 `trace_id` 关联、回查本地 `llm_service` 即可求和（§8.4），无需被测改
  回执。唯一前置工作是给本地 `llm_service` 补一个"按 trace_id 聚合用量"端点。
- **人工上传降级为兜底**：仅当被测服务未起或未接适配器时使用。

因此本地部署下，**推荐"自动适配器掐表 + 本地 `llm_service` 回查"组合**，§8.3 的回执
契约作为被测愿意自报时的更优解。对应部署见 §11 的本地形态。

---

## 9. 文档解析子系统

上传文档 → 归一化为大模型可读文本，位于 eval-api。

- **输入格式**：`.md/.txt/.html/.pdf/.docx`（可扩展 `.chm/.hdx`）。
- **输出**：归一化 markdown/纯文本 + 结构（标题树，供 SourceRef 定位）。
- **接口**：`Parser.parse(file) -> ParsedDoc{ text, sections[], meta }`，按格式分发到
  具体解析器；可复用本仓库 `knowledge_mining_zym` 的 preprocessing 思路（但作为
  独立实现，不 import 业务模块，保持通用）。
- **长文处理**：按标题树切块，超长块再按 `max_chars` 截断，分块分别出题，避免吃满
  上下文。

---

## 10. 技术选型

| 服务      | 技术                                   | 说明                                  |
| --------- | -------------------------------------- | ------------------------------------- |
| eval-web  | SPA(原生/Vue/React) + Nginx 静态托管   | 无密钥，仅调 eval-api                 |
| eval-api  | Python 3.10+ / FastAPI / SQLAlchemy    | 与仓库栈一致；PostgreSQL 或 SQLite    |
| eval-llm  | Python / FastAPI                       | Provider 抽象；密钥仅存于此           |
| 解析      | markdown-it-py / pdfminer / python-docx| 复用仓库现有解析依赖                  |
| 部署      | Docker Compose（开发）/ K8s（生产）    | 三服务各一镜像                        |

---

## 11. 部署形态

```yaml
# docker-compose（示意）
services:
  eval-web:   { build: ./web,   ports: ["8080:80"],   env: [EVAL_API_URL] }
  eval-api:   { build: ./api,   ports: ["8700:8700"], env: [DB_URL, EVAL_LLM_URL] }
  eval-llm:   { build: ./llm,   ports: ["8780:8780"], env: [LLM_PROVIDER, ANTHROPIC_API_KEY, ...] }
  db:         { image: postgres:16, volumes: [pgdata] }
```

- 仅 eval-web 对外暴露；eval-api/eval-llm 内网。
- 密钥经环境变量/密钥管理注入 eval-llm。

**本地形态（被测服务在本地起，§8.7）**：被测查询服务与 `llm_service` 也在同机/同
内网，eval-api 直接按内网地址访问：

```
eval-web   :8080  ─► eval-api :8700 ─┬─► eval-llm        :8780  (出题/裁判)
                                     ├─► 被测查询服务    :<本地端口> (自动取答 + 掐表)
                                     └─► llm_service     :8900  (按 trace_id 回查被测 token)
```

- eval-api 配置项：`TARGET_QUERY_URL`（本地被测服务地址）、`LLM_SERVICE_URL`
  （本地 `llm_service`，用于 token 回查）。

---

## 12. 配置与安全

- **密钥隔离**：LLM 密钥只在 eval-llm；前后端无密钥。
- **被测凭据**：被测系统访问凭据存 Project 配置（加密存储），仅 eval-api 适配器使用。
- **鉴权**：eval-web↔eval-api 走会话/Token；服务间走内网 + service token。
- **多租户**：Project 维度隔离数据与配额。

---

## 13. 从现有 v1 单体迁移

| v1 单体模块                 | v2 归属                                  |
| --------------------------- | ---------------------------------------- |
| `generator.py`              | eval-api 用例编排 + eval-llm `/generate-cases` |
| `judge.py`                  | eval-api 评测编排 + eval-llm `/judge`    |
| `llm/*` 后端抽象            | 下沉为 eval-llm 的 Provider 层           |
| `metrics.py` / `report.py`  | eval-api 指标/报告模块（基本照搬）        |
| `web/`(FastAPI+SPA)         | 拆为 eval-web(纯前端) + eval-api(REST)   |
| `store.py`(JSON 文件)       | 升级为 DB 持久化（保留 JSON 导入导出）   |
| `corpus.py`                 | eval-api 文档解析子系统(§9)              |

> 迁移可渐进：先把 `llm/*` 抽成 eval-llm 独立服务（接口不变），再把 `web` 拆出纯
> 前端，最后把文件存储换 DB。v1 的数据模型(`models.py`)与报告渲染可大量复用。

---

## 14. 演进路线

1. **被测适配器**：HTTP/SDK 自动取答 + §8 自动采集 token/时长，告别人工上传。
2. **LLM 网关用量聚合 API**：给 `llm_service` 补"按 trace_id 聚合用量"端点（§8.4）。
3. **IR 客观指标**：用 SourceRef 黄金来源算 precision@k/MRR，与 LLM 裁判互证。
4. **多运行对比 / 趋势**：跨版本、跨被测系统对比看板。
5. **裁判校准**：人工抽样与 LLM 裁判一致性核对，校准阈值与模板。
6. **配额与计费**：按 Project 统计框架自身 LLM 成本，支持多租户计费。
```
