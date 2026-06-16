# runtime_eval · 通用知识库测试框架（三服务）

一个**通用**的运行态测试框架，量化评测**被测问答 Agent（copilot 形态）在运行态的真实
回答质量**。不绑定任何单一业务场景：上传任意测试文档即可出题、评测、出报告。

> 📖 **想直接上手用？** 看手把手操作手册 [`使用指南.md`](使用指南.md)（从零启动→出题→
> 评测→出报告）。本 README 偏**技术参考**（架构、接口契约、持久化、指标定义）。

> 架构设计与取舍详见
> [`docs/architecture/2026-06-02-runtime-eval-framework-architecture.md`](../docs/architecture/2026-06-02-runtime-eval-framework-architecture.md)；
> 开发方案见
> [`docs/plans/2026-06-02-runtime-eval-framework-impl-plan.md`](../docs/plans/2026-06-02-runtime-eval-framework-impl-plan.md)。

---

## 目录

- [1. 它解决什么问题](#1-它解决什么问题)
- [2. 三服务架构](#2-三服务架构)
- [3. 工作流程总览](#3-工作流程总览)
- [4. 安装与环境](#4-安装与环境)
- [5. 配置 .env](#5-配置-env)
- [6. 启动两个服务](#6-启动两个服务)
- [7. 前端使用流程](#7-前端使用流程)
- [8. 服务接口契约](#8-服务接口契约)
- [9. 持久化布局](#9-持久化布局)
- [10. 指标说明](#10-指标说明)
- [11. 切换到真实 Claude](#11-切换到真实-claude)
- [12. 测试](#12-测试)
- [13. 常见问题](#13-常见问题)

---

## 1. 它解决什么问题

我们能跑通挖掘管道、能召回证据，但缺一把尺子衡量"问一个真实问题，最终答案对不对、
快不快、贵不贵"。本框架做三件事：

1. **出题**：外接大模型（Claude）以**一线操作员**身份阅读上传的测试文档，按问题类型
   构造真实查询，并给出**期望答案 + 评分要点 + 出处**。
2. **取回回答**：把题目交给被测 copilot Agent，得到自然语言回答。**当前回答经前端
   人工上传**（被测 Agent 接口尚未约定，先解耦）。
3. **评测出报告**：LLM 裁判把回答与期望答案比对打分，产出**按问题类型区分的准确率、
   token 消耗、查询时长**等指标，并渲染 Markdown + HTML 报告。

> **为什么是 LLM 裁判？** serving 的 `/api/v1/search` 只返回检索证据包，真正面向
> 操作员的自然语言答案由下游 copilot agent（调 MCP）组织。框架以**最终自然语言
> 答案**为评测对象，故采用 **LLM-as-judge** 语义裁判。

---

## 2. 三服务架构

| 服务         | 包                       | 职责                                                        | 默认端口 |
| ------------ | ------------------------ | ----------------------------------------------------------- | -------- |
| **eval-llm** | `runtime_eval.eval_llm`  | 大模型服务：`/generate-cases`、`/judge`，封装 Provider 与密钥 | 8801     |
| **eval-api** | `runtime_eval.eval_api`  | 后端：REST 编排项目/文档/用例/回答/评测/报告，HTTP 调 eval-llm，持久化，并托管 eval-web | 8800     |
| **eval-web** | `runtime_eval.eval_web`  | 纯前端 SPA，仅调 eval-api（由 eval-api 在 `/` 托管）        | —        |

依赖方向：`eval-web → eval-api → eval-llm`。只有 eval-llm 持有模型密钥。

包内还有一个 `shared/`（跨服务纯领域模型 + JSON 容错解析），无 web 依赖。

---

## 3. 工作流程总览

```
 前端新建项目
     │  ① 上传测试文档（.md/.txt）→ eval-api 解析为纯文本 + 分节
     ▼
 workspace/<project_id>/documents/<doc>.json
     │  ② 出题：eval-api 调 eval-llm /generate-cases（操作员视角分型出题）
     ▼
 workspace/<project_id>/suites/<suite>.json
     │  ③ 把题目交给被测 copilot agent，人工把回答上传回前端
     ▼
 workspace/<project_id>/responses/<suite>.json
     │  ④ 评测：eval-api 调 eval-llm /judge 逐题打分
     ▼
 workspace/<project_id>/runs/<run>.json  +  reports/<run>.{md,html}
```

---

## 4. 安装与环境

**Python 必须 ≥ 3.10**（pydantic 运行期需要）。本机默认 `py` 是 3.9，请显式用
`py -3.10`（或任意 ≥3.10 的解释器）。

核心依赖（fastapi / uvicorn / pydantic / httpx / python-dotenv）已包含在**仓库根**
`requirements.txt`；本框架额外需要 `python-multipart`（文档上传走 multipart），
列在 `runtime_eval/requirements.txt`：

```bash
pip install -r requirements.txt                 # 仓库根：核心依赖
pip install -r runtime_eval/requirements.txt    # 本框架增量（python-multipart 等）
```

仅当使用 Anthropic 直连且想用官方 SDK 时（可选，不装则自动走 httpx 裸调用）：

```bash
pip install anthropic
```

> 所有命令都从 **`runtime_eval/` 目录**执行（包名也叫 `runtime_eval`，位于
> `runtime_eval/runtime_eval/`）。Windows 下若 `-m` 找不到包，设置 `PYTHONPATH`
> 指向 `runtime_eval/` 目录即可（见 §13）。

---

## 5. 配置 .env

```bash
cp .env.example .env        # 在 runtime_eval/ 下
```

`.env` 关键项：

| 变量                      | 服务     | 取值 / 默认                          | 说明                          |
| ------------------------- | -------- | ------------------------------------ | ----------------------------- |
| `EVAL_LLM_PROVIDER`       | eval-llm | `mock` / `anthropic` / `llm_service` / `claude_cli` | 出题与裁判用哪个 Provider |
| `ANTHROPIC_API_KEY`       | eval-llm | —                                    | anthropic provider 必填       |
| `ANTHROPIC_BASE_URL`      | eval-llm | （可选）                             | 走中转网关时填                |
| `EVAL_ANTHROPIC_MODEL`    | eval-llm | `claude-sonnet-4-5`                  | anthropic 模型                |
| `EVAL_LLM_SERVICE_URL`    | eval-llm | `http://localhost:8900`              | llm_service 地址              |
| `EVAL_CLAUDE_BIN`         | eval-llm | `claude`                             | claude_cli：claude 命令/全路径 |
| `EVAL_CLAUDE_MODEL`       | eval-llm | （空=claude 默认）                   | claude_cli：指定模型          |
| `EVAL_CLAUDE_TIMEOUT`     | eval-llm | `600`                                | claude_cli：单次调用超时(秒)  |
| `EVAL_CLAUDE_EXTRA_ARGS`  | eval-llm | （可选）                             | claude_cli：追加 claude 原始参数 |
| `EVAL_TEMPERATURE`        | eval-llm | `0.2`                                | 出题温度（裁判恒为 0）        |
| `EVAL_LLM_URL`            | eval-api | `http://localhost:8801`              | eval-api → eval-llm 地址      |
| `EVAL_PER_TYPE`           | eval-api | `3`                                  | 每篇文档每种类型出题数        |
| `EVAL_PASS_THRESHOLD`     | eval-api | `0.6`                                | 通过阈值                      |
| `EVAL_REQUEST_TIMEOUT`    | 两者     | `120/180`                            | LLM/HTTP 请求超时（秒）       |
| `EVAL_MAX_DOC_CHARS`      | eval-api | `24000`                              | 单文档送 LLM 的最大字符数     |
| `EVAL_WORKSPACE_DIR`      | eval-api | `runtime_eval/workspace`             | 产物目录（可覆盖）            |

两个服务可共用同一份 `.env`。

---

## 6. 启动两个服务

开两个终端（先起 eval-llm，再起 eval-api）：

```bash
cd runtime_eval

# 终端 1：大模型服务（默认 8801）
py -3.10 -m runtime_eval.eval_llm --host 127.0.0.1 --port 8801

# 终端 2：后端 + 前端（默认 8800，自动托管 eval-web）
py -3.10 -m runtime_eval.eval_api --host 127.0.0.1 --port 8800
```

打开 <http://127.0.0.1:8800> 即进入前端。`mock` provider 无需网络、无需 key，可直接
跑通全链路用于演示与验证。

---

## 7. 前端使用流程

1. **新建项目**（左上输入名称 → `+ 新建项目`）。
2. **上传文档**：选 `.md`/`.txt` → `上传并解析`。eval-api 解析为纯文本 + 分节。
3. **出题**：勾选要覆盖的问题类型与「每类每文档」题数 → `生成测试集`。eval-api 调
   eval-llm 出题，右侧列出题目（可展开看期望答案/要点/出处）。
   - 也可在「**导入已有用例**」卡片上传现成的 YAML/JSON 用例集（跳过出题，无需
     eval-llm）。详见 §8.1。
4. **填回答**：逐题在文本框**粘贴被测 Agent 回答**，并填**查询时长(ms)** 与
   **Agent token** → `保存回答`。
5. **评测**：`运行评测` 触发裁判 + 出报告，顶部显示总体/分型准确率。
6. **看报告**：`↗ 打开完整报告` 查看 HTML 报告。
7. **检索层（可选）**：顶部切到「**检索层（证据召回）**」，逐题把检索器返回的证据
   **按排名每行一条**粘贴 → `运行检索评测`，得到 HitRate/Recall/MRR/NDCG/Context
   Precision/Context Recall 等 IR 指标与报告。详见 §8.2。

> 前端只调 eval-api，不含任何业务逻辑与密钥。

---

## 8. 服务接口契约

### eval-llm

```
GET  /health                  -> {status:"ok", provider:"..."}
POST /generate-cases
  body { document_text, doc_ref, types[], per_type, persona? }
  -> { cases:[{question,question_type,expected_answer,key_points,source,difficulty}],
       usage:{prompt,completion,total} }
POST /judge
  body { question, question_type, expected_answer, key_points, agent_answer }
  -> { verdict, score, rationale, covered_points, missed_points, usage:{...} }
POST /judge-retrieval
  body { question, expected_answer, gold_facts[], items[] }
  -> { item_grades[0-3 按排名], gold_covered_at[每条黄金事实首次被覆盖的排名,0=未覆盖],
       rationale, usage:{...} }
```

### eval-api（前端消费）

```
POST /api/v1/projects                         { name } -> {project_id}
GET  /api/v1/projects
POST /api/v1/projects/{pid}/documents         multipart(file) -> {document_id, sections}
GET  /api/v1/projects/{pid}/documents
POST /api/v1/projects/{pid}/suites:generate   { document_ids[], types[], per_type } -> suite
POST /api/v1/projects/{pid}/suites:import      multipart(file: YAML/JSON) -> suite
GET  /api/v1/suites/{sid}
POST /api/v1/suites/{sid}/responses           { agent_name, answers[] }
GET  /api/v1/suites/{sid}/responses
POST /api/v1/suites/{sid}/judge               { threshold? } -> { run_id, metrics }
GET  /api/v1/runs/{rid}/metrics
GET  /api/v1/runs/{rid}/report?format=html|md
# --- 检索层（IR 指标）---
POST /api/v1/suites/{sid}/retrieval           { agent_name, items{cid:[{rank,text,source?}]} }
POST /api/v1/suites/{sid}/retrieval:import    multipart(file: YAML/JSON evidence)
GET  /api/v1/suites/{sid}/retrieval
POST /api/v1/suites/{sid}/retrieval:evaluate  { k_values? } -> { run_id, metrics }
GET  /api/v1/retrieval-runs/{rid}/metrics
GET  /api/v1/retrieval-runs/{rid}/report?format=html|md
GET  /                                         eval-web SPA
```

### 8.1 导入已有用例（suites:import）

把外部整理好的用例集（YAML/JSON）直接导入为一个 suite，跳过 eval-llm 出题。
顶层接受 `questions:` 或 `cases:` 列表，或一个裸列表。每条按以下字段映射（取首个存在者）：

| 框架字段 | 来源字段 |
|---|---|
| `id` | `id` / `case_id`（缺省自动 `q001…`） |
| `question` | `question` / `query` |
| `question_type` | `question_type` / `type`（按 §10 类型校验，含 `constraint`；可识别少量同义词） |
| `expected_answer` | `expected_answer` / `answer` |
| `key_points` | `key_points`，否则合并 `expected_evidence_contains` + `expected_entities` |
| `source.section` | `source_section` / `source.section` |
| `source.doc` | `source.doc`，否则取上传文件名 |
| `difficulty` / `notes` | 同名字段 |

> YAML 导入需 `pyyaml`（见 `requirements.txt`）；未安装时仍可导入 `.json`。
> 未知 `question_type`、缺字段、重复 id 等会返回 400 并指出具体行号。
>
> 导入时还会把 `expected_evidence`（或 `expected_evidence_contains`）与
> `expected_entities` 落到结构化字段，供**检索层评测**作黄金证据使用（见 §8.2）。

### 8.2 检索层评测（IR 指标）

衡量"被测检索器召回的证据包对不对、排得好不好"，与生成层（回答质量）相互独立、
可单独跑。流程：**人工上传每条用例的检索证据 → 全 LLM 相关性裁判 → 自动算 IR 指标**。

1. 上传证据：前端切到"检索层"，每条用例把检索器返回的证据**按排名每行一条**粘进去；
   或调 `POST /suites/{sid}/retrieval`（JSON）/`retrieval:import`（文件）。
   文件接受 `{agent_name?, items:{cid:[...]}}`、裸 `{cid:[...]}`、或 `[{case_id, items:[...]}]`；
   每个 item 可为字符串或 `{rank?, text, source?}`。
2. 评测：`POST /suites/{sid}/retrieval:evaluate { k_values? }`（默认 K=1,3,5,10）。
   eval-llm 对每条用例给每个证据条目打 0–3 相关性等级，并判定每条黄金事实最早被哪个
   条目覆盖；HitRate/Recall/MRR/NDCG/ContextPrecision/ContextRecall 由这些标签确定性算出
   （一次裁判可报多个 K）。
3. 黄金事实来源：用例的 `expected_evidence`（缺省退回 `key_points`）加 `expected_entities`。

> 与生成层共用 eval-llm 与项目/用例，不需要被测服务在线——证据同样由人工上传。

#### 8.2.1 拉真实查询日志评测（精确率族，无需黄金标注）

不想人工上传、也没有黄金标注时，可直接拉被测检索器的**真实流量**（`serving_query_logs`）
做检索层评测：

```
POST /api/v1/projects/{pid}/retrieval/live:evaluate
     { domain?, channel?, since?, limit?, k_values? }
  -> { run_id, suite_id, pulled_cases, metrics }
```

框架按 query 重建「问题 + 按排名的证据条目」：问题取 `query_text`；证据文本用
item id JOIN `asset_retrieval_units.text`（日志本身不存文本）；篇章路径取
`relative_path`；时长取 `duration_ms`。随后由 eval-llm 逐条目按**与问题的相关性**
打 0–3 分。

- 先在 `.env` 配 `SERVING_PG_*`（见 `.env.example`），未配置该接口返回 503。
- 真实日志**无黄金标注**，因此只产出**精确率族**指标：HitRate / MRR / NDCG /
  ContextPrecision（+ 查询时长）；**召回率族**（Recall@K / ContextRecall）在报告中
  标记为 `N/A（需黄金标注，后续开发）`。
- 产物与普通检索层 run 完全一致（同样的 `retrieval-runs/{rid}/metrics|report`）。

---

## 9. 持久化布局

文件存储，按项目命名空间（DB 为后续替换，接口不变）：

```
workspace/
  projects.json                       # 所有项目
  index.json                          # suite_id / run_id -> project_id 反查
  <project_id>/
    documents/<document_id>.json      # Document（解析后纯文本 + 分节）
    suites/<suite_id>.json            # TestSuite
    responses/<suite_id>.json         # ResponseSet（人工上传回答）
    runs/<run_id>.json                # EvalRun（生成层裁判产出）
    retrieval/<suite_id>.json         # RetrievalSet（人工上传检索证据）
    retrieval_runs/<run_id>.json      # RetrievalRun（检索层裁判产出）
    reports/<run_id>.{md,html}        # 渲染报告（生成层 + 检索层共用目录）
```

未填 `answer`（空串）的题会被判为 `missing`、计 0 分。

---

## 10. 指标说明

| 指标                  | 定义                                                          |
| --------------------- | ------------------------------------------------------------- |
| **按类型准确率**      | 每类 `通过数 / 总数`；score ≥ 阈值(默认 0.6) 计通过；未答计不通过 |
| **总体准确率**        | 所有题的通过数 / 总数                                          |
| **平均分**            | 裁判 0–1 连续分均值（整体 + 分型各一份）                       |
| **查询时长**          | 来自上传的 `latency_ms`：均值 / P50 / P95 / 最大              |
| **Agent token**       | 被测 Agent 自身合计 + 均值/题                                  |
| **框架成本**          | 框架自身出题 token、裁判 token（**与被测 Agent 分开统计**）   |
| **裁决分布**          | correct / partial / incorrect / missing 各类计数              |

**问题类型**（与 serving 意图分类对齐）：`factoid` 事实 / `conceptual` 概念 /
`procedural` 操作 / `constraint` 约束 / `troubleshooting` 故障 / `navigational` 导航。

### 检索层（IR）指标

证据条目按排名给 0–3 相关性等级（>0 视为相关）；下列指标在每个 K 上各报一份，并按
问题类型分桶：

| 指标                  | 定义                                                          |
| --------------------- | ------------------------------------------------------------- |
| **HitRate@K**         | top-K 内至少命中一个相关条目则计 1，否则 0（按用例求均值）     |
| **Recall@K**          | top-K 覆盖的黄金事实数 / 黄金事实总数                          |
| **MRR@K**             | 首个相关条目排名的倒数（>K 计 0）                              |
| **NDCG@K**            | `DCG@K / IDCG@K`，`DCG=Σ(2^grade−1)/log2(i+1)`，用 0–3 分级    |
| **ContextPrecision@K**| top-K 中相关条目数 / `min(K, 实际返回数)`                     |
| **ContextRecall**     | 任意排名下被覆盖的黄金事实数 / 黄金事实总数（不限 K）          |

---

## 11. 切换到真实 Claude

只改 eval-llm 的 `.env`，代码零改动：

**方式一：Anthropic 直连**

```ini
EVAL_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
EVAL_ANTHROPIC_MODEL=claude-sonnet-4-5
# 走中转网关时再加：ANTHROPIC_BASE_URL=https://your-gateway/...
```

**方式二：复用仓库内 llm_service**（需 llm_service 已在 8900 运行）

```ini
EVAL_LLM_PROVIDER=llm_service
EVAL_LLM_SERVICE_URL=http://localhost:8900
```

**方式三：本机 Claude Code CLI（`claude -p`）** —— 复用已安装并登录好的 `claude`
命令做出题与裁判，本仓库**无需配置任何 API Key**。前置：`claude` 已在 PATH 上
（Windows 会自动识别 `claude.cmd`）。

```ini
EVAL_LLM_PROVIDER=claude_cli
# EVAL_CLAUDE_MODEL=claude-sonnet-4-6   # 可选，空=用 claude 自身默认模型
# EVAL_CLAUDE_TIMEOUT=600               # 大文档出题可能较久，按需调大
```

实现说明：prompt 经 stdin 传入 `claude -p --output-format json`（避免命令行长度限制），
在临时空目录运行以隔离本项目的 `CLAUDE.md` 上下文；token 用量从 CLI 的 `usage`
字段（含缓存 token）解析。注意每次调用会带上 Claude Code 自身的 system prompt，
故 prompt token 计数会偏大、首次有缓存写入成本，属正常现象。

重启 eval-llm 即生效，eval-api 无需改动。token 用量自动计入报告的"框架成本"。

> 兼容：旧变量名 `EVAL_LLM_BACKEND` 仍被识别（作为 `EVAL_LLM_PROVIDER` 的回退）。

---

## 12. 测试

```bash
cd runtime_eval
py -3.10 -m pytest -q
```

- `tests/test_eval_llm.py`：eval-llm 的 `/health`、`/generate-cases`、`/judge`
  （mock provider + TestClient）。
- `tests/test_eval_api.py`：eval-api 全链路（项目→文档→出题→回答→评测→报告→
  SPA 托管），用注入的 in-process eval-llm 客户端，无网络。

---

## 13. 常见问题

**Q：报错 `Unable to evaluate type annotation 'str | None'`？**
A：用了 Python 3.9。改用 `py -3.10`（或任意 ≥3.10）。

**Q：`-m runtime_eval.eval_api` 报 `No module named 'runtime_eval'`？**
A：从 `runtime_eval/` 目录执行，并设置 `PYTHONPATH` 指向该目录：
```bash
PYTHONPATH=/abs/path/to/runtime_eval py -3.10 -m runtime_eval.eval_api
```

**Q：上传文档报 415？**
A：上传了暂不支持的二进制格式（.pdf/.docx/.pptx/.xlsx 等），请先转 markdown。
文本格式 `.md`/`.txt` 与 `.chm`（微软编译帮助文档）已支持；`.chm` 解析复用
knowledge_mining 的转换器，需 Windows `hh.exe` 或 PATH 上的 `7-Zip` 才能解压，
否则同样返回 415。

**Q：评测报"尚无任何已上传回答"？**
A：该 suite 还没有任何非空 answer。先在前端逐题粘贴回答并保存。

**Q：eval-api 调用 eval-llm 失败 / 连接被拒？**
A：确认 eval-llm 已启动且 `EVAL_LLM_URL` 指向其地址（默认 `http://localhost:8801`）。

**Q：控制台中文显示乱码？**
A：Windows 控制台代码页问题，不影响落盘文件——产物 JSON / 报告均为 UTF-8。

**Q：报告在哪？**
A：`workspace/<project_id>/reports/<run_id>.html` 和 `.md`；前端「打开完整报告」即
访问 `GET /api/v1/runs/{rid}/report`。
