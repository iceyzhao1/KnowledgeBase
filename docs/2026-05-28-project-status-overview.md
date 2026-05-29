# CoreMasterKB 项目进展总览

> 日期：2026-05-28
> 基线版本：v1.1+
> 部署方式：Docker 单容器 All-in-One（supervisord 管理 6 个服务）

## 一、系统架构

```
Agent / MCP Client
  → MCP Server (port 9000) / 直接调用
  → Agent Serving Java (port 8081)
    → asset_core (PostgreSQL + pgvector)
  ← Knowledge Mining (port 8901) 写入
  ← LLM Service (port 8900) 提供 LLM 能力
  ← Main Control Service (port 8910) 配置管理
  ← kb-ui (nginx port 80) 前端界面
```

### 三库分离

| 数据库 | 职责 | 读写方 |
|--------|------|--------|
| `asset_core` | 正式知识资产（segments / units / embeddings / builds / releases） | Mining 写，Serving 只读 |
| `mining_runtime` | 挖掘运行态（runs / documents / stage_events） | Mining 高频写 |
| `agent_llm_runtime` | LLM 运行态（tasks / requests / attempts / results / templates） | LLM Service 主写 |

### 数据主链

```
source_batch → document → snapshot → snapshot_link
  → raw_segments / segment_relations / retrieval_units / retrieval_embeddings
  → build → release → serving
```

---

## 二、各服务进展

### 2.1 knowledge_mining（挖掘服务）

**状态：核心 Pipeline 已完成，功能完整**

**技术栈**：Python 3.11+ / FastAPI 3.0 / PostgreSQL（psycopg + async pool）

**API 端点**：

| 路由前缀 | 功能 | 状态 |
|----------|------|------|
| `GET /health` | 健康检查 | ✅ |
| `POST /api/runs` | 创建挖掘 Run（异步） | ✅ |
| `GET /api/runs` | 列出所有 Run | ✅ |
| `GET /api/runs/{id}` | Run 详情 | ✅ |
| `GET /api/runs/{id}/stages` | Run 阶段时间线 | ✅ |
| `GET /api/runs/{id}/documents` | Run 内文档列表（分页+过滤） | ✅ |
| `GET /api/runs/{id}/progress` | Run 进度聚合 | ✅ |
| `GET /api/runs/{id}/artifacts` | Run 产物聚合 | ✅ |
| `POST /api/runs/{id}/cancel` | 取消 Run | ✅ |
| `POST /api/runs/{id}/publish` | 发布 Build 为 Release | ✅ |
| `GET /api/runs/{id}/documents/{docId}/...` | 单文档详情/阶段/产物/segments/units/relations | ✅ |
| `GET /api/knowledge/stats` | 知识库全局统计 | ✅ |
| `GET /api/knowledge/documents` | 文档列表 | ✅ |
| `GET /api/knowledge/documents/{id}` | 文档详情（含 snapshot 历史） | ✅ |
| `GET /api/knowledge/documents/{id}/segments` | 文档 segments | ✅ |
| `GET /api/knowledge/documents/{id}/units` | 文档 retrieval units | ✅ |
| `GET /api/knowledge/documents/{id}/relations` | 文档 segment relations | ✅ |
| `GET /api/knowledge/segments` | 全局 segments 列表 | ✅ |
| `GET /api/knowledge/units` | 全局 retrieval units 列表 | ✅ |
| `GET /api/knowledge/relations` | 全局 relations 列表 | ✅ |
| `POST /api/uploads` | 文件上传 | ✅ |
| `GET/POST /api/config/...` | 配置相关 | ✅ |
| `GET/POST /api/builds/...` | Build 管理 | ✅ |

**文件解析能力**：

| 格式 | 解析器 | 状态 |
|------|--------|------|
| Markdown | `MarkdownParser`（markdown-it-py 结构化解析） | ✅ |
| TXT | `PlainTextParser`（段落分块 + 滑动窗口） | ✅ |
| PDF | `PdfParser`（pdfminer.six layout API） | ✅ |
| 其他 | `PassthroughParser`（跳过） | ✅ |
| DOCX | 未实现 | ❌ |
| HTML | 未实现 | ❌ |

**并发控制**：全局互斥锁防止并发 Run

---

### 2.2 agent_serving_java（检索服务）

