# 本体+图数据库演进：数据层痛点分析

> 日期：2026-06-01
> 目标：从当前 Graph-RAG（PostgreSQL 关系存储 + pgvector）演进到本体驱动 + 图数据库架构
> 参考：`old/Self_Knowledge_Evolve`（以下简称 SKE）的完整设计与实现
> 面向：与数据库实验室的合作讨论

## 0. 两个系统的数据层对比

| 维度 | CoreMasterKB（当前） | SKE（目标态参考） |
|------|---------------------|-------------------|
| **数据库引擎** | PostgreSQL × 1（三 schema 分离） | PostgreSQL × 2 + Neo4j × 1 + MinIO × 1 |
| **图存储** | 无图数据库；段间关系存在 PG 表中（`asset_raw_segment_relations`） | Neo4j 双层图（本体推理层 + 知识溯源层） |
| **本体模型** | 无本体；`semantic_role`（11种）+ `block_type`（9种）扁平分类 | 五层本体（概念/机制/方法/条件/场景）+ 77种关系 + 828条别名 |
| **知识表示** | Retrieval Units（文本片段）= 检索终点 | Fact（SPO 三元组）= 知识原子，可推理、可溯源、可合并、可冲突检测 |
| **置信度** | 无 | 五维评分（source_authority × 0.30 + extraction_method × 0.20 + ontology_fit × 0.20 + cross_source_consistency × 0.20 + temporal_validity × 0.10） |
| **知识治理** | 无（无候选词、无演化门控、无冲突检测） | 完整闭环：候选词发现 → 六道门控 → 人工审批 → YAML + Git 版本管理 |
| **查询能力** | 4路召回（FTS + Dense + Entity + Graph Expand）+ RRF + Cascade Rerank | 声明式查询引擎 5 代数原语（seed/expand/combine/aggregate/project）+ 21 语义算子 |
| **证据溯源** | `source_refs_json`（JSONB 字段） | Fact → Evidence → Segment → Document 完整四层溯源链 |

---

## 1. 痛点一：关系数据无法表达图遍历语义

### 1.1 现状

当前 `asset_raw_segment_relations` 表存储段间关系：

```sql
-- 当前实现：平面关系表
asset_raw_segment_relations (
    source_segment_id, target_segment_id, relation_type, weight, confidence, distance
)
```

15 种 RST 关系（ELABORATES, CAUSES, CONTRASTS_WITH 等）以行的形式存在 PG 中。

### 1.2 痛点

1. **图遍历必须递归 SQL**：BFS/DFS 需要用 `WITH RECURSIVE`， PostgreSQL 递归 CTE 在深度 >3 时性能急剧下降（Serving 的 `GraphExpander` 当前 `maxDepth=2` 就是为了规避这个问题）
2. **多跳路径查询不可行**：SKE 的声明式查询引擎依赖 Neo4j 的原生图遍历做 `expand`、`path`、`dependency_closure`、`impact_propagate`，PG 用递归 CTE 无法高效实现
3. **关系类型过滤推不下去**：SKE 的 `expand` 算子支持 `any_of: ["depends_on", "explains"]` 类型的白名单过滤，PG 中每加一种过滤条件就需要额外索引或全表扫描
4. **无法做图算法**：社区检测（Louvain/Label Propagation）、最短路径、PageRank、中心度分析等图算法在 PG 中没有原生支持

### 1.3 影响

- Serving 的 `GraphExpander` 只能做 2 跳 BFS，无法做跨文档语义推理
- 无法实现 SKE 的依赖闭包（`dependency_closure`）和影响传播（`impact_propagate`）算子
- 无法实现跨层推理（从"概念"穿越到"方法"再到"条件"的五层路径查询）

---

## 2. 痛点二：缺少知识原子层（Fact / Evidence）

### 2.1 现状

当前最小知识单元是 `asset_retrieval_units`（检索单元），本质是"一段文本"。它是检索的终点，不是知识的原子。

### 2.2 SKE 的 Fact 模型

