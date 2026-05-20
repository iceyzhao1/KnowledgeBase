# 挖掘流水线 & 前端 迭代优化记录

> 创建时间：2026-05-21
> 状态：进行中
> 约束：至少完成 3 次迭代，上限 6 次。无需用户批准，自主执行。每次迭代必须提交代码并更新本文档，形成闭包。
>
> **闭包规则**：每次迭代结束时，必须：(1) 代码已提交 git；(2) 本文档已更新本次迭代的完整记录；(3) 迭代状态标记为"已完成"。未完成这三个条件不得开始下一次迭代。
>
> **上下文恢复规则**：本文档是唯一的上下文来源。上下文压缩后，只需读取此文档即可恢复工作状态。所有准则、检查清单、文件索引、枚举值参考均在此文档中。每次迭代开始前必须重新读取此文档。

---

## 一、项目背景

### 系统架构

```
data/knowledge_base/  →  demo_run.py  →  PostgreSQL  →  挖掘 API (:8901)  →  kb-ui (Vue)
                                                    ↗
                         LLM 代理 (:8900)  ----------
```

### 服务清单

| 服务 | 端口 | 启动命令 | 停止 |
|------|------|---------|------|
| LLM 代理 | 8900 | `python -m llm_service` | 杀掉 8900 端口进程 |
| 挖掘 API | 8901 | `python -m knowledge_mining.mining.api` | 杀掉 8901 端口进程 |
| 前端 | 5173 | `cd kb-ui && npm run dev` | 杀掉 5173 端口进程 |

### 数据库连接

- PostgreSQL：121.89.90.178:5432/coremasterkb，用户 kb_user
- 两套 Schema：`mining_runtime`（运行时）+ `asset_core`（资产）

### 测试数据（`data/knowledge_base/`）

| 目录 | 文件 |
|------|------|
| 5G核心网基础 | 5G核心网架构概述.md |
| PDU会话管理 | PDU会话管理.md |
| QoS策略控制 | QoS策略与控制.md |
| SMF会话管理功能 | SMF会话管理功能.md |
| UPF用户面功能 | UPF用户面功能详解.md |
| 网络切片 | 网络切片技术.md |
| 业务感知功能描述 | 4个文件 + 2个子目录 |

### Pipeline 阶段（每个文档）

```
解析(parse) → 分段(segment) → 增强(enrich) → 语篇分析(discourse) → 检索单元构建(retrieval_units)
→ [DB写入] select_snapshot → commit_segments → build_relations → build_retrieval_units
→ [构建发布] assemble_build → validate_build → publish_release
```

前端合并展示为 7 个阶段：解析、分段、增强、语篇分析、检索单元、数据提交、构建&发布

### 数据库表清单

**运行时表（mining_runtime）**：
- `mining_runs` — 运行记录（id, status, total_documents, new/updated/failed/committed_count, started_at, finished_at）
- `mining_run_documents` — 文档处理结果（id, run_id, document_key, action[NEW/UPDATE/SKIP], status[pending/processing/committed/failed/skipped], document_id, document_snapshot_id, error_message）
- `mining_run_stage_events` — 阶段事件（id, run_id, run_document_id, stage, status[started/completed/failed/skipped], duration_ms, output_summary, error_message）

**资产表（asset_core）**：
- `asset_source_batches` — 来源批次
- `asset_documents` — 文档（document_key, document_name, document_type）
- `asset_document_snapshots` — 文档快照（normalized_content_hash, mime_type, title）
- `asset_document_snapshot_links` — 文档-快照关联
- `asset_raw_segments` — 原始分段（segment_index, block_type, semantic_role, section_title, raw_text, token_count）
- `asset_raw_segment_relations` — 段间关系（source_segment_id, target_segment_id, relation_type, weight, confidence, distance）
- `asset_retrieval_units` — 检索单元（unit_type[raw_text/contextual_text/summary/generated_question/entity_card], title, text, weight, block_type, semantic_role）
- `asset_retrieval_embeddings` — 向量嵌入
- `asset_builds` — 构建记录
- `asset_build_document_snapshots` — 构建-快照关联
- `asset_publish_releases` — 发布记录

### 后端 API 端点清单

