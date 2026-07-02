# telecom_eval 评估框架使用说明

`telecom_eval` 是面向电信知识库问答/检索系统的评估框架。它可以管理测试集、调用被测检索范式、计算检索与端到端回答指标、保存判分过程，并在前端展示报告和样本级调试信息。

适合用来回答这些问题：

- 检索系统有没有找到标准答案需要的证据？
- 返回证据的排序是否合理？
- 最终回答是否覆盖标准要点？
- 回答里的结论是否有证据支撑？
- 哪些样本拉低了某个指标？
- 某个样本为什么被判为命中、漏召回、引用错误或需要重点检查？

## 目录结构

```text
telecom_eval/
  api/          FastAPI 应用、路由和运行配置装配
  config/       runtime.json 和配置说明
  metrics/      检索、端到端、诊断类指标
  models/       Case、Trace、Metric、Artifact、Dataset 等数据模型
  services/     评估运行、测试集、报告、调试视图服务
  storage/      SQLite 存储与迁移
  subjects/     被测系统适配器，支持 local_mock / http
  judges/       大模型裁判、预算、缓存和调用记录
  ui/           Vue + Element Plus 前端
  demo.py       离线 mock 演示脚本
```

## 快速开始

### 1. 安装 Python 依赖

在仓库根目录执行：

```powershell
python -m pip install -e .[dev]
```

如果本机有多个 Python，建议使用 Python 3.11 或更高版本。

### 2. 配置运行参数

主要配置在：

```text
telecom_eval/config/runtime.json
```

常用字段：

| 字段 | 说明 |
| --- | --- |
| `api.port` | 评估后端端口，当前默认是 `8811` |
| `api.db_path` | SQLite 评估库路径 |
| `runner.max_concurrent_runs` | 同一后端进程最多同时运行的评估任务数，默认 `2` |
| `ui.dev_port` | 前端开发端口，当前默认是 `5174` |
| `ui.eval_api_base_url` | 前端代理到评估后端的地址 |
| `subject.provider` | 被测系统来源：`local_mock`、`http` 或 `fake` |
| `subject.search_base_url` | 真实检索服务 base URL |
| `judge.provider` | 判分 provider，默认 `mock` |

注意：启动前确认 `ui.eval_api_base_url` 和后端实际端口一致。例如后端使用 `8811` 时，前端代理应指向：

```json
"eval_api_base_url": "http://127.0.0.1:8811"
```

环境变量优先级高于 `runtime.json`。常用覆盖项：

```powershell
$env:TELECOM_EVAL_DB_PATH="data/evaluation/telecom_eval_demo.db"
$env:TELECOM_EVAL_API="http://127.0.0.1:8811"
$env:TELECOM_EVAL_SUBJECT_PROVIDER="local_mock"
$env:TELECOM_EVAL_MAX_CONCURRENT_RUNS="2"
```

### 3. 启动后端

```powershell
uvicorn telecom_eval.api.app:create_app --factory --host 127.0.0.1 --port 8811 --reload
```

健康检查：

```text
http://127.0.0.1:8811/api/v1/eval/health
```

### 4. 启动前端

```powershell
cd telecom_eval/ui
npm install
npm run dev
```

默认访问：

```text
http://127.0.0.1:5174
```

## 离线演示

如果只是验证框架流程，可以运行 mock demo：

```powershell
python -m telecom_eval.demo
```

它会：

1. 创建一个小型 mock 测试集；
2. 写入 SQLite；
3. 运行一次 mixed 评估；
4. 输出 run id、聚合指标和 Markdown 报告。

默认 SQLite 路径：

```text
data/evaluation/telecom_eval_demo.db
```

也可以指定：

```powershell
python -m telecom_eval.demo --db-path data/evaluation/my_eval.db
```

## 典型使用流程

### 1. 准备测试集

打开前端后进入“测试集”页面。

你可以：

- 点击“新建测试集”创建空测试集；
- 点击“下载模板”下载 CSV 模板；
- 进入测试集详情页后导入样本；
- 在样本编辑页补充标准答案、关键要点和标准证据。

推荐测试集字段：

| 字段 | 说明 |
| --- | --- |
| `case_id` | 样本 ID，可选；不填时导入器会按问题生成 |
| `question` | 测试问题，必填 |
| `expected_answer` | 标准答案 |
| `expected_key_points` | 标准答案关键要点，CSV 中可用分号分隔 |
| `expected_evidence_contains` | 标准证据应包含的短语 |
| `expected_evidence` | 标准证据内容或结构化 JSON |
| `answerability` | `answerable`、`unanswerable`、`should_refuse` |
| `task_type` | 任务类型，例如 `retrieval_or_e2e` |
| `risk_level` | `low`、`medium`、`high`、`critical` |
| `tags` | 标签，CSV 中可用分号分隔 |

