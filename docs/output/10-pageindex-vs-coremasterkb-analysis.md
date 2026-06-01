# PageIndex vs CoreMasterKB：对比分析与增益借鉴

> 日期：2026-06-01
> 参考：[VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex)（30K+ stars）、OraclePageIndex、Neo4j PageIndex 变体
> 目标：识别 PageIndex 中可增益 CoreMasterKB 演进的思想和模式

## 0. 三个系统的核心定位

| 维度 | CoreMasterKB（当前） | PageIndex（VectifyAI） | SKE（演进目标参考） |
|------|---------------------|----------------------|-------------------|
| **定位** | 云核心网知识库平台 | 通用文档索引与推理检索 | 电信领域知识演化系统 |
| **检索范式** | Graph-RAG（4路召回 + RRF + Rerank） | Vectorless Reasoning-based RAG | 声明式查询引擎 + 图遍历 |
| **索引结构** | pgvector 向量 + FTS 全文 + JSONB 实体 | 层级树索引（JSON Tree） | Neo4j 双层图 + PostgreSQL 关系 |
| **是否需要向量** | 是（BGE-M3 1024维） | 否（纯 LLM 推理导航树） | 是（多场景：对齐/去重/冲突检测） |
| **是否需要图数据库** | 否（PG 递归 CTE 模拟图遍历） | 否（JSON 树结构） | 是（Neo4j 原生图遍历） |
| **知识粒度** | 检索单元（文本片段） | 页面/章节（自然段落） | Fact（SPO 三元组） |
| **扩展性** | PostgreSQL 单机 | JSON 文件系统（文件级树层） | PG + Neo4j 双引擎 |

---

## 1. PageIndex 的核心创新

### 1.1 层级树索引（Hierarchical Tree Index）

PageIndex 的核心数据结构是一棵 **JSON 层级树**，类似"智能目录"：

```
Document
├── Chapter 1: Financial Stability (node_id=0006, pages 21-22)
│   ├── Section 1.1: Monitoring Vulnerabilities (node_id=0007, pages 22-28)
│   └── Section 1.2: International Cooperation (node_id=0008, pages 28-31)
├── Chapter 2: Monetary Policy (node_id=0009, pages 32-45)
│   ├── ...
```

每个节点包含：
- `title`：章节标题
- `node_id`：唯一标识
- `start_index` / `end_index`：页码范围
- `summary`：LLM 自动生成的摘要
- `nodes`：子节点（递归嵌套）

### 1.2 Vectorless 推理检索（Reasoning-based Retrieval）

PageIndex 不使用向量数据库，而是让 LLM **直接在树结构上推理**：

1. **输入**：问题 + 树结构（不含全文，只有标题和摘要）
2. **推理**：LLM 分析问题与树节点的语义关系，选出最相关的 `node_id` 列表
3. **提取**：只从选中的页码范围提取原文
4. **回答**：基于提取的精确内容生成答案

**关键 insight**：树索引本身就是一个极其紧凑的"语义压缩"，LLM 看到标题+摘要就能判断相关性，无需向量相似度计算。

### 1.3 Agentic 检索（三工具模式）

PageIndex 用 OpenAI Agents SDK 暴露三个工具：

| 工具 | 功能 | 等价物 |
|------|------|--------|
| `get_document()` | 获取文档元数据 | CoreMasterKB 的 `GET /documents/{id}` |
| `get_document_structure()` | 获取树结构（无全文） | **缺失** — CoreMasterKB 无此能力 |
| `get_page_content(pages)` | 获取指定页码的原文 | CoreMasterKB 的段/单元检索 |

Agent 自主决定调用顺序：先看结构 → 定位节点 → 提取内容。

---

## 2. 核心差异对比

### 2.1 索引策略：扁平 vs 层级 vs 图

| 维度 | CoreMasterKB | PageIndex | SKE |
|------|-------------|-----------|-----|
| **索引粒度** | 检索单元（~500 token 片段） | 文档章节（自然段落） | Fact 三元组 |
| **组织方式** | 扁平表 + 关系表 | 嵌套树 JSON | Neo4j 图 |
| **层级信息** | `section_path`（字符串，如 "1.2.3"） | 天然嵌套结构 | 五层本体 + 图遍历 |
| **导航方式** | 向量相似度 + FTS + BFS | LLM 推理选路径 | 图遍历 + 代数查询 |

