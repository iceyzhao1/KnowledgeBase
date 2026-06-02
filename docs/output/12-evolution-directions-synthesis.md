# CoreMasterKB 演进方向：11 个工业级系统的思想借鉴与融合

> 日期：2026-06-02
> 目标：从 11 个工业级系统中提炼可落地的演进思想，综合给出 CoreMasterKB 的演进方向
> 面向：产品演进规划、与数据库实验室合作

---

## 0. 被调研系统一览

| # | 系统 | 核心定位 | 核心创新 | Star 数 |
|---|------|---------|---------|---------|
| 1 | **Microsoft GraphRAG** | 层级社区摘要 + 全局/局部搜索 | Leiden 社区检测 → 层级摘要 → map-reduce 全局搜索 | 33K |
| 2 | **Cognee** | 模块化 KG 构建管线 | Task/Pipeline 可组合架构、3 存储引擎（关系+向量+图） | 新兴 |
| 3 | **Graphiti / Zep** | 时序知识图谱 + agent 记忆 | 双时态模型（valid_at/invalid_at）、增量实体解析、3 层子图 | 10K+ |
| 4 | **LlamaIndex PGIndex** | Property Graph 抽象层 | Schema-free/schema-constrained 可切换、5 种子检索器可组合 | 40K (LLamaIndex) |
| 5 | **FalkorDB GraphRAG-SDK** | Redis 图数据库 + 策略化管线 | 6 个 ABC 策略可替换、9 步管线、4 路混合检索 | 新兴 |
| 6 | **WhyHow KG Studio** | 规则驱动 KG 构建 | 实体解析规则 + 实体提取规则 + 实体关系规则、人类在环 | 新兴 |
| 7 | **R2R (SciPhi)** | 全栈 RAG 平台 | 全 PostgreSQL 栈、30+ 格式解析、Leiden 聚类、Hatche DAG 编排 | 5K+ |
| 8 | **Mem0** | Agent 记忆层 | 3 信号融合检索（semantic + BM25 + entity boost）、8 阶段 pipeline | 25K+ |
| 9 | **PageIndex** | Vectorless 推理式 RAG | 层级 JSON 树索引、LLM 在树结构上推理导航 | 30K+ |
| 10 | **Pinecone Nexus** | 知识引擎 | Context Compiler 编译时推理、KnowQL 声明式查询 | 商业 |
| 11 | **SKE**（自有参考） | 本体驱动 + 图遍历 + 知识演化 | 五层本体、Fact 三元组、声明式查询引擎 5 代数原语 | 内部 |

---

## 1. 十大演进方向（按优先级排序）

### 方向 1：层级社区检测 + 摘要（来源：Microsoft GraphRAG + R2R）

**来源思想**：
- **Microsoft GraphRAG**：Leiden 算法递归聚类 → 多层级社区 → 每社区 LLM 生成摘要 → 全局搜索用 map-reduce 在社区摘要上推理
- **R2R**：graspologic Leiden 聚类 → 社区评分（1-10）+ 发现列表 → 社区摘要嵌入向量

**当前 CoreMasterKB 的差距**：
- `asset_raw_segment_relations` 有 15 种 RST 关系但无社区概念
- 无法回答"云核心网领域的主要主题有哪些"这类全局性问题
- Serving 的 GraphExpander 只做 2 跳 BFS，无法理解全局结构

**增益方案**：
```
Build 阶段新增：
  segment relations → 构建实体图 → Leiden 层级聚类 → 社区摘要 → 存储

Serving 阶段：
  Global Search：map-reduce 在社区摘要上回答全局性问题
  Local Search：实体 → 社区 → 社区成员 → 关联段 → 回答局部问题
```

**与数据库实验室的合作点**：
- Leiden 算法在 PostgreSQL 中的高效实现（当前用 graspologic/Python）
- 社区摘要的存储和索引策略

**预估工作量**：2-3 周

---

### 方向 2：时序知识图谱 + 事实生命周期（来源：Graphiti/Zep + SKE）

**来源思想**：
- **Graphiti**：每条边携带 4 个时间戳（valid_at / invalid_at / created_at / expired_at），双时态模型，新事实自动使旧事实失效
- **SKE**：Fact 的 lifecycle_state（active / conflicted / superseded / merged），五维置信度评分

**当前 CoreMasterKB 的差距**：
- 知识没有时效性概念，不知道某条配置参数何时有效、何时被废弃
- 无法处理"3GPP Release 17 和 Release 18 的差异"这类时序查询
- 无事实级生命周期管理

**增益方案**：
```
新增 Fact 表：
  facts(id, subject, predicate, object, confidence,
        valid_at, invalid_at,     -- 业务时间（何时为真）
        created_at, expired_at,   -- 系统时间（何时被录入/失效）
        lifecycle_state, source_segment_ids)

新增 Evidence 表：
  evidence(id, fact_id, segment_id, exact_span, extraction_method, score)
```