**健康与系统**：
- `GET /health` — 服务健康检查
- `GET /api/system/status` — 系统状态（DB连接池、最近运行）
- `GET /api/config` — 当前配置
- `GET /api/config/domain-packs` — 域包列表
- `GET /api/config/stages` — Pipeline 阶段列表

**挖掘运行**：
- `POST /api/runs` — 提交挖掘任务
- `GET /api/runs` — 运行列表（status/domain 筛选）
- `GET /api/runs/{runId}` — 运行详情
- `GET /api/runs/{runId}/stages` — 运行级阶段时间线
- `GET /api/runs/{runId}/documents` — 文档处理结果
- `GET /api/runs/{runId}/progress` — 运行进度
- `GET /api/runs/{runId}/documents/{docId}` — 单文档详情
- `GET /api/runs/{runId}/documents/{docId}/stages` — 文档级阶段时间线
- `GET /api/runs/{runId}/documents/{docId}/artifacts` — 文档产物计数
- `GET /api/runs/{runId}/documents/{docId}/segments` — 文档分段
- `GET /api/runs/{runId}/documents/{docId}/units` — 文档检索单元
- `GET /api/runs/{runId}/documents/{docId}/relations` — 文档关系（含 source_text/target_text）
- `GET /api/runs/{runId}/artifacts` — 运行级产物汇总
- `POST /api/runs/{runId}/cancel` — 取消运行
- `POST /api/runs/{runId}/publish` — 发布构建

**知识资产**：
- `GET /api/knowledge/stats` — 全局统计
- `GET /api/knowledge/documents` — 文档列表（type 筛选）
- `GET /api/knowledge/documents/{docId}` — 文档详情
- `GET /api/knowledge/documents/{docId}/segments` — 文档分段
- `GET /api/knowledge/documents/{docId}/units` — 文档检索单元
- `GET /api/knowledge/documents/{docId}/relations` — 文档关系（含 source_text/target_text）
- `GET /api/knowledge/segments` — 全局分段列表
- `GET /api/knowledge/units` — 全局检索单元列表
- `GET /api/knowledge/relations` — 全局关系列表（含 source_text/target_text，用于图谱）

**上传**：
- `POST /api/uploads` — 上传文件
- `GET /api/uploads` — 列出已上传批次

**构建与发布**：
- `GET /api/builds` — 构建列表
- `GET /api/builds/{buildId}` — 构建详情
- `GET /api/releases` — 发布列表
- `GET /api/releases/active` — 当前活跃发布

### 前端路由清单

| 路径 | 组件 | 功能 |
|------|------|------|
| `/mining` | RunsView.vue | 运行列表 |
| `/mining/create` | CreateRunView.vue | 创建新任务 |
| `/mining/:runId` | RunDetailView.vue | 运行详情（Pipeline流+文档表） |
| `/mining/:runId/documents/:docId` | RunDocumentDetailView.vue | 文档详情（时间线+产物标签页） |
| `/knowledge` | DocumentsView.vue | 知识资产文档列表 |
| `/knowledge/:docId` | DocumentDetailView.vue | 文档详情（分段/单元/关系三标签页） |
| `/graph` | GraphView.vue | 知识图谱（力导向图+关系表） |

### 枚举值参考

**block_type**：`paragraph`, `heading`, `table`, `list`, `code`, `blockquote`, `html_table`, `raw_html`, `unknown`

**semantic_role**：`concept`, `parameter`, `example`, `note`, `procedure_step`, `troubleshooting_step`, `constraint`, `alarm`, `checklist`, `unknown`

**relation_type**：`previous`, `next`, `same_section`, `same_parent_section`, `section_header_of`, `references`, `elaborates`, `condition`, `contrast`, `evidences`, `causes`, `results_in`, `backgrounds`, `conditions`, `summarizes`, `justifies`, `enables`, `contrasts_with`, `parallels`, `sequences`, `unrelated`, `other`

**unit_type**：`raw_text`, `contextual_text`, `summary`, `generated_question`, `entity_card`, `table_row`, `other`

---

## 二、迭代协议

每次迭代**必须**按顺序完成以下所有步骤，形成闭包。不需要用户批准。