**PageIndex 的树 vs CoreMasterKB 的 section_path**：

CoreMasterKB 的 `asset_raw_segments` 有 `section_path` 字段（如 `"1.2.3"`），但这是**扁平的字符串标签**，不是可导航的树结构。当前检索完全忽略了这个层级信息。

> **增益机会**：CoreMasterKB 可以将 `section_path` 提升为可导航的树索引，让检索过程"先定位章节，再提取段落"，减少无效召回。

### 2.2 检索策略：多路召回 vs 推理导航 vs 图遍历

| 检索方式 | CoreMasterKB | PageIndex | 适合场景 |
|---------|-------------|-----------|---------|
| **FTS 全文** | tsvector + pg_trgm + LIKE 三级降级 | 无 | 精确关键词匹配 |
| **Dense Vector** | pgvector cosine + HNSW | 无 | 语义模糊匹配 |
| **Entity Exact** | JSONB @> containment | 无 | 实体精确过滤 |
| **Graph Expand** | BFS maxDepth=2 | 无 | 段间关系扩展 |
| **Tree Navigation** | 无 | LLM 在树结构上推理选节点 | 层级文档理解 |
| **图遍历** | 无（递归 CTE 模拟） | 无 | 多跳语义推理 |

> **关键差异**：PageIndex 认为"向量检索是 vibe retrieval（玄学检索）"，完全摒弃了向量。CoreMasterKB 和 SKE 都使用向量，但用途不同——CoreMasterKB 只用于检索，SKE 还用于对齐、去重、冲突检测。

### 2.3 知识粒度：文本片段 vs 页面范围 vs 三元组

| 粒度 | 表达力 | 去重能力 | 推理能力 |
|------|--------|---------|---------|
| 检索单元（CoreMasterKB） | 低（一段文本） | 文档级（content_hash） | 无 |
| 页面范围（PageIndex） | 中（自然章节） | 无 | 树路径推理 |
| Fact 三元组（SKE） | 高（SPO + 置信度 + 生命周期） | 事实级（语义去重） | 依赖闭包、影响传播 |

---

## 3. PageIndex 中可增益 CoreMasterKB 的思想

### 3.1 高价值增益：树索引 + 推理检索

**核心思想**：将文档的层级结构显式建模为可导航的树，检索时先"定位章节"再"提取内容"。

**CoreMasterKB 现有基础**：
- `section_path` 字段已有层级信息（如 `"1.2.3"`）
- `asset_raw_segments` 的 `block_type` 区分了标题、正文、列表等
- `asset_raw_segment_relations` 的 RST 关系提供了段间语义连接

**增益方案**：

```
当前：  query → 4路召回 → fusion → rerank → 返回片段列表
增强：  query → 树结构导航（定位章节） → 章节范围内精确检索 → 返回上下文段落
```

具体实现思路：
1. **构建阶段**：从 `section_path` + `block_type` + RST 关系自动生成文档的层级树索引（类似 PageIndex 的 JSON Tree，但存在 PG 中）
2. **检索阶段**：增加一个 `TreeNavigator` 步骤——用 LLM 在树结构上推理，选出相关章节范围
3. **过滤阶段**：在 4路召回的结果上，用章节范围做二次过滤，提升精准度

**预期收益**：
- 减少无效召回（不需要从全库搜索，先缩小到章节范围）
- 提升可解释性（"答案来自第 X 章第 Y 节"，而非"来自某个相似片段"）
- 降低向量检索的噪声

### 3.2 高价值增益：Agentic 检索模式

**核心思想**：将检索暴露为 Agent 可调用的工具集，让 LLM 自主决定检索策略。

**PageIndex 的三工具模式** vs **CoreMasterKB 的固定管道**：