**与数据库实验室的合作点**：
- 双时态查询的 SQL 优化（valid_at/invalid_at 区间查询）
- 事实失效的事件通知机制

**预估工作量**：3-4 周

---

### 方向 3：策略化管线架构（来源：FalkorDB SDK + Cognee）

**来源思想**：
- **FalkorDB GraphRAG-SDK**：6 个 ABC 策略（Loader / Chunker / Extractor / Resolver / Retriever / Reranker），每个可独立替换，9 步管线
- **Cognee**：Task/Pipeline 可组合架构，每个 Task 封装一个 Python callable，支持批量、错误处理、日志

**当前 CoreMasterKB 的差距**：
- Serving 的 SearchService 是硬编码 11 阶段管道，不可替换
- Mining Pipeline 的各阶段虽可配置但不可独立替换
- 引入新检索策略（如社区搜索、时序查询）需要大量修改核心代码

**增益方案**：
```python
# 策略 ABC
class RetrievalStrategy(ABC):
    @abstractmethod
    async def retrieve(self, query, context) -> RetrievalResult: ...

# 可组合管线
class RetrievalPipeline:
    def __init__(self, strategies: list[RetrievalStrategy]):
        self.strategies = strategies

    async def execute(self, query, context):
        results = await gather(*[s.retrieve(query, context) for s in self.strategies])
        return fuse(results)
```

**预估工作量**：2-3 周

---

### 方向 4：多信号融合检索（来源：Mem0 + FalkorDB + R2R）

**来源思想**：
- **Mem0**：3 信号融合（semantic similarity + BM25 + entity boost），BM25 用 query-length-adaptive sigmoid 归一化
- **FalkorDB**：4 路检索（fulltext + vector + MENTIONED_IN 遍历 + 2-hop 邻居→chunk）
- **R2R**：HyDE（假设文档嵌入）+ RAG Fusion（多查询变体 + RRF 合并）+ semantic + fulltext + graph

**当前 CoreMasterKB 的差距**：
- 4 路召回（FTS + Dense + Entity + Graph Expand）已有，但 RRF 权重固定
- 无 HyDE、无 RAG Fusion、无 BM25 归一化
- 无"实体→关联段"的 MENTIONED_IN 遍历路径

**增益方案**：
- 在 `asset_raw_segments` 增加 `entity_mentions` 关联表，支持 MENTIONED_IN 遍历
- 引入 HyDE（对查询生成假设答案 → 用假设答案的 embedding 搜索）
- 引入 RAG Fusion（对查询生成 N 个变体 → 分别检索 → RRF 合并）

**预估工作量**：1-2 周

---

### 方向 5：Schema 约束的实体提取（来源：LlamaIndex PGIndex + WhyHow + FalkorDB）

**来源思想**：
- **LlamaIndex SchemaLLMPathExtractor**：定义 `validation_schema = {"PERSON": ["WORKS_AT", "HAS"], "ORG": ["WORKS_WITH"]}`，严格约束 LLM 只提取 schema 内的三元组
- **WhyHow**：3 类规则（Entity Resolution Rules / Entity Extraction Rules / Entity Relationship Rules），人类在环
- **FalkorDB**：GraphSchema 约束提取，prune 步骤过滤 off-schema 的实体/关系

**当前 CoreMasterKB 的差距**：
- `semantic_role`（11 种）和 `block_type`（9 种）是扁平标签，不是可约束的 schema
- LLM 提取实体时无 schema 约束，可能产生无关实体
- 无实体归一化规则

**增益方案**：
```yaml
# 云核心网领域 Schema
entity_types:
  NetworkFunction: [SMF, UPF, AMF, PCF, UDM, ...]
  Protocol: [BGP, OSPF, GTP, SIP, DIAMETER, ...]
  Vendor: [Huawei, Ericsson, Nokia, ZTE, ...]
  Parameter: [timeout, retry_count, mtu, ...]
  Fault: [connection_lost, handover_failure, ...]

relation_types:
  NetworkFunction -[IMPLEMENTS]-> Protocol
  NetworkFunction -[CONFIGURED_BY]-> Parameter
  Fault -[CAUSED_BY]-> Parameter
  Vendor -[MANUFACTURES]-> NetworkFunction
```

**预估工作量**：2-3 周

---

### 方向 6：编译时知识制品（来源：Pinecone Nexus + PageIndex）

**来源思想**：
- **Pinecone Nexus**：Context Compiler 自动编译源数据为任务优化的 Artifacts，"编译一次，查询多次"
- **PageIndex**：层级 JSON 树索引 = 极度紧凑的语义压缩，LLM 在树上推理导航