**状态：核心检索 Pipeline 已完成，功能完整**

**技术栈**：Java 21+ / Spring Boot 3 / MyBatis-Plus / PostgreSQL + pgvector

**API 端点**：

| 路由 | 功能 | 状态 |
|------|------|------|
| `POST /api/v1/search` | 主检索端点（返回 ContextPack） | ✅ |
| `GET /health` | 健康检查 | ✅ |

**检索 Pipeline 11 阶段**：

1. **Resolve Domain** — 解析 effectiveDomain，设置 `LlmClient.knowledgeDomain`（确保后续所有 LLM 调用携带正确域），验证 DB 可达，设置 DomainContext
2. **Load Domain Profile** — 加载领域配置（scenario_pack）
3. **Query Understanding**（并行 Embedding）— LLM 理解查询意图、提取实体、关键词
4. **Retrieval Router** — 根据领域配置路由到不同检索通道
5. **Resolve Scope** — 解析 active release → build → snapshot IDs
6. **Collect Query Embedding** — 获取查询向量（与步骤 3 并行发起）
7. **Retrieve**（多路并行）— 从所有启用的检索通道召回候选
8. **Fuse** — 融合多路结果（RRF / Weighted RRF / Identity）
9. **Rerank** — 级联重排（Model Reranker → LLM Reranker → Score Reranker）
10. **Assemble ContextPack** — 组装最终结果包（items + relations + sources + evidence_groups）
11. **Build Debug Info** — 可选调试信息

**检索通道**：

| 通道 | 实现类 | 算法 | 状态 |
|------|--------|------|------|
| `fts`（全文检索） | `FtsRetriever` | PostgreSQL tsquery 全文搜索 | ✅ |
| `dense_vector`（向量检索） | `DenseVectorRetriever` | pgvector 余弦相似度 | ✅ |
| `entity_exact`（实体精确匹配） | `EntityExactRetriever` | 精确实体名匹配 | ✅ |
| `graph_expand`（图扩展） | `GraphExpander` | segment relation 图遍历 | ✅ |

**Rerank Pipeline**（级联）：

| Reranker | 实现 | 状态 |
|----------|------|------|
| Model Reranker | `ZhipuModelReranker`（智谱 rerank API） | ✅ |
| LLM Service Reranker | `LlmServiceReranker`（通过 llm_service 调用） | ✅ |
| LLM Direct Reranker | `LlmReranker`（直接调用 LLM） | ✅ |
| Score Reranker | `ScoreReranker`（基于 score chain 加权） | ✅ |

**多域支持**：`DomainPoolManager` + `DomainRoutingDataSource` 支持按域路由到不同数据库

**可观测性**：`TraceCollector` 记录每阶段耗时，`QueryLogAspect` 记录查询日志

---

### 2.3 llm_service（大模型服务）

**状态：核心功能已完成，支持多种 LLM Provider**

**技术栈**：Python 3.11+ / FastAPI / PostgreSQL（async psycopg）

**API 端点**：

| 路由 | 功能 | 状态 |
|------|------|------|
| `POST /api/v1/tasks` | 提交 LLM 任务（异步） | ✅ |
| `POST /api/v1/tasks/embed` | 提交 Embedding 任务 | ✅ |
| `POST /api/v1/tasks/rerank` | 提交 Rerank 任务 | ✅ |
| `POST /api/v1/execute` | 同步执行 LLM 任务（等待结果） | ✅ |
| `GET /api/v1/tasks/{id}` | 任务详情（含 request/result/attempts/events） | ✅ |
| `POST /api/v1/tasks/{id}/cancel` | 取消任务 | ✅ |
| `POST /api/v1/tasks/{id}/retry` | 重试失败任务 | ✅ |
| `POST /api/v1/tasks/batch-cancel` | 批量取消 | ✅ |
| `GET/POST /api/v1/templates/...` | Prompt 模板 CRUD | ✅ |
| `GET /api/v1/results/{id}` | 获取任务结果 | ✅ |
| `GET /api/v1/stats` | 服务统计 | ✅ |
| `GET /health` | 健康检查 | ✅ |
| `POST /api/v1/models/embed` | 直接 Embedding | ✅ |
| `POST /api/v1/models/rerank` | 直接 Rerank | ✅ |

**LLM Provider**：