| 模式 | 灵活性 | Token 效率 |
|------|--------|-----------|
| PageIndex（3 工具） | Agent 自主组合 | 按需加载 |
| CoreMasterKB（固定管道） | 11阶段全部执行 | 全量召回再过滤 |

**增益方案**：
- `get_document_tree(domain_id)` — 返回文档的层级树结构
- `search_in_section(section_path, query)` — 在指定章节范围内检索
- `expand_relations(segment_id, depth)` — 沿 RST 关系图展开

这直接映射到 MCP Server 的工具暴露，使 MCP Client 能做推理式检索。

### 3.3 中等价值增益：推理式查询理解

**核心思想**：PageIndex 不做 query understanding pipeline，而是让 LLM 直接在树结构上推理。

**对比**：

```
CoreMasterKB 当前：
  query → QueryUnderstanding（意图分类、实体提取、query 改写）
       → RoutePlan（决定走哪些通道）
       → 4路召回 → fusion → rerank

PageIndex：
  query → LLM 直接看树结构 → 推理出相关节点 → 提取内容
```

**增益**：在 CoreMasterKB 的 `QueryUnderstanding` 阶段，引入"树结构上下文"——不是让 LLM 猜测查询意图，而是让它在文档的层级结构上"看到"可能的答案位置。

### 3.4 中等价值增益：Vectorless 作为降级方案

**核心思想**：当向量服务不可用时（模型加载失败、GPU 不可用），PageIndex 的 tree-only 模式是一种优雅的降级方案。

**CoreMasterKB 现有的降级**：
- FTS 降级：tsvector → pg_trgm → LIKE
- 但没有"向量不可用时"的整体降级方案

**增益方案**：
- 当 Dense Vector Retriever 不可用时，切换到"树导航 + FTS"模式
- 类似 PageIndex 的方式：先用树结构定位章节 → 再用 FTS 在章节范围内搜索
- 无需向量，只需 LLM 推理 + 文本搜索

### 3.5 低价值增益：Vision-based RAG

PageIndex 支持"零 OCR"的视觉 RAG——直接处理 PDF 页面图片。

**对 CoreMasterKB 的适用性**：低。云核心网文档主要是结构化文本（配置手册、协议规范），不是图表密集的金融报告。但如果有大量网络拓扑图、信令流程图，可以考虑。

---

## 4. PageIndex 不能解决的 CoreMasterKB 痛点

PageIndex 是**单文档、无向量、无本体**的轻量级方案，以下 CoreMasterKB 痛点它无法解决：

| 痛点 | PageIndex 能否解决 | 原因 |
|------|-------------------|------|
| 跨文档知识推理 | 否 | PageIndex 是单文档检索，File System 扩展也只是文件级路由 |
| 知识去重与合并 | 否 | 无 Fact 模型，无知识粒度概念 |
| 本体与术语归一化 | 否 | 无本体层，无别名管理 |
| 多跳图遍历 | 否 | 无图数据库，树只有层级遍历 |
| 知识生命周期管理 | 否 | 无置信度、无状态管理 |
| 跨源冲突检测 | 否 | 单文档视角，无跨文档概念 |
| 声明式查询组合 | 部分 | 树导航是一种声明式，但不如 SKE 的 5 代数原语灵活 |

**结论**：PageIndex 的树索引思想是"检索层优化"，而 SKE 的本体+图是"知识层重构"。两者是不同层面的创新。

---

## 5. PageIndex 生态中的有趣变体

### 5.1 OraclePageIndex — 图数据库增强版

