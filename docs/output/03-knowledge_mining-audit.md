# knowledge_mining 审查报告

> 审查日期：2026-06-01
> 基线版本：v1.2+（参考 docs/2026-05-28-project-status-overview.md）
> 自 05-28 以来变更：15 次 commit

## 1. 目录结构

```
knowledge_mining/
├── demo_run.py                    # 演示脚本
├── docs/                         # 阶段文档（00-08）
├── mining/
│   ├── api/                      # FastAPI 服务层
│   │   ├── app.py                # FastAPI 应用主文件
│   │   ├── deps.py               # 依赖注入
│   │   └── routes/               # 路由模块
│   │       ├── builds.py         # 构建管理
│   │       ├── config.py         # 配置管理
│   │       ├── health.py         # 健康检查
│   │       ├── knowledge.py      # 知识查询（含分页）
│   │       ├── runs.py           # 运行管理（含分页）
│   │       └── uploads.py        # 文件上传
│   ├── contracts/                # 数据模型和协议
│   │   ├── models.py             # 核心数据模型（~400行）
│   │   ├── protocols.py          # 接口协议定义
│   │   └── rst_relations.py      # RST关系定义（15种关系）
│   ├── infra/                    # 基础设施层
│   │   ├── archive_extractor.py  # 压缩包提取
│   │   ├── db.py                 # 数据库配置
│   │   ├── docx_parser.py        # DOCX解析
│   │   ├── domain_pack.py        # 域配置包
│   │   ├── embedding.py          # 嵌入处理（仅调 llm_service）
│   │   ├── hash_utils.py         # 哈希工具
│   │   ├── llm_client.py         # LLM客户端
│   │   ├── llm_templates.py      # LLM模板系统
│   │   ├── mining_config.py      # 挖掘配置
│   │   ├── pdf_parser.py         # PDF解析
│   │   ├── pg_config.py          # PostgreSQL配置
│   │   ├── pg_schema.py          # PostgreSQL模式
│   │   ├── structure/            # 文档结构解析
│   │   ├── text_utils.py         # 文本工具
│   │   └── upload_config.py      # 上传配置
│   ├── ingestion/                # 数据摄取层
│   │   ├── pdf_preprocessing.py  # PDF预处理
│   │   ├── preprocessing.py      # 预处理（html/chm/等）
│   │   └── __init__.py
│   ├── jobs/                     # 任务管理
│   │   └── run.py                # 运行脚本
│   ├── pipeline.py               # 管道架构（StreamingPipeline）
│   ├── stages/                   # 处理阶段
│   │   ├── enrich/               # LLM增强
│   │   ├── eval.py               # 评估
│   │   ├── parse.py              # 解析（多格式）
│   │   ├── publishing.py         # 发布
│   │   ├── relations/            # RST关系提取（独立模块）
│   │   ├── retrieval_units/      # 检索单元构建
│   │   └── segment.py            # 分段（v1.1: heading独立）
│   └── __init__.py
├── tests/                        # 测试套件（12个）
└── domain_packs -> 不再使用（已迁移到 scenario_packs/）
```

## 2. 相比 05-28 基线的关键变更

### 2.1 Heading 独立为 Segment（重要变更）

**提交**: `9cd182b [claude]: emit headings as independent segments, exclude from merge`

**之前**：heading 文本只通过 `section_title` 传播到内容 segment，heading 本身不产生独立 segment。

**之后**：heading 作为独立 segment 输出（`block_type='heading'`），供关系构建阶段使用。heading 不参与合并，不参与 backward_merge。

**影响**：relation 构建可以利用 heading segment 建立层级关系，改善图结构。

### 2.2 RST 关系扩展到 15 种

**提交**: `3ab90cd [claude]: extract RST relations to standalone module, add 3 new types`

新增关系：
| 关系 | DB值 | 含义 |
|------|------|------|
| EXEMPLIFIES | exemplifies | 举例说明 |
| CONCEDES | concedes | 让步/承认 |
| PURPOSES | purposes | 目的/意图 |

关系定义移至独立模块 `contracts/rst_relations.py`，作为唯一真实来源。

### 2.3 配置集中化

**提交**: `d7b0ae3, b47d4cb`

- LLM 配置集中到 `llm_service` 作为单一真实来源
- semantic_roles / document_types 集中到 `domain.yaml`
- 移除旧的 `domain_packs/` 目录，统一使用 `scenario_packs/`

### 2.4 分页支持

**提交**: `67474a5`

- `runs.py` 和 `knowledge.py` 路由添加分页参数
- 前端文档列表支持分页加载

### 2.5 移除 ZhipuEmbeddingGenerator

**提交**: `c098cc7`

- mining 端不再直接调用智谱 embedding API
- 所有 embedding 统一通过 `llm_service` 调用

### 2.6 Question-Gen 模板简化

