# 通用知识库测试框架 实现/修改开发方案

- 日期：2026-06-02
- 作者：Claude
- 关联任务：TASK-20260421-v11-agent-serving
- 关联架构：`docs/architecture/2026-06-02-runtime-eval-framework-architecture.md`（v2 三服务）
- 状态：v2 开发方案；按此对 `runtime_eval/` 单体代码做三服务化改造

---

## 修订说明

| 版本 | 日期       | 变更                                                                        |
| ---- | ---------- | --------------------------------------------------------------------------- |
| v1   | 2026-06-02 | 单体实现：CLI + 内嵌 FastAPI 前端 + 可插拔 LLM 后端，已离线冒烟通过。        |
| v2   | 2026-06-02 | **改为三服务**（eval-web / eval-api / eval-llm）通用框架开发方案，覆盖 v1。 |

---

## 1. 改造目标

把 v1 单体 `runtime_eval/` 改造为架构 v2 的三服务结构，保持现有出题/裁判/指标/报告
能力不丢，且每一步可运行、可测试：

- **eval-llm**：大模型服务，暴露 `/generate-cases`、`/judge` 领域接口，封装 Provider 与密钥。
- **eval-api**：后端服务，REST 编排（项目/文档/用例/回答/评测/报告），HTTP 调 eval-llm，持久化。
- **eval-web**：纯前端 SPA，仅调 eval-api。

---

## 2. 目标代码结构

包根仍为 `runtime_eval`（目录 `runtime_eval/runtime_eval/`），内部拆子包：

```
runtime_eval/runtime_eval/
  shared/                  跨服务纯领域模型与工具(无 web 依赖)
    models.py              QuestionType/TestCase/TestSuite/AgentResponse/ResponseSet/
                           CaseResult/EvalRun/TokenUsage/Verdict/SourceRef + Project/Document
    jsonutil.py            LLM 输出 JSON 容错解析
  eval_llm/                ③ 大模型服务
    config.py              Provider 选择 + 密钥(.env)
    prompts.py             操作员出题 / 裁判 prompt + JSON schema
    providers/             base / anthropic / llm_service / mock / factory
    service.py             领域逻辑：generate_cases() / judge_case()
    app.py                 FastAPI: POST /generate-cases /judge, GET /health
    __main__.py            python -m runtime_eval.eval_llm
  eval_api/                ② 后端服务
    config.py              DB/存储路径 + EVAL_LLM_URL
    parser.py              文档解析(md/txt 先行, 二进制留扩展点)
    store.py               持久化(文件版, 项目维度命名空间; 预留 DB)
    llm_client.py          HTTP 客户端 → eval-llm
    orchestrator.py        出题/评测编排
    metrics.py             指标聚合(迁自 v1)
    report.py              md + html 报告(迁自 v1)
    app.py                 FastAPI REST(项目/文档/用例/回答/评测/报告) + 挂载 eval-web 静态
    __main__.py            python -m runtime_eval.eval_api
  eval_web/                ① 前端
    static/index.html      SPA(适配 eval-api 端点)
    static/app.js
  tests/
    test_eval_llm.py       eval-llm /generate-cases /judge (mock provider, TestClient)
    test_eval_api.py       eval-api 全链路(in-process eval-llm client)
```

v1 旧扁平模块（`config.py/generator.py/judge.py/llm/*/web/*/store.py/corpus.py/cli.py`）
迁移后删除，避免双份。

---

## 3. 迁移映射（v1 → v2）

| v1 模块               | 去向                                                      |
| --------------------- | -------------------------------------------------------- |
| `models.py`           | `shared/models.py`（+ Project/Document 新模型）          |
| `jsonutil.py`         | `shared/jsonutil.py`                                      |
| `llm/*`               | `eval_llm/providers/*`                                    |
| `generator.py` prompt | `eval_llm/prompts.py` + `eval_llm/service.generate_cases`|
| `judge.py` prompt     | `eval_llm/prompts.py` + `eval_llm/service.judge_case`    |
| `corpus.py`           | `eval_api/parser.py`                                      |
| `store.py`            | `eval_api/store.py`（项目维度）                          |
| `metrics.py`          | `eval_api/metrics.py`（基本照搬）                        |
| `report.py`           | `eval_api/report.py`（基本照搬）                         |
| `web/server.py`+static| `eval_api/app.py` + `eval_web/static/*`                  |
| `generator/judge` 编排| `eval_api/orchestrator.py`（改为 HTTP 调 eval-llm）      |
| `cli.py`              | 暂移除；以两个 `__main__` 起服务，CLI 后续按需重建        |