```sql
-- SKE 的知识原子
facts (fact_id, subject, predicate, object, qualifier, domain, confidence, lifecycle_state)
evidence (evidence_id, fact_id, source_doc_id, segment_id, exact_span, source_rank, extraction_method, evidence_score)
```

### 2.3 痛点

1. **无法做事实级去重和合并**：当前只有文档级和段落级去重（content_hash / normalized_hash），没有"同一个事实被多个文档表述"的去重能力。SKE 的 Stage 5（Dedup）使用 SimHash + Embedding 在 Fact 级做语义去重
2. **无法做冲突检测**：当两个文档对同一事实给出矛盾描述时（例如不同厂商的配置差异），当前系统无感知。SKE 的 `conflict_detector` 检测 `cardinality=one` 谓语的矛盾事实
3. **无法做置信度评分**：当前检索结果没有来源权威度、提取方法、跨源一致性等置信度维度。SKE 的五维评分让每条知识都有量化可信度
4. **无法做事实生命周期管理**：当前知识只有 active 一种状态。SKE 的 Fact 有 `active / conflicted / superseded / merged` 四种状态，支持知识老化

### 2.4 影响

- 知识无法"越用越准"——没有演化机制
- 用户无法判断检索结果的可信度
- 跨文档同一事实重复返回，浪费 context window

---

## 3. 痛点三：缺少本体层（Ontology）

### 3.1 现状

当前 `semantic_role` 有 11 种值（concept / procedure / principle / requirement 等），`block_type` 有 9 种值。这是扁平的、文档级的分类标签，不是可推理的本体。

### 3.2 SKE 的五层本体

```
L1 概念层（114 节点）→ L2 机制层（24 节点）→ L3 方法层（22 节点）→ L4 条件层（20 节点）→ L5 场景层（13 节点）
+ 77 种关系 + 828 条别名（含中英文 + 厂商变体）
```

### 3.3 痛点

1. **检索结果无层级结构**：当前 `SearchService` 返回扁平的 `ContextItem` 列表，没有"这是概念定义 / 这是配置方法 / 这是适用条件"的层级区分。SKE 的 Copilot 按 `scenario → condition → method → mechanism → concept` 五层分组回答
2. **术语归一化缺失**：用户问"UPF"，文档里写的是"用户面功能"，当前系统依赖 FTS 的分词匹配（jieba），无法做到精确的术语归一化。SKE 的 `lexicon_aliases` 表有 828 条别名映射
3. **无本体对齐（Align）阶段**：当前 Pipeline 的 enrich 阶段做了角色分类和元数据提取，但不会把 segment 对齐到本体节点。SKE 的 Stage 3（Align）做精确匹配 → 别名匹配 → Embedding 模糊匹配
4. **无法做候选词治理**：新领域术语（如"5G LAN"、"网络切片"）的出现无法被系统发现、评分、审批、晋升。SKE 有完整的 `evolution_candidates` + 六道门控 + Auto-Promote 机制

### 3.4 影响

- Agent 无法根据查询意图选择合适的知识层级
- 跨厂商术语无法归一化（Huawei 的"Eth-Trunk" vs Cisco 的"Port-Channel"）
- 知识库无法自我扩充，只能靠人工导入

---

## 4. 痛点四：Serving 侧的查询引擎能力不足

### 4.1 现状

当前 Serving 的 `SearchService` 是一个硬编码的 11 阶段管道：query understanding → route plan → 4路召回 → fusion → rerank → assemble。它是面向"文档检索"设计的。

### 4.2 SKE 的声明式查询引擎

```json
{"intent": "BGP依赖链", "steps": [
  {"op": "seed", "by": "alias", "value": "BGP", "as": "$bgp"},
  {"op": "expand", "from": "$bgp", "any_of": ["depends_on"], "depth": 2, "as": "$deps"},
  {"op": "expand", "from": "$deps", "any_of": ["tagged_in"], "as": "$segs"},
  {"op": "aggregate", "function": "rerank", "from": "$segs", "query": "BGP dependency", "limit": 10, "as": "$result"}
]}
```

### 4.3 痛点