### 步骤 1：环境重置
- 杀掉 8900、8901 端口上的现有进程
- 清空 PostgreSQL 中所有资产表 + 运行时表（TRUNCATE，包括 asset_retrieval_embeddings, asset_retrieval_units, asset_raw_segment_relations, asset_raw_segments, asset_build_document_snapshots, asset_publish_releases, asset_builds, asset_document_snapshot_links, asset_document_snapshots, asset_documents, asset_source_batches, mining_run_stage_events, mining_run_documents, mining_runs）
- 如果 Schema 有变更：调用 `recreate_all_tables()`
- 验证数据库已清空：`SELECT COUNT(*) FROM mining_runs` 应返回 0

### 步骤 2：启动服务
- 启动 LLM 代理（端口 8900）
- 启动挖掘 API（端口 8901）
- 验证：`curl http://localhost:8901/health` 返回正常

### 步骤 3：运行挖掘
- 执行 `python knowledge_mining/demo_run.py`（或修改 DATA_DIR 指定文件/目录）
- 等待完成
- 验证 run status = completed

### 步骤 4：验证

#### 4.1 Pipeline 正确性检查

对每个文档逐一执行以下 SQL 检查：

**a) 运行级检查**
```sql
-- 运行状态必须为 completed
SELECT id, status, total_documents, committed_count, failed_count
FROM mining_runs ORDER BY created_at DESC LIMIT 1;
```
- `status` = completed
- `committed_count` = `total_documents`（所有文档都成功）
- `failed_count` = 0

**b) 文档级检查**
```sql
-- 每个文档状态
SELECT id, document_key, action, status, error_message
FROM mining_run_documents
WHERE run_id = '{runId}'
ORDER BY document_key;
```
- 所有文档 `status` = committed
- 无 `error_message`

**c) 阶段事件完整性检查**
```sql
-- 每个文档的阶段事件
SELECT run_document_id, stage, status, duration_ms, output_summary
FROM mining_run_stage_events
WHERE run_id = '{runId}'
ORDER BY run_document_id, created_at;
```
- 每个文档必须有完整的阶段序列：parse(started→completed) → segment(started→completed) → enrich(started→completed) → discourse(started→completed) → retrieval_units(started→completed) → select_snapshot(started→completed) → commit_segments(started→completed) → build_relations(started→completed) → build_retrieval_units(started→completed)
- 没有 `failed` 状态的事件
- LLM 调用的阶段（enrich, discourse）耗时应 > 1秒
- 无零耗时的非瞬时阶段

**d) 资产表数据完整性检查**
```sql
-- 文档和快照
SELECT COUNT(*) FROM asset_documents;
SELECT COUNT(*) FROM asset_document_snapshots;
SELECT COUNT(*) FROM asset_document_snapshot_links;
-- 分段
SELECT document_snapshot_id, COUNT(*) FROM asset_raw_segments GROUP BY document_snapshot_id;
SELECT block_type, COUNT(*) FROM asset_raw_segments GROUP BY block_type;
SELECT semantic_role, COUNT(*) FROM asset_raw_segments GROUP BY semantic_role;
-- 关系
SELECT relation_type, COUNT(*) FROM asset_raw_segment_relations GROUP BY relation_type;
-- 检索单元
SELECT unit_type, COUNT(*) FROM asset_retrieval_units GROUP BY unit_type;
-- 构建
SELECT id, status FROM asset_builds;
SELECT id, status FROM asset_publish_releases;
```
- `asset_builds.status` = published
- `asset_publish_releases` 有 status = active 的记录
- `asset_raw_segments` 的 block_type 不全是 unknown
- `asset_raw_segments` 的 semantic_role 不全是 unknown
- `asset_retrieval_units` 覆盖多种 unit_type（至少 raw_text + summary + generated_question）
- `asset_raw_segment_relations` 的 relation_type 分布合理（不全是一种类型）

#### 4.2 前后端联调检查

逐个检查每个前端页面调用的 API 是否返回 200 且数据正确：