| Provider | 实现 | 用途 | 状态 |
|----------|------|------|------|
| OpenAI Compatible | `OpenAICompatibleProvider` | 通用 LLM 调用（兼容 OpenAI API） | ✅ |
| BigModel (智谱) | `BigModelProvider` | Embedding + Rerank | ✅ |
| Mock | `MockProvider` | 测试用 | ✅ |

**核心功能**：

- **Worker 并发执行**：可配置并发数，默认 4
- **Lease Recovery**：自动回收超时 lease 的任务
- **幂等性**：支持 idempotency_key 去重
- **模板注册表**：`TemplateRegistry` 从数据库加载 prompt 模板，带缓存 TTL
- **事件总线**：`EventBus` 通知任务状态变化
- **解析器**：支持 json_object / json_array / text 输出类型

---

### 2.4 main_control_service（主控配置中心）

**状态：核心 CRUD 已完成**

**技术栈**：Python 3.11+ / FastAPI 2.0 / YAML 文件读写

**API 端点**：

| 路由 | 功能 | 状态 |
|------|------|------|
| `GET /health` | 健康检查 | ✅ |
| `GET /api/v1/system` | 列出系统配置文件 | ✅ |
| `GET /api/v1/system/{name}` | 获取系统配置（JSON） | ✅ |
| `GET /api/v1/system/{name}/raw` | 获取系统配置（YAML 原文） | ✅ |
| `PUT /api/v1/system/{name}/raw` | 更新系统配置（YAML 原子写） | ✅ |
| `GET /api/v1/domains` | 列出所有领域 | ✅ |
| `GET /api/v1/domains/{id}` | 获取领域详情 | ✅ |
| `GET /api/v1/domains/{id}/raw` | 获取领域配置（YAML 原文） | ✅ |
| `POST /api/v1/domains` | 创建领域（含默认 scenario_pack） | ✅ |
| `PUT /api/v1/domains/{id}/raw` | 更新领域配置（YAML 原子写） | ✅ |
| `DELETE /api/v1/domains/{id}` | 删除领域（含 scenario_pack） | ✅ |
| `GET /api/v1/domains/{id}/scenario` | 获取 scenario pack | ✅ |
| `GET /api/v1/domains/{id}/scenario/raw` | 获取 scenario pack（YAML 原文） | ✅ |
| `PUT /api/v1/domains/{id}/scenario/raw` | 更新 scenario pack | ✅ |

**配置文件结构**：

```
main_control_service/config/
├── system/
│   ├── database.yaml        # 数据库配置
│   └── llm_service.yaml     # LLM 服务配置
├── domain_registry.yaml     # 领域注册表
└── scenario_packs/
    └── cloud_core_network/
        └── domain.yaml      # 领域配置包
```

**关键特性**：
- 原子写入（tempfile + os.replace）
- YAML 校验（写入前 parse 验证）
- 自动创建默认 scenario_pack

---

### 2.5 mcp_server（MCP 服务）

**状态：基础功能已完成**

**技术栈**：Python / FastMCP

**工具**：

| 工具名 | 功能 | 状态 |
|--------|------|------|
| `search_knowledge` | 检索云核心网知识库，返回证据包 | ✅ |
| `health_check` | 检查知识库可用性（暂未对外暴露） | ⚠️ 内部可用 |

**参数**：query / domain / scope / entities / debug

**指令系统**：内嵌完整的推理护栏和回答行为指南（evidence_role 分类体系）

---

### 2.6 kb-ui（前端）

**状态：主要页面已完成，YAML 编辑器已集成 CodeMirror**

**技术栈**：Vue 3 + TypeScript + Pinia + Vue Router + TailwindCSS + ECharts

**页面路由**：

| 路由 | 页面 | 功能 | 状态 |
|------|------|------|------|
| `/` | DashboardView | 仪表盘（服务状态、统计卡片、图表） | ✅ |
| `/mining` | RunsView | 挖掘 Run 列表 | ✅ |
| `/mining/create` | CreateRunView | 创建挖掘 Run | ✅ |
| `/mining/:runId` | RunDetailView | Run 详情（进度、文档列表、stage 时间线） | ✅ |
| `/mining/:runId/documents/:docId` | RunDocumentDetailView | 单文档处理详情 | ✅ |
| `/search` | SearchView | 知识检索（调用 serving API） | ✅ |
| `/knowledge` | DocumentsView | 知识库文档列表 | ✅ |
| `/knowledge/:docId` | DocumentDetailView | 文档详情（segments / units / relations） | ✅ |
| `/graph` | GraphView | 知识图谱可视化 | ✅ |
| `/llm` | LlmView | LLM 任务列表 | ✅ |
| `/llm/:taskId` | LlmTaskDetailView | LLM 任务详情 | ✅ |
| `/settings` | SettingsView | 系统设置（SystemConfigTab + DomainManageTab + DomainDetailTab） | ✅ |