一个样本要成为可确认样本，通常需要同时具备：

- 问题；
- 标准答案；
- 标准证据或标准证据短语。

### 2. 创建评估运行

进入“新建评估”页面：

1. 选择测试集；
2. 选择检索范式；
3. 选择评估类型：
   - `检索`：只评估检索证据；
   - `端到端`：评估最终回答；
   - `混合`：同时评估检索和回答；
4. 设置返回证据条数，默认 `10`；
5. 按需开启“大模型判分”；
6. 点击“创建并运行”。

任务创建后会立即跳转到报告页。评估任务会先进入 `queued`，后台 worker 按 `runner.max_concurrent_runs` 并发执行；运行中页面会自动刷新，完成后展示完整指标。

### 3. 查看报告

报告页路径：

```text
/runs/<run_id>
```

报告页包含：

- 运行概览；
- 质量指标解读；
- 技术指标明细；
- 指标评分样本明细；
- 失败样本；
- 大模型判分用量。

在“技术指标明细”中点击“查看详情”，可以看到该指标下每个样本的分数。低分样本会排在前面。每行都可以继续点击“查看判分过程/详情”进入样本详情。

### 4. 查看样本判分过程

样本详情页路径：

```text
/runs/<run_id>/cases/<case_id>
```

这里可以看到：

- 问题与标准答案；
- 标准证据；
- 检索证据包；
- 检索内容判定过程；
- 调用链时间线；
- 样本指标；
- 评估产物；
- 失败归因；
- 大模型判分调用；
- 原始 JSON。

“检索内容判定过程”会展示每个标准证据点如何和检索结果做内容语义判断。当前主检索指标不再使用 ID 作为严格命中条件，而是基于证据内容、关键短语、文本相似度以及必要时的大模型裁判判断。

## 指标说明

### 检索主指标

| 指标 | 说明 |
| --- | --- |
| `retrieval.hit_at_k` | 前 K 条证据中是否至少有一条支持标准证据点 |
| `retrieval.recall_at_k` | 标准证据点被检索结果支持的比例 |
| `retrieval.mrr` | 首条正确证据的排序质量 |
| `retrieval.evidence_coverage` | 检索证据对回答所需材料的覆盖程度 |

这些指标按内容语义判断，不把 `evidence_id`、`segment_id` 当作严格命中依据。

### 检索诊断指标

| 指标 | 说明 |
| --- | --- |
| `retrieval.gold_evidence_similarity_at_k` | 检索结果与标准证据的最高文本相似度 |
| `retrieval.gold_phrase_coverage_at_k` | 标准关键短语被检索结果覆盖的比例 |
| `retrieval.segment_resolve_rate` | 检索结果里的片段引用能解析回文本的比例 |
| `retrieval.latency` | 检索耗时 |

诊断指标用于解释问题，不一定直接代表最终质量结论。

### 端到端指标

| 指标 | 说明 |
| --- | --- |
| `e2e.key_point_coverage` | 最终回答覆盖标准要点的比例 |
| `e2e.faithfulness` | 回答陈述是否有检索证据支撑 |
| `e2e.citation_accuracy` | 引用是否准确指向支持证据 |
| `e2e.refusal_accuracy` | 对不可答或应拒答问题的处理是否正确 |
| `e2e.answer_correctness` | 大模型裁判综合标准答案、关键要点和证据后，对最终回答正确性的判断 |

## 大模型判分

框架支持 mock judge 和真实 judge。

默认配置：

```json
"judge": {
  "provider": "mock"
}
```

如果需要真实语义判分，可切换到 `claude_cli`：

```json
"judge": {
  "provider": "claude_cli",
  "claude_cli": {
    "bin": "claude",
    "model": "",
    "timeout": 600
  }
}
```

或者使用环境变量：

```powershell
$env:TELECOM_EVAL_JUDGE_PROVIDER="claude_cli"
$env:TELECOM_EVAL_CLAUDE_BIN="claude"
```

注意：

- 前端创建评估时也要打开“允许大模型判分”；
- 前端只需要设置“大模型失败重试次数”，0 表示失败后不重试，1 表示失败后再试一次；
- 总调用次数和总 token 默认不设上限；
- 判分调用会记录在 SQLite 中，可在样本详情页查看。