**当前 CoreMasterKB 的差距**：
- 每次 Serving 请求都执行完整 11 阶段管道
- 同一领域的问题重复执行相同检索逻辑
- 无预编译的知识制品

**增益方案**：
```
Build 阶段生成制品：
  - 领域概览制品：云核心网核心概念 + 关系图
  - 故障诊断制品：故障模式 → 原因 → 解决方案的预编译路径
  - 配置参考制品：参数 → 默认值 → 依赖 → 约束的结构化表
  - 对比分析制品：厂商/版本间的预编译差异表

查询时：
  MCP Client → 匹配制品 → 直接返回 → 未命中再走传统管道
```

**预估工作量**：2-3 周

---

### 方向 7：增量实体解析与去重（来源：Graphiti + FalkorDB + R2R）

**来源思想**：
- **Graphiti**：3 阶段实体解析（exact match → fuzzy Jaccard/MinHash similarity → LLM reasoning），增量更新
- **FalkorDB**：2 阶段去重（exact name match → embedding cosine similarity > threshold），后置全局去重
- **R2R**：可选 LLM 实体去重，比较实体描述判断是否同一

**当前 CoreMasterKB 的差距**：
- 仅有文档级和段落级去重（content_hash / normalized_hash）
- 无实体级去重——"UPF"和"用户面功能"是同一实体但系统不知道
- 无增量解析——每次 build 都是全量重建

**增益方案**：
```
Mining Pipeline 增加 Resolve 阶段：
  1. 精确匹配：normalized_name 归一化后精确比较
  2. 模糊匹配：别名表（lexicon_aliases）查询
  3. 语义匹配：embedding cosine > 0.92
  4. LLM 确认：对不确定的实体用 LLM 判断是否同一
```

**预估工作量**：2 周

---

### 方向 8：树索引 + 推理导航（来源：PageIndex + OraclePageIndex）

**来源思想**：
- **PageIndex**：JSON 层级树（标题+摘要+页码），LLM 在树结构上推理选节点，只提取选中内容
- **OraclePageIndex**：6 种查询意图（LOOKUP/RELATIONSHIP/EXPLORATION/COMPARISON/HIERARCHICAL/TEMPORAL），每种不同遍历策略

**当前 CoreMasterKB 的基础**：
- `section_path`（如 `"1.2.3"`）已有层级信息但未被利用
- 可直接构建树索引

**增益方案**：
```
构建阶段：section_path + block_type → 文档层级树（存 PG）
检索阶段：query → LLM 在树上推理选章节 → 章节范围内精确检索
降级模式：向量不可用时，树导航 + FTS 替代
```

**预估工作量**：1-2 周

---

### 方向 9：声明式查询语言（来源：Pinecone Nexus KnowQL + SKE + LlamaIndex）

**来源思想**：
- **Nexus KnowQL**：6 原语（intent / filter / provenance / shape / confidence / budget），agent 一次调用拿结构化答案
- **SKE**：5 代数原语（seed / expand / combine / aggregate / project），可自由组合查询
- **LlamaIndex TextToCypherRetriever**：自然语言 → Cypher 查询 → 图遍历

**当前 CoreMasterKB 的差距**：
- MCP `search_knowledge` 只接受自然语言 query
- 无法表达输出形状、置信度阈值、溯源要求、延迟预算

**增益方案**：
```json
{
  "ask": "SMF 会话管理配置参数",
  "shape": {"parameters": [{"name": "str", "default": "str", "range": "str"}]},
  "scope": {"network_elements": ["SMF"]},
  "ground": true,
  "budget": {"max_latency_ms": 500}
}
```

**预估工作量**：1-2 周

---

### 方向 10：Agent 记忆层（来源：Mem0 + Graphiti）

**来源思想**：
- **Mem0**：add() 8 阶段 pipeline（context gathering → existing retrieval → LLM extraction → hash dedup → persist → entity linking），3 信号融合检索
- **Graphiti**：3 层子图（Episode → Entity → Community），Saga 组织顺序对话，双时态边

**当前 CoreMasterKB 的差距**：
- 无 agent 级别的记忆——每次 MCP 查询都是无状态的
- 无法记住用户之前的查询偏好、已讨论的实体、未解决的问题

**增益方案**：
```
新增会话层：
  - Session 表：关联 agent_id / user_id
  - Episode 表：记录每次交互（query + result + feedback）
  - 从 Episode 中提取实体和关系，增量更新知识图谱
```

**预估工作量**：2-3 周

---

## 2. 演进路线图