---

## 4. 服务接口契约（落地版）

### eval-llm

```
GET  /health                      -> {status:"ok", provider:"..."}
POST /generate-cases
  body { document_text, doc_ref, types[], per_type, persona? }
  -> { cases:[{question,question_type,expected_answer,key_points,source,difficulty}],
       usage:{prompt,completion,total} }
POST /judge
  body { question, question_type, expected_answer, key_points, agent_answer }
  -> { verdict, score, rationale, covered_points, missed_points, usage:{...} }
```

### eval-api（前端消费）

```
POST /api/v1/projects                         { name } -> {project_id}
GET  /api/v1/projects
POST /api/v1/projects/{pid}/documents         multipart -> {document_id, sections}
GET  /api/v1/projects/{pid}/documents
POST /api/v1/projects/{pid}/suites:generate   { document_ids[], types[], per_type } -> suite
GET  /api/v1/suites/{sid}
POST /api/v1/suites/{sid}/responses           { agent_name, answers[] }
GET  /api/v1/suites/{sid}/responses
POST /api/v1/suites/{sid}/judge               { threshold? } -> { run_id, metrics }
GET  /api/v1/runs/{rid}/metrics
GET  /api/v1/runs/{rid}/report?format=html|md
GET  /                                         eval-web SPA
```

---

## 5. 持久化（分阶段）

- **本阶段**：文件存储，按项目命名空间：`workspace/<project_id>/{documents,suites,responses,runs,reports}/`。
  `store.py` 封装读写，接口面向"项目 + 实体 id"，便于后续替换为 DB。
- **后续**：SQLAlchemy + SQLite/PostgreSQL，模型不变，仅换 Store 实现。

---

## 6. token / 时长采集（对接架构 §8）

- **本阶段**：回答经前端人工上传，`latency_ms` / `token_usage` 由用户填（兜底路径）。
- **预留扩展点**：
  - `eval_api/adapters/target.py`（占位）：HTTP 调本地被测服务 + 掐表得时长；
  - `eval_api/adapters/cost.py`（占位）：按 `trace_id` 回查本地 `llm_service` 求和 token；
  - 需 `llm_service` 补"按 trace_id 聚合用量"端点（跨服务，单列任务）。

---

## 7. 实施阶段与验收

| 阶段 | 内容                                                         | 验收                                   | 本轮 |
| ---- | ------------------------------------------------------------ | -------------------------------------- | ---- |
| P1   | 抽 `shared/`；建 `eval_llm` 服务(含 providers)、prompts、app | `test_eval_llm` 通过(mock provider)    | ✅   |
| P2   | 建 `eval_api`：parser/store/llm_client/orchestrator/metrics/report/app | `test_eval_api` 全链路通过(mock)       | ✅   |
| P3   | `eval_web` SPA 适配 eval-api；eval-api 挂载静态              | `TestClient` GET / 返回页面            | ✅   |
| P4   | 删除 v1 旧扁平模块；更新 README/运行说明                     | `py -3.10 -m pytest` 全绿              | ✅   |
| P5   | 被测适配器 + 成本采集器 + llm_service 聚合端点               | 自动取答 + 自动采集                    | 计划 |
| P6   | DB 持久化 + 多租户 Project + 二进制文档解析                  | 迁移 Store 实现                        | 计划 |

> 本轮交付 P1–P4（三服务结构跑通、mock 全链路测试绿）。P5/P6 列为后续，单独排期。

---

## 8. 风险与回滚

- **风险**：重构面大，导入路径变更多。**缓解**：纯领域逻辑下沉 `shared/` 复用，
  providers/metrics/report 基本照搬；每阶段独立 TestClient 测试。
- **运行环境**：仍需 `py -3.10`（pydantic 运行期）。
- **回滚**：改动均未提交（暂存区），如方向不符可 `git checkout` 工作区单文件回退。

---

## 9. 不在本轮范围

- 真实 anthropic / 本地被测服务联网验证（仅 mock 闭环）。
- DB 持久化、二进制文档解析、被测自动取答与 token 自动回查（P5/P6）。
- 鉴权/多租户配额。