**a) 挖掘工作台页面**
- `/mining`（RunsView.vue）：调用 `GET /api/runs` → 列表有数据，状态显示为"已完成"
- `/mining/create`（CreateRunView.vue）：页面可渲染，表单元素存在
- `/mining/:runId`（RunDetailView.vue）：
  - `GET /api/runs/{runId}` → run_id、状态、文档数正确
  - `GET /api/runs/{runId}/stages` → 阶段事件列表非空
  - `GET /api/runs/{runId}/progress` → progress_percent 有值
  - `GET /api/runs/{runId}/documents` → 文档列表有数据，每行有文件名、状态、阶段
  - Pipeline 流组件显示 7 个阶段，全部"完成"状态
- `/mining/:runId/documents/:docId`（RunDocumentDetailView.vue）：
  - `GET /api/runs/{runId}/documents/{docId}` → 文档详情
  - `GET /api/runs/{runId}/documents/{docId}/stages` → 时间线有数据
  - `GET /api/runs/{runId}/documents/{docId}/artifacts` → 产物计数（segment_count, unit_count, relation_count）
  - `GET /api/runs/{runId}/documents/{docId}/segments` → 分段表有数据
  - `GET /api/runs/{runId}/documents/{docId}/units` → 检索单元表有数据
  - `GET /api/runs/{runId}/documents/{docId}/relations` → 关系表有数据，source_text/target_text 非空

**b) 知识资产页面**
- `/knowledge`（DocumentsView.vue）：调用 `GET /api/knowledge/documents` → 列表有数据
- `/knowledge/:docId`（DocumentDetailView.vue）：
  - `GET /api/knowledge/documents/{docId}` → 文档详情
  - `GET /api/knowledge/documents/{docId}/segments` → 分段标签页有数据
  - `GET /api/knowledge/documents/{docId}/units` → 检索单元标签页有数据
  - `GET /api/knowledge/documents/{docId}/relations` → 关系标签页有数据，显示段落文本而非 ID
- `/graph`（GraphView.vue）：
  - `GET /api/knowledge/relations` → 关系列表，source_text/target_text 非空
  - 图谱节点显示文本内容而非截断 ID
  - 关系表显示中文关系类型标签

**c) 构建与发布检查**
- `GET /api/builds` → 有 published 状态的构建
- `GET /api/releases/active` → 有活跃发布

#### 4.3 语义质量审查

**审查流程**：先阅读原始语料，理解内容结构，再对照挖掘产出评估质量。

**a) 原始语料预读（每次迭代必须执行）**
- 读取本次挖掘的原始 Markdown 文件
- 人工（Claude）梳理文档结构：大标题、小标题、主要概念、关键流程
- 识别文档中：核心定义（3-5个）、关键流程（2-3个）、重要参数/表格、逻辑关系（因果、对比、依赖）
- 这些作为"期望产出"的基准

**b) 分段质量审查**

读取 `asset_raw_segments` 中本次文档的全部分段，对照原文评估：
- **边界合理性**：是否在语义边界处切断？不应在句子中间、定义中间、流程中间切断
- **粒度合适性**：每个分段是否承载一个完整的语义单元？过大的分段（>500字）是否应拆分？过小的（<20字）是否应合并？
- **block_type 准确性**：`heading`/`paragraph`/`list`/`table`/`code` 是否与实际内容匹配？
- **semantic_role 准确性**：`concept`/`procedure_step`/`parameter`/`example`/`note` 是否准确反映内容性质？
- **section_title 覆盖率**：有多少分段缺少 section_title？

**c) 关系质量审查**

读取 `asset_raw_segment_relations` 中本次文档的全部关系，评估：
- **关系类型准确性**：`elaborates` 是否真的是详述？`contrast` 是否真的是对比？
- **置信度区分度**：高置信度（>0.8）的关系是否比低置信度的更有意义？
- **覆盖率**：文档中的核心概念之间的逻辑关系是否被捕获？
- **噪声比**：有多少关系是显而易见的结构关系（previous/next/same_section）vs 有意义的语义关系？

**d) 检索单元质量审查**

按 unit_type 分组读取，逐一评估：

- **raw_text**：是否与原始分段一致？是否完整？
- **contextual_text**：是否提供了有用的前后文？上下文窗口是否合适（不太短也不太长）？
- **summary**：
  - 是否准确概括了核心内容？
  - 是否遗漏了关键信息？
  - 是否包含原文没有的臆测？
  - 是否用中文撰写？