1. **无法做图推理查询**：依赖闭包（"BGP 的所有依赖是什么"）、影响传播（"修改 OSPF cost 会影响什么"）在当前架构中不可能。Serving 的 `GraphExpander` 只做 2 跳 BFS 遍历段间关系，不做语义推理
2. **查询计划不可组合**：当前 11 阶段是固定的，不能按需组合。SKE 的 5 个代数原语可以自由组合出任意查询
3. **缺少 21 个语义算子**：`dependency_closure`、`impact_propagate`、`conflict_detect`、`stale_knowledge`、`cross_layer_check` 等高级算子当前完全没有
4. **无图算法支持**：社区检测、中心度分析、路径权重计算等需要图数据库原生支持

### 4.4 数据层要求

- 需要图数据库支持原生图遍历和路径查询
- 需要在图数据库中存储本体节点和本体关系
- 需要跨 PG（关系数据）和图数据库（图数据）的混合查询能力

---

## 5. 痛点五：跨库事务一致性

### 5.1 现状

当前三个 schema（`asset_core` / `mining_runtime` / `agent_llm_runtime`）在同一 PostgreSQL 实例中，事务一致性由 PG 本地事务保证。

### 5.2 引入图数据库后的挑战

SKE 使用三个独立的存储引擎：
- PostgreSQL（`telecom_kb`）：文档、段、事实、治理
- PostgreSQL（`telecom_crawler`）：爬虫任务
- Neo4j：本体图 + 知识图

**SKE 的一致性方案**：最终一致性——Pipeline 按阶段顺序执行，每阶段完成后写入对应的存储引擎，不保证跨引擎事务。

### 5.3 痛点

1. **双写一致性**：Mining Pipeline 需要同时向 PG 写入资产数据 + 向图数据库写入本体/事实数据。如何保证要么全成功、要么全回滚？
2. **版本同步**：本体 YAML → PG lexicon + Neo4j 节点，三处数据如何保持一致？SKE 用 `load_ontology.py` 做冷启动同步，但没有运行时增量同步
3. **跨引擎 JOIN**：Serving 查询可能需要同时从 PG 读检索文本 + 从图数据库读路径信息。SKE 的 `context_assemble` 算子分两次查询再合并
4. **数据生命周期**：Build/Release 生命周期如何映射到图数据库？当前 PG 中的 release 机制（active release → build → snapshots）如何与图数据库中的节点版本对齐？

### 5.4 与数据库实验室的合作点

- **分布式事务方案**：是否需要 Saga / 2PC / Outbox Pattern？还是接受最终一致性 + 补偿？
- **CDC（Change Data Capture）**：PG → 图数据库的增量同步方案
- **查询路由层**：如何透明地在 PG 和图数据库之间路由查询？

---

## 6. 痛点六：图数据库选型与 Schema 设计

### 6.1 SKE 的 Neo4j Schema

```
节点标签（15 个唯一约束）：
  OntologyNode, MechanismNode, MethodNode, ConditionRuleNode, ScenarioPatternNode,
  Concept, Entity, CaseNode, Fact, KnowledgeSegment, SourceDocument, Evidence,
  Alias, CandidateConcept, OntologyVersion

关系类型（动态）：depends_on, explains, implements, ... 77 种本体关系
  + tagged_in, rst_adjacent, evidenced_by, supported_by, extracted_from, belongs_to, alias_of

双层分离：
  本体推理层：OntologyNode ─[DEPENDS_ON]→ OntologyNode
  知识溯源层：Fact ─[:SUPPORTED_BY]→ Evidence ─[:EXTRACTED_FROM]→ Segment ─[:BELONGS_TO]→ Document
  两层通过属性松耦合（Fact.subject = node_id），无图边
```

### 6.2 选型考量

| 图数据库 | 优势 | 劣势 |
|---------|------|------|
| **Neo4j**（SKE 选择） | Cypher 表达力强、生态成熟、APOC 库丰富 | 社区版单机、企业版贵、JVM 内存开销大、与 PG 双写复杂 |
| **Apache AGE** | PG 原生扩展、Cypher on PG、无需新引擎 | 社区较小、性能不及原生图数据库、特性不够成熟 |
| **NebulaGraph** | 分布式、中文社区好、性能好 | 学习曲线陡、运维复杂、Cypher 支持不完整 |
| **PostgreSQL + recursive CTE** | 无需引入新引擎、运维简单 | 深层遍历性能差、无图算法库 |