**提交**: `5bdf23b, 4146f0f`

- question-gen 和 discourse-relation 模板从 `json_array` 改为 `json_object`
- question-gen schema 从 array-of-objects 简化为 array-of-strings

### 2.7 代理绕过

**提交**: `30fbe7a, 5339b22`

- 所有 httpx 调用设置 `trust_env=False` + `proxy=None`
- 避免 localhost/internal 请求被代理

## 3. API 端点（完整清单，43个）

### 健康检查（2个）
- `GET /health`
- `GET /api/system/status`

### Run 管理（13个）
- `POST /api/runs` — 创建挖掘 Run
- `GET /api/runs` — 列出 Run（分页+过滤）
- `GET /api/runs/{id}` — Run 详情
- `GET /api/runs/{id}/stages` — Run 阶段时间线
- `GET /api/runs/{id}/documents` — Run 文档列表（**分页**）
- `GET /api/runs/{id}/progress` — Run 进度
- `GET /api/runs/{id}/documents/{docId}` — 文档详情
- `GET /api/runs/{id}/documents/{docId}/stages` — 文档阶段时间线
- `GET /api/runs/{id}/documents/{docId}/artifacts` — 文档产物
- `GET /api/runs/{id}/documents/{docId}/segments` — 文档 segments（**分页**）
- `GET /api/runs/{id}/documents/{docId}/units` — 文档 units（**分页**）
- `GET /api/runs/{id}/documents/{docId}/relations` — 文档 relations（**分页**）
- `POST /api/runs/{id}/cancel` — 取消 Run
- `POST /api/runs/{id}/publish` — 发布 Build 为 Release
- `GET /api/runs/{id}/artifacts` — Run 产物

### 知识资产（9个）
- `GET /api/knowledge/stats`
- `GET /api/knowledge/documents`
- `GET /api/knowledge/documents/{id}`
- `GET /api/knowledge/documents/{id}/segments`（**分页**）
- `GET /api/knowledge/documents/{id}/units`（**分页**）
- `GET /api/knowledge/documents/{id}/relations`（**分页**）
- `GET /api/knowledge/segments`
- `GET /api/knowledge/units`
- `GET /api/knowledge/relations`

### Build & Release（4个）
- `GET /api/builds`
- `GET /api/builds/{id}`
- `GET /api/releases`
- `GET /api/releases/active`

### 上传（3个）
- `GET /api/uploads/config`
- `POST /api/uploads`
- `GET /api/uploads`

### 配置（4个）
- `GET /api/config`
- `GET /api/config/domain-packs`
- `GET /api/config/domain-packs/{name}`
- `GET /api/config/stages`

## 4. Pipeline 阶段（8个核心阶段）

| 阶段 | 类/函数 | 输入→输出 | 状态 |
|------|---------|----------|------|
| Parse | `create_parser()` | RawFile → SectionNode | ✅ |
| Segment | `segment_document()` v1.1 | SectionNode → RawSegmentData[] | ✅ |
| Enrich | `LlmEnricher.enrich_batch()` | Segments → Enriched Segments | ✅ |
| Discourse | `DiscourseRelationBuilder.build()` | Segments → SegmentRelationData[] | ✅ |
| Retrieval Units | `build_retrieval_units()` | Segments → RetrievalUnitData[] | ✅ |
| Embedding | `embedding_stage()` | Units → Vectors | ✅ |
| DB Write | `db_write_stage()` | All → PostgreSQL | ✅ |
| Eval | `eval_stage()` | Units → Quality Scores | ✅ |

## 5. 已知问题与待完善项

### 高优先级
1. **并发 Run 限制** — 全局互斥锁，一次只能运行一个 Run
2. **大文档内存占用** — StreamingPipeline 中文档全量加载到内存
3. **缺少重试策略** — LLM 调用失败后无自动重试（依赖 llm_service 侧重试）

### 中优先级
4. **heading segment 的关系质量** — heading 独立后需评估 RST 关系提取是否有效利用
5. **question-gen 简化后的效果** — array-of-strings 是否比 array-of-objects 效果差
6. **缺少 Pipeline 进度持久化** — 进度信息仅存内存

### 低优先级
7. **缺少文档级血缘追踪** — document snapshot 链不完整
8. **缺少增量 Run 的智能断点续传** — 当前基于 hash 的 SKIP 判断较粗糙

## 6. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| 核心 Pipeline | **99%** | 8阶段完整，heading独立已实现 |
| 文件解析 | **98%** | 7种格式，PDF中文标题4种模式 |
| RST关系 | **95%** | 15种关系，独立模块 |
| 配置管理 | **95%** | 集中化完成，proxy绕过已修复 |
| API 端点 | **98%** | 43个端点，分页已添加 |
| 测试覆盖 | **75%** | 12个测试文件 |