- **generated_question**：
  - 是否是自然、可回答的问题？
  - 问题的答案是否确实在对应的分段中？
  - 问题是否覆盖了分段的核心知识点？
  - 是否用中文撰写？
  - 问题多样性：是否有重复或高度相似的问题？
- **weight 分布**：权重是否合理区分了重要性？

**e) 横切质量评估**
- **概念覆盖度**：原文中的重要概念是否都有对应的检索单元？
- **检索有效性**：如果用 generated_question 去检索，能否命中正确的 raw_text？
- **信息密度**：检索单元之间是否有大量重复信息？
- **LLM 输出质量**：LLM 是否遵循了 prompt 指令？是否有幻觉？

### 步骤 5：规划优化方向
- 基于步骤 4.3 的发现，起草具体改进计划
- 优先级：分段准确性 > 语义角色分类 > 关系质量 > 检索单元质量
- 识别需要修改的文件：prompt 文件（`.j2` 模板）、Pipeline 配置、分段算法参数
- 写入当前迭代的子章节

### 步骤 6：实施
- 修改代码（挖掘流水线、prompt、前端）
- 如修改了前端：`cd kb-ui && npm run build` 确认编译通过
- 重新执行步骤 1-4 验证

### 步骤 7：审查
- 自审代码变更（检查所有改动文件）
- 调用 code-reviewer agent 审查
- 修复发现的问题

### 步骤 8：提交
- 以 `[claude]` 前缀提交代码
- 更新本文档的迭代记录
- 本次迭代形成闭包

---

## 三、关键文件索引

### 挖掘流水线代码
- `knowledge_mining/demo_run.py` — 挖掘运行入口
- `knowledge_mining/mining/jobs/run.py` — Pipeline 主逻辑（阶段编排）
- `knowledge_mining/mining/infra/mining_config.py` — 挖掘配置
- `knowledge_mining/mining/infra/pg_config.py` — 数据库配置
- `knowledge_mining/mining/api/` — API 服务

### Prompt 模板（影响挖掘质量的关键文件）
- `scenario_packs/cloud_core_network/domain.yaml` — 域包配置，包含所有 LLM 模板定义
  - `mining-question-gen`：生成检索问题
  - `mining-segment-understanding`：实体提取+语义角色分类+内容质量评估
  - `mining-discourse-relation`：语篇关系分析（RST）
  - `mining-contextual-retrieval`：上下文检索增强（当前 policy 为 off）
- `knowledge_mining/mining/infra/llm_templates.py` — 模板加载与动态注入
- `knowledge_mining/mining/infra/domain_pack.py` — 域包加载

### 检索策略（当前配置）
- `raw_text`: primary（主要）
- `generated_question`: auxiliary（辅助）
- `entity_card`: off
- `table_row`: off
- `contextual_retrieval`: off
- `max_questions_per_segment`: 2

### 实体类型（域包定义）
command, network_element, parameter, protocol, interface, alarm, feature, concept

### 关系类型（数据库 CHECK 约束）
previous, next, same_section, same_parent_section, section_header_of, references, elaborates, condition, contrast, evidences, causes, results_in, backgrounds, conditions, summarizes, justifies, enables, contrasts_with, parallels, sequences, unrelated, other

### 数据库 Schema
- `databases/asset_core/schemas/002_asset_core_postgresql.sql` — 资产表结构
- `databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql` — 运行时表结构

### 前端代码
- `kb-ui/src/api/mining.ts` — API 调用层
- `kb-ui/src/types/index.ts` — 类型定义
- `kb-ui/src/router/index.ts` — 路由定义
- `kb-ui/src/views/mining/` — 挖掘工作台页面
- `kb-ui/src/views/knowledge/` — 知识资产页面
- `kb-ui/src/components/mining/PipelineFlow.vue` — Pipeline 流组件

---

## 四、迭代历史

### 迭代 0 — 基线建立（待执行）

**目标**：用单文档运行当前流水线，记录基线质量

**状态**：待执行

（后续迭代将在此处追加）