**组件**：

| 组件 | 功能 | 状态 |
|------|------|------|
| `YamlEditor.vue` | CodeMirror YAML 编辑器 | ✅ |
| `ServiceHealthCard.vue` | 服务健康卡片 | ✅ |
| `StatsCard.vue` | 统计卡片 | ✅ |
| `PipelineFlow.vue` | Pipeline 流程图 | ✅ |
| `EvidenceCard.vue` | 证据卡片 | ✅ |
| `PipelineTrace.vue` | Pipeline 追踪 | ✅ |
| `ForceGraph.vue` | 力导向图 | ✅ |
| `BarChart / LineChart / PieChart` | 图表组件 | ✅ |

**API 层**：`controlPlane.ts` / `mining.ts` / `serving.ts` / `llm.ts`，通过 nginx 代理

**状态管理**：Pinia stores（`controlPlane` / `mining` / `domain`）

---

### 2.7 数据库 Schema

**PostgreSQL（生产）+ SQLite（历史兼容）**

**asset_core 核心表**：
- `asset_documents` — 文档注册
- `asset_document_snapshots` — 文档快照
- `asset_document_snapshot_links` — 文档-快照关联
- `asset_raw_segments` — 原始片段
- `asset_raw_segment_relations` — 片段关系
- `asset_retrieval_units` — 检索单元
- `asset_retrieval_embeddings` — 向量嵌入
- `asset_builds` — 构建
- `asset_publish_releases` — 发布

**mining_runtime 核心表**：
- `mining_runs` — 挖掘运行
- `mining_run_documents` — 运行内文档状态
- `mining_run_stage_events` — 阶段事件

**agent_llm_runtime 核心表**：
- `agent_llm_tasks` — LLM 任务
- `agent_llm_requests` — 请求记录
- `agent_llm_attempts` — 尝试记录
- `agent_llm_results` — 结果
- `agent_llm_events` — 事件
- `agent_llm_prompt_templates` — Prompt 模板

---

### 2.8 Docker 部署

**单容器 All-in-One**：`docker/Dockerfile` 构建，supervisord 管理 6 个服务

**构建**：`deploy-build.sh` → `cmkb.tar`（约 203MB）

**部署**：`deploy-server.sh` + `docker-compose.yml`

**Nginx 代理**：
- `/api/mining/*` → port 8901
- `/api/serving/*` → port 8081
- `/api/llm/*` → port 8900
- `/api/control-plane/*` → port 8910
- 静态文件 → kb-ui dist

---

## 三、整体完成度评估

| 模块 | 完成度 | 备注 |
|------|--------|------|
| knowledge_mining | **95%** | Pipeline 全链路完成，缺少 DOCX/HTML 解析 |
| agent_serving_java | **90%** | 检索 Pipeline 完成，多域支持完成，缺少高级缓存 |
| llm_service | **90%** | 核心 CRUD + Worker 完成，缺少流式输出 |
| main_control_service | **85%** | YAML CRUD 完成，缺少服务联动控制 |
| mcp_server | **70%** | 基础工具完成，缺少多域切换和结果缓存 |
| kb-ui | **85%** | 主要页面完成，YAML 编辑器已集成 |
| 部署基础设施 | **90%** | Docker 全流程打通 |

## 四、待完善事项

1. **文件格式**：DOCX / HTML 解析器未实现
2. **MCP Server**：health_check 工具未对外暴露；缺少多域切换
3. **流式 LLM**：llm_service 不支持 SSE 流式输出
4. **高级缓存**：serving 层缺少结果缓存
5. **服务联动**：main_control_service 修改配置后无法自动通知其他服务 reload
6. **E2E 测试**：缺少跨服务的端到端集成测试