### 6.3 合作点

- 是否可以在 PG 之上构建图抽象层（AGE 或自定义），避免引入独立图数据库？
- 图数据库的 Schema 如何与当前的 `asset_core` 表结构对齐？
- 图数据的索引策略（节点属性索引、全文索引、向量索引）如何设计？

---

## 7. 痛点七：数据迁移与渐进式演进

### 7.1 现状数据量级

当前 `asset_core` 中：
- `asset_retrieval_units`：数万条检索单元
- `asset_raw_segments`：数万条段落
- `asset_raw_segment_relations`：数万条关系
- `asset_retrieval_embeddings`：数万条向量

### 7.2 迁移挑战

1. **段 → 事实的鸿沟**：当前的 segment 是文本片段，SKE 的 Fact 是结构化三元组（subject, predicate, object）。需要 LLM 抽取或规则转换，不是简单的数据搬迁
2. **关系语义对齐**：当前 15 种 RST 关系（ELABORATES, CAUSES 等）需要映射到 SKE 的 77 种本体关系。不是 1:1 映射，需要语义分析
3. **本体冷启动**：当前没有本体。需要为云核心网领域构建五层本体（参考 SKE 的 `ontology/domains/` YAML 文件结构）
4. **增量迁移策略**：不能停机迁移。需要在运行中逐步引入本体层和图数据库，同时保持现有检索服务不受影响

### 7.3 合作点

- 增量迁移的数据管道设计
- 双写期间的幂等和去重策略
- 迁移验证的数据一致性校验方案

---

## 8. 痛点八：Embedding 与向量检索的局限

### 8.1 现状

当前使用 pgvector + HNSW 做向量检索，BGE-M3 1024 维。

### 8.2 SKE 的增强方案

- **三级回退**：HTTP BGE-M3 服务 → Ollama → sentence-transformers
- **多向量**：title_vec + content_vec（双向量索引）
- **语义去重**：cosine > 0.90 自动合并
- **候选词去重**：embedding 聚类

### 8.3 痛点

1. **向量只服务于检索**：当前 embedding 只用于 Dense Vector Retriever。SKE 把 embedding 用在更多场景——对齐（Align）、去重（Dedup）、冲突检测、候选词治理
2. **没有跨模态检索**：图数据库中节点属性可以包含 embedding，做"从节点到段"的语义扩展
3. **向量与图遍历的结合**：SKE 的 `seed` 算子支持 `by: "embedding"` 初始化结果集，然后 `expand` 做图遍历。当前架构无法支持这种混合查询模式

---

## 9. 总结：数据库实验室合作的关键议题

### 9.1 高优先级（决定架构走向）

| # | 议题 | 涉及的痛点 |
|---|------|-----------|
| 1 | **图数据库选型**：独立 Neo4j vs PG 扩展（AGE）vs 自研 | 痛点 1, 6 |
| 2 | **跨引擎事务与一致性**：分布式事务 vs 最终一致性 | 痛点 5 |
| 3 | **本体 Schema 设计**：五层本体如何映射到图数据库的节点/边模型 | 痛点 3, 6 |
| 4 | **混合查询路由**：PG 关系查询 + 图遍历的统一查询接口 | 痛点 4, 5 |

### 9.2 中优先级（核心功能实现）

| # | 议题 | 涉及的痛点 |
|---|------|-----------|
| 5 | **Fact 抽取与存储**：从段文本到结构化三元组的数据管道 | 痛点 2 |
| 6 | **增量迁移策略**：现有数据如何渐进式迁入新架构 | 痛点 7 |
| 7 | **图遍历优化**：BFS/DFS/最短路径/社区检测的查询优化 | 痛点 1, 4 |
| 8 | **向量 + 图联合检索**：embedding 空间与图空间的融合查询 | 痛点 8 |

### 9.3 低优先级（锦上添花）