## 被测检索系统接入

`subject.provider` 决定检索从哪里来：

| provider | 用途 |
| --- | --- |
| `local_mock` | 从本地 Markdown 语料模拟检索，适合离线调试 |
| `http` | 调真实检索范式服务 |
| `fake` | 使用内置假适配器，适合单元测试 |

真实 HTTP 检索配置示例：

```json
"subject": {
  "provider": "http",
  "search_base_url": "http://10.205.71.26:8081",
  "search_domain": "cloud_core_network",
  "search_timeout": 30,
  "trust_env": false
}
```

前端会从范式服务获取已发布范式列表，用户选择范式后，后端用该范式的 `url` 作为检索路径。

## 数据存储

框架使用 SQLite 保存评估数据。路径由 `api.db_path` 或 `TELECOM_EVAL_DB_PATH` 决定。

主要保存内容：

- 测试集和样本；
- 数据集导入记录和快照；
- 评估运行；
- 检索/回答 trace；
- 指标结果；
- 判分 artifact；
- 诊断结果；
- 报告；
- 大模型判分调用记录。

前端的删除按钮是软删除：

- 删除评估运行时，`eval_runs.status` 会变成 `deleted`；
- 删除测试集时，`eval_datasets.status` 会变成 `deleted`；
- SQLite 原始记录仍保留，默认列表接口会隐藏这些记录。

## 常用 API

所有 API 默认挂在：

```text
/api/v1/eval
```

常用接口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` | `/datasets` | 测试集列表 |
| `POST` | `/datasets` | 创建测试集 |
| `GET` | `/datasets/{dataset_id}` | 测试集详情 |
| `POST` | `/datasets/{dataset_id}/imports:preview` | 导入预览 |
| `POST` | `/datasets/{dataset_id}/imports` | 提交导入 |
| `POST` | `/runs` | 创建评估任务并入队运行 |
| `GET` | `/runs` | 运行列表 |
| `GET` | `/runs/{run_id}/report` | 运行报告 |
| `GET` | `/runs/{run_id}/cases` | 运行样本分数明细 |
| `GET` | `/debug/runs/{run_id}/cases/{case_id}` | 样本调试详情 |

## 开发与验证

后端快速编译检查：

```powershell
python -m compileall telecom_eval
```

前端构建：

```powershell
cd telecom_eval/ui
npm run build
```

运行 demo 测试：

```powershell
python -m telecom_eval.demo
```

如果本地 `pytest` 使用了低版本 Python，可能会因为 `enum.StrEnum` 等依赖失败。建议使用 Python 3.11+ 的环境运行测试。

## 常见问题

### 前端请求后端失败

检查：

1. 后端是否启动；
2. `runtime.json` 中 `ui.eval_api_base_url` 是否指向后端实际端口；
3. 是否设置了 `TELECOM_EVAL_API` 覆盖了配置；
4. 前端 dev server 是否重启。

### 报告里没有样本级指标明细

通常是后端服务还没重启到最新代码，或者当前 run 没有保存 case 级 metric。报告页会尝试从 `/runs/{run_id}/cases` 兜底拼出明细。若仍为空，检查该 run 的 `eval_metrics` 表是否有 `case_id` 不为空的记录。

### 样本详情里没有“检索内容判定过程”

旧 run 不会自动补写新的 `retrieval_content_judgment` artifact。重新运行评估后，新的 run 会保存判定过程。

### 指标全是 0

优先进入报告页指标详情，查看是哪些样本拉低了指标。再进入样本详情，检查：

- 标准答案和标准证据是否完整；
- 检索证据包是否有内容；
- 检索结果是否覆盖标准关键短语；
- `retrieval_content_judgment` 的支持判断原因。

### 删除后 SQLite 里为什么还有记录

这是设计行为。删除按钮用于清空前端显示，采用软删除，不物理删除历史记录。需要审计或恢复时仍可以从 SQLite 中查看原始数据。

## 推荐新手路径

1. 运行 `python -m telecom_eval.demo`，确认框架能在本地跑通；
2. 启动后端和前端；
3. 在“测试集”页下载模板并创建一个小测试集；
4. 导入 3 到 5 条样本；
5. 创建一次 mixed 评估；
6. 在报告页查看指标；
7. 点“查看详情”定位低分样本；
8. 进入样本详情页查看证据包和判分过程。