```
Phase 0（立即可做，无需新基础设施）         预计 4-6 周
├── 方向 8：树索引 + 推理导航（1-2 周）
├── 方向 4：多信号融合检索增强（1-2 周）
└── 方向 3：策略化管线架构重构（2-3 周）

Phase 1（短期，在现有 PG 上增强）           预计 4-6 周
├── 方向 5：Schema 约束实体提取（2-3 周）
├── 方向 7：增量实体解析与去重（2 周）
└── 方向 9：声明式查询接口（1-2 周）

Phase 2（中期，引入图数据库 + 知识制品）     预计 8-12 周
├── 方向 1：层级社区检测 + 摘要（2-3 周）
├── 方向 6：编译时知识制品（2-3 周）
├── 方向 2：时序知识图谱 + 事实生命周期（3-4 周）
└── 方向 10：Agent 记忆层（2-3 周）
```

---

## 3. 各系统对 10 个方向的贡献矩阵

| 方向 | GraphRAG | Cognee | Graphiti | LlamaIndex | FalkorDB | WhyHow | R2R | Mem0 | PageIndex | Nexus | SKE |
|------|---------|--------|----------|-----------|----------|--------|------|------|-----------|-------|-----|
| 1.社区检测+摘要 | **核心** | | | | | | **辅助** | | | | |
| 2.时序KG+事实 | | | **核心** | | | | | | | | **辅助** |
| 3.策略化管线 | | **核心** | | | **核心** | | | | | | |
| 4.多信号融合 | | | | | **辅助** | | **辅助** | **核心** | | | |
| 5.Schema约束 | | | | **核心** | **辅助** | **核心** | | | | | |
| 6.编译时制品 | | | | | | | | | **辅助** | **核心** | |
| 7.实体解析去重 | | | **核心** | | **辅助** | | **辅助** | | | | |
| 8.树索引导航 | | | | | | | | | **核心** | | |
| 9.声明式查询 | | | | **辅助** | | | | | | **核心** | **核心** |
| 10.Agent记忆 | | | **核心** | | | | | **核心** | | | |

---

## 4. 与数据库实验室的合作议题（更新至 22 项）

### 高优先级（决定架构走向）

| # | 议题 | 来源 |
|---|------|------|
| 1 | 图数据库选型（Neo4j vs AGE vs FalkorDB vs NebulaGraph） | FalkorDB, Graphiti |
| 2 | 跨引擎事务一致性（PG + 图数据库双写） | Graphiti, Cognee |
| 3 | 本体 Schema 设计 → 图数据库节点/边映射 | LlamaIndex, WhyHow, SKE |
| 4 | 混合查询路由（PG 关系 + 图遍历统一接口） | FalkorDB, Nexus |
| 5 | Leiden 社区检测在 PG 中的高效实现 | GraphRAG, R2R |
| 6 | 双时态查询的 SQL/图查询优化 | Graphiti, SKE |

### 中优先级（核心功能实现）

| # | 议题 | 来源 |
|---|------|------|
| 7 | Fact 三元组抽取与存储的数据管道 | SKE, FalkorDB |
| 8 | 增量迁移策略 | FalkorDB incremental update |
| 9 | 实体解析（exact + fuzzy + embedding + LLM） | Graphiti, FalkorDB, Mem0 |
| 10 | 向量 + 图联合检索 | FalkorDB 4-path, Mem0 3-signal |
| 11 | 层级树索引在 PG 中的存储（ltree / 物化路径） | PageIndex |
| 12 | Schema 约束的实体提取引擎 | LlamaIndex, WhyHow |
| 13 | 编译时制品的存储与版本化 | Nexus |
| 14 | 多信号融合的归一化与权重调优 | Mem0 sigmoid, R2R RRF |
| 15 | 社区摘要的嵌入与检索 | GraphRAG, R2R |
| 16 | 声明式查询到 PG SQL + 图查询的编译 | Nexus KnowQL, SKE |

### 低优先级（锦上添花）

| # | 议题 | 来源 |
|---|------|------|
| 17 | 知识治理闭环（候选词 → 门控 → 审批 → 晋升） | SKE |
| 18 | 冲突检测与解决 | SKE, Graphiti |
| 19 | 置信度评分与时效性衰减 | SKE, Nexus |
| 20 | Agent 记忆层的存储与检索 | Mem0, Graphiti |
| 21 | 制品新鲜度检测与自动重编译 | Nexus |
| 22 | 意图分类 → 检索策略的查询优化器 | OraclePageIndex, FalkorDB |

---

## 5. 总结

**一句话**：从 11 个工业级系统中提炼出 10 个演进方向，分 3 个 Phase 渐进落地。

**核心洞察**：所有系统都指向同一个演进方向——**把运行时的重复推理搬到编译时**，同时让查询接口更结构化、更声明式。

| Phase | 核心变化 | 是否需要新基础设施 |
|-------|---------|-------------------|
| **Phase 0** | 管线可组合化 + 检索增强 | 否 |
| **Phase 1** | Schema 约束 + 实体归一化 + 声明式接口 | 否 |
| **Phase 2** | 社区检测 + 知识制品 + 时序图谱 + Agent 记忆 | 是（图数据库） |