| # | 议题 | 涉及的痛点 |
|---|------|-----------|
| 9 | **知识治理闭环**：候选词发现 → 门控 → 审批 → 晋升 | 痛点 3 |
| 10 | **冲突检测与解决**：跨源事实矛盾的发现与处理 | 痛点 2 |
| 11 | **置信度评分与衰减**：五维评分 + 时效性衰减 | 痛点 2 |
| 12 | **声明式查询引擎移植**：5 代数原语 + 21 算子 | 痛点 4 |

---

## 附录 A：SKE 数据模型（PostgreSQL 核心 12 表）

```
telecom_kb:
  documents          — 源文档（source_url, content_hash, source_rank）
  segments           — 文本段落（raw_text, embedding[1024], title_vec[1024], content_vec[1024]）
  t_rst_relation     — RST 话语关系（src_edu_id → dst_edu_id, relation_type, nuclearity）
  segment_tags       — 段落标签（tag_type, tag_value, ontology_node_id）
  facts              — 知识三元组（subject, predicate, object, confidence, lifecycle_state）
  evidence           — 证据链（fact_id → segment_id, exact_span, source_rank）
  lexicon_aliases    — 别名词典（surface_form → canonical_node_id）
  governance.evolution_candidates — 候选词（surface_forms[], composite_score, review_status）
  governance.conflict_records     — 冲突记录
  governance.ontology_versions   — 本体版本
  governance.review_records      — 审批记录
  system_stats_snapshots         — 监控快照
```

## 附录 B：SKE 数据模型（Neo4j 核心结构）

```
推理层（用于图遍历）:
  (:OntologyNode {node_id, domain, knowledge_layer, lifecycle_state})
  (:MechanismNode {node_id, domain, lifecycle_state})
  (:MethodNode {node_id, domain, lifecycle_state})
  (:ConditionRuleNode {node_id, domain, lifecycle_state})
  (:ScenarioPatternNode {node_id, domain, lifecycle_state})
  (:Alias {alias_id, surface_form, canonical_node_id})

  关系（77种本体关系 + 保留关系）:
    -[:DEPENDS_ON {fact_count, max_confidence}]->
    -[:EXPLAINS {fact_count, max_confidence}]->
    -[:IMPLEMENTED_BY {fact_count, max_confidence}]->
    ...

溯源层（用于证据追溯）:
  (:Fact {fact_id, subject, predicate, object, confidence, lifecycle_state})
  (:Evidence {evidence_id, source_rank, extraction_method})
  (:KnowledgeSegment {segment_id, raw_text, segment_type})
  (:SourceDocument {source_doc_id, title, source_url})

  关系:
    (:Fact)-[:SUPPORTED_BY]->(:Evidence)-[:EXTRACTED_FROM]->(:KnowledgeSegment)-[:BELONGS_TO]->(:SourceDocument)
```

## 附录 C：当前 CoreMasterKB 数据模型（PostgreSQL 20 表）

```
asset_core（11 表）:
  asset_source_batches        — 数据源批次
  asset_documents             — 逻辑文档
  asset_document_snapshots    — 内容快照（SHA256 去重）
  asset_document_snapshot_links — 文档-快照关联
  asset_raw_segments          — 原始段落（block_type, semantic_role, section_path）
  asset_raw_segment_relations — 段间关系（15 种 RST 类型）
  asset_retrieval_units       — 检索单元（raw_text, search_text, search_vector, entity_refs_json）
  asset_retrieval_embeddings  — 向量嵌入（embedding_vector_vec[1024]）
  asset_builds                — 构建记录
  asset_build_document_snapshots — 构建-文档关联
  asset_publish_releases      — 发布版本

mining_runtime（3 表）:
  mining_runs                 — 挖掘运行
  mining_run_documents        — 运行文档
  mining_run_stage_events     — 阶段事件

agent_llm_runtime（6 表）:
  agent_llm_prompt_templates  — 提示模板
  agent_llm_tasks             — 任务队列
  agent_llm_requests          — 请求详情
  agent_llm_attempts          — 尝试记录
  agent_llm_results           — 结果解析
  agent_llm_events            — 事件流水
```