[jasperan/OraclePageIndex](https://github.com/jasperan/OraclePageIndex) 将 PageIndex 的 JSON 树替换为 **Oracle SQL Property Graph（SQL/PGQ）**：

- **存储**：文档/章节/实体/关系全部存为 Oracle Property Graph
- **查询**：用标准 SQL `GRAPH_TABLE MATCH` + 递归路径表达式 `->+` 做多跳遍历
- **意图分类**：6 种查询意图（LOOKUP/RELATIONSHIP/EXPLORATION/COMPARISON/HIERARCHICAL/TEMPORAL），每种有不同的遍历策略
- **图增强**：自动发现孤立实体间的关系（gap-filling agent）

**对 CoreMasterKB 的启示**：
- 如果选用 PostgreSQL 原生图扩展（如 Apache AGE），可以用类似 SQL/PGQ 的方式做图查询
- 意图分类 → 不同遍历策略的模式可以直接借鉴

### 5.2 Neo4j PageIndex — 生产级图存储版

[vectorless_RAG](https://github.com/TejasS1233/vectorless_RAG) 将 PageIndex 的 JSON 树持久化到 **Neo4j**：

- `[:HAS_SECTION]` / `[:HAS_SUBSECTION]` 关系构建文档层级
- LLM 从根节点开始，逐层选择要遍历的子节点
- 支持跨文档的 `[:REFERENCES]` 关系

**对 CoreMasterKB 的启示**：
- 文档层级可以自然映射为图中的父子关系
- 当前 `section_path` 字段可以直接转换为图中的节点链

### 5.3 ReasonKG — PageIndex + 知识图谱 + 一致性检查

[ReasonKG](https://github.com/Chandana0127/ReasonKG-Knowledge-Graph-Grounded-RAG-with-Logical-Consistency-Checking) 将 PageIndex 与 Neo4j 知识图谱结合：

- PageIndex 做文档级检索
- Neo4j 知识图谱做实体关系验证
- **规则引擎**检测并自动纠正 LLM 输出中的数值幻觉

**对 CoreMasterKB 的启示**：
- "检索层（PageIndex 风格）+ 验证层（知识图谱风格）"的双层架构
- 配置参数（IP 地址、端口号、超时值等）可以用知识图谱做精确验证

---

## 6. 综合建议：可落地的增益路径

### Phase 1：树索引增强（1-2 周）

1. **构建 `asset_document_tree` 表**：从现有 `section_path` + `block_type` 自动生成文档层级树
2. **暴露 `get_document_tree` API**：返回 JSON 树结构
3. **在 SearchService 中增加 Tree Navigation 步骤**：可选的检索前置步骤

### Phase 2：Agentic 检索（1 周）

1. **MCP Server 暴露树导航工具**：`get_tree` + `search_in_section` + `expand_relations`
2. **MCP Client 可选择 Agentic 模式**：让 LLM 自主决定检索策略

### Phase 3：降级方案（0.5 周）

1. **Vectorless 降级**：当向量服务不可用时，切换到"树导航 + FTS"模式
2. **利用现有 FTS 三级降级**：tsvector → pg_trgm → LIKE 保持不变

### Phase 4：意图分类查询（1 周）

1. **借鉴 OraclePageIndex 的 6 种意图分类**
2. **不同意图走不同检索路径**：
   - LOOKUP → Entity Exact + FTS
   - RELATIONSHIP → Graph Expand
   - EXPLORATION → Dense Vector + 树导航
   - COMPARISON → 多路召回 + Rerank
   - HIERARCHICAL → 树导航
   - TEMPORAL → FTS + 时间过滤

---

## 7. 总结

| 维度 | PageIndex 带来的增益 | 与 SKE 演进的关系 |
|------|---------------------|-------------------|
| **树索引** | 高 — 可立即落地，提升检索精准度 | 补充 SKE 方案，不冲突 |
| **Vectorless 降级** | 高 — 增强系统鲁棒性 | SKE 也需要向量，降级方案互补 |
| **Agentic 检索** | 高 — MCP Server 天然支持 | 与 SKE 声明式查询可共存 |
| **意图分类** | 中 — 可优化查询路由 | SKE 的代数查询更灵活，但意图分类是好的起点 |
| **Vision RAG** | 低 — 领域特性不强 | 不适用 |
| **图数据库** | 不适用 — PageIndex 本身不用图 | 需要单独评估（见痛点 06 文档） |

**核心结论**：PageIndex 的"树索引 + 推理导航"思想可以在 **不引入新基础设施** 的前提下增强 CoreMasterKB 的检索层。它是对 SKE 演进方案的**补充**而非替代——树索引解决"单文档精准定位"，SKE 的本体+图解决"跨文档知识推理"。
