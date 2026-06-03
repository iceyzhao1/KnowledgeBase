# PRD：CoreMasterKB 检索服务下一阶段改进

> 日期：2026-06-03
> 版本：v1.0
> 范围：agent_serving_java + mcp_server + knowledge_mining 联动改进
> 基线：v1.2+（参考 docs/2026-05-28-project-status-overview.md）

---

## 0. 文档目的

基于对 12 份 output 文档（01-12）和 agent_serving_java 全量代码的深入理解，识别检索服务下一步最该做的改进点，形成可落地的产品需求。

**不包含**具体实现方案（代码级别），只描述 **做什么、为什么、期望效果、验收标准**。

---

## 1. 当前状态总结

### 1.1 检索 Pipeline（SearchService.search）

11 阶段管道，已完整实现：

```
Resolve Domain → Load Profile → Query Understanding (+Embedding 并行)
→ Retrieval Router → Resolve Scope → Collect Embedding
→ Retrieve (4路并行) → Fuse (Weighted RRF) → Rerank (级联)
→ Assemble ContextPack → Build Debug Info
```

### 1.2 四路检索通道

| 通道 | 实现 | 状态 |
|------|------|------|
| FTS | 三级降级 (tsvector → pg_trgm → LIKE) + jieba 分词 + scope 自动重试 | 成熟 |
| Dense Vector | pgvector cosine + scope 下推 + 无结果自动去 scope | 成熟 |
| Entity Exact | JSONB @> containment + 固定分 0.95 | 成熟 |
| Graph Expand | BFS 2-hop + relationTypes 过滤 + maxResults | 成熟 |

### 1.3 已有但未集成的能力

| 能力 | 实现状态 | 集成状态 |
|------|---------|---------|
| EvidenceRoleClassifier (5 级证据角色) | 代码已写完 | **未集成到 SearchService** |
| 意图感知路由 (7 意图 → 不同权重/topK) | RetrievalRouter 已实现 | 已集成 |

### 1.4 已确认的缺失项

| 缺失 | 影响 | 来源 |
|------|------|------|
| 无结果缓存 | 重复查询每次都走完整 Pipeline（~400ms+） | 01-audit #1, 02-roadmap Phase 2 |
| 无可观测性指标 | 无法监控 QPS、延迟分布、各通道命中率 | 01-audit #3, 02-roadmap Phase 1 |
| 无 LLM 熔断 | llm_service 不可用时无优雅降级 | 01-audit #8, 02-roadmap Phase 4 |
| 证据角色未集成 | MCP Client 无法区分 direct_answer/support/background | 代码审查发现 |
| 无树索引导航 | section_path 层级信息未利用 | 10/11/12 文档 |
| 无 HyDE 检索 | 语义模糊查询召回不足 | 12-合成文档 方向 4 |
| 无 HyDE/RAG Fusion | 语义模糊查询召回不足 | 12-合成文档 方向 4 |
| 话语角色未传递 | Mining 端 RST 关系信息未传递到 Serving | 03-mining-audit |
| 无 Contextual Retrieval | 索引时未注入上下文 | 02-roadmap |

---

## 2. 改进需求（按优先级排序）

### P0：集成已有能力（1-2 天）

#### PRD-01：集成 EvidenceRoleClassifier 到主管道

**背景**：EvidenceRoleClassifier 已实现 5 级分类（direct_answer / support / contrast / background / missing），但代码注释明确写了 "NOT integrated in main pipeline yet"。MCP Server 的指令系统已定义了 evidence_role 分类体系，但 serving 端实际未在输出中携带此信息。

**需求**：在 ContextAssembler 组装阶段之后、返回 ContextPack 之前，调用 EvidenceRoleClassifier 对所有 ContextItem 标注 evidence_role。

**期望效果**：
- MCP Client 接收到的每个 ContextItem 携带 `evidence_role` 字段
- MCP Client 可以据此区分"直接答案"和"背景信息"
- 支持 12-合成文档 中方向 9（声明式查询）的 confidence 原语

**验收标准**：
- `POST /api/v1/search` 返回的 ContextPack.items 中每个 item 包含 `evidence_role` 字段
- 角色分类与得分、seed 关系、RST 关系类型的映射逻辑符合 EvidenceRoleClassifier 的规则

---

#### PRD-02：暴露 MCP health_check 工具

**背景**：mcp_server 的 health_check 工具已实现但被注释掉。MCP Client 无法在查询前检测知识库可用性，可能导致超时或无效请求。

**需求**：取消注释 health_check 工具，暴露为 MCP 工具。

**期望效果**：
- MCP Client 可在查询前调用 health_check 确认可用性
- 不可用时 MCP Client 可提前告知用户，而非等待超时

**验收标准**：
- MCP Client 调用 health_check 返回 `{ available: true/false, message: "..." }`
- serving 不可用时返回 `available: false`

---

### P1：可观测性 + 稳定性（3-5 天）

#### PRD-03：检索 Pipeline 可观测性

**背景**：当前仅有 TraceCollector（阶段耗时追踪）和 QueryLogAspect（AOP 查询日志），缺少 Prometheus 指标。无法监控 QPS、延迟分布、各通道命中率、rerank 成功率。

**需求**：
- 在 SearchService 关键阶段注入 Micrometer 指标
- 暴露 `/actuator/prometheus` 端点
- 关键指标：
  - `serving_search_duration_ms`：全链路延迟（直方图）
  - `serving_retrieval_candidates`：各路由候选数（按 route 标签）
  - `serving_rerank_duration_ms`：重排延迟
  - `serving_rerank_fallback_total`：rerank 降级次数（model → llm → score）
  - `serving_query_intent_total`：意图分布（按 intent 标签）
  - `serving_scope_empty_total`：scope 为空导致无结果的次数

**期望效果**：
- Grafana Dashboard 可展示检索服务全链路状态
- 可基于指标告警（如 rerank 降级率 > 50%）
- 可量化各检索通道的贡献度

**验收标准**：
- `/actuator/prometheus` 返回上述 6 个指标
- 指标包含正确的标签（route、intent、method）
- 不影响检索延迟（指标收集为异步）

---

#### PRD-04：LLM 调用熔断与降级

**背景**：当前对 llm_service 的调用（查询理解、rerank）无熔断机制。llm_service 不可用时，查询理解会 fallback 到规则引擎（已实现），但 rerank 只能 fallback 到 ScoreReranker。缺少 Circuit Breaker 模式。

**需求**：
- 为 LlmClient 的核心方法添加 Resilience4j CircuitBreaker
- 熔断后自动降级：
  - 查询理解 → ruleUnderstand（已有）
  - Rerank → ScoreReranker（已有）
  - Embedding → 跳过 dense_vector 通道（已有）
- 熔断恢复：半开状态探测

**期望效果**：
- llm_service 故障时检索服务不报错，自动降级
- 熔断状态可通过 actuator 端点查看
- 故障恢复后自动切回 LLM 路径

**验收标准**：
- llm_service 停止后，检索请求 100% 成功（降级到规则+分数排序）
- llm_service 恢复后，1 分钟内自动切回 LLM 路径
- 熔断事件记录在日志和 actuator 中

---

### P2：检索质量提升（5-7 天）

#### PRD-05：话语角色传递（Mining → Serving 联动）

**背景**：Mining 端已提取 15 种 RST 关系（elaborates, causes, contrasts 等），但这些关系的语义角色（nucleus/satellite）未传递到 Serving 端。当前 Serving 只看 GraphExpand 的 BFS 跳数，不理解关系类型。

**需求**：
- Mining 端：在 enrich 阶段为每个 segment 添加 `discourse_role` 元数据（nucleus / satellite / standalone），基于 RST 关系推断
- Serving 端：ContextAssembler 组装时利用 discourse_role：
  - nucleus 段落优先级高于 satellite
  - 支持/对比/背景的分组可按 discourse_role 辅助判断
- 传递路径：mining 写入 `asset_raw_segments.metadata_json` → serving 通过 retrieval unit 读取

**期望效果**：
- EvidenceRoleClassifier 可结合 discourse_role 做更精准的分类
- ContextPack 中核心信息（nucleus）排在前面
- 辅助信息（satellite）标注为 support 或 background

**验收标准**：
- 新挖掘的 segment 包含 `discourse_role` 字段
- Serving 返回的 ContextItem 中 nucleus 类型的平均排序高于 satellite
- 不影响已有检索性能

---

#### PRD-06：意图感知的检索策略增强

**背景**：当前 RetrievalRouter 已按意图调整各通道权重，但：
- `troubleshooting` 意图未启用 graph_expand（故障诊断需要沿因果链扩展）
- `comparison` 意图未利用 RST 关系中的 `contrasts_with`
- 所有意图的 rerank 方法固定为 score/cascade，未按意图差异化

**需求**：
- `troubleshooting` 意图：启用 graph_expand 通道，权重优先走 causal 关系链（causes → results_in）
- `comparison` 意图：在 graph_expand 中优先走 `contrasts_with` 关系
- `procedure` 意图：启用 graph_expand 走 `purposes` / `enables` 关系
- 每种意图可配置 rerank 策略（而不仅是 needsComparison 触发 cascade）

**期望效果**：
- 故障诊断类查询的图扩展命中率提升
- 对比类查询能返回双方信息
- 操作步骤类查询能返回前后依赖

**验收标准**：
- `troubleshooting` 意图的 route plan 包含 graph_expand 通道
- 3 种新意图有独立的 route policy 配置（在 domain.yaml 中可覆盖）
- 不影响其他意图的检索效果

---

#### PRD-07：section_path 树索引导航

**背景**：`asset_raw_segments` 的 `section_path`（如 "1.2.3"）提供了文档层级信息，但当前检索完全忽略此信息——全库扁平搜索。PageIndex 系统证明"先定位章节再检索"可显著提升精准度。

**需求**：
- Mining 端：已有 section_path，无需改动
- Serving 端：新增 TreeNavigator 步骤（在 Retrieve 之前）：
  - 根据 query understanding 的 scope（如 network_elements=[SMF]）和意图，推断可能的章节范围
  - 规则推断（无需 LLM）：利用 entity → section_path 映射，统计哪些章节路径高频包含该实体
  - 将推断的 section_path 范围作为 RetrievalQuery 的附加过滤条件
  - 无匹配范围时不影响正常检索（全库搜索降级）

**期望效果**：
- 单实体精确查询（如"SMF 会话管理参数"）优先在 SMF 相关章节检索
- 减少全库搜索的噪声
- section_path 利用率为 0 → > 0

**验收标准**：
- 包含明确实体（如 SMF、UPF）的查询，检索结果的 section_path 一致性 > 60%
- 无明确 scope 的查询不受影响（全库搜索）
- 不增加检索延迟（树导航为预计算映射查询）

---

### P3：性能与体验（3-5 天）

#### PRD-08：多级结果缓存

**背景**：当前每次请求都走完整 Pipeline（~400ms+），无缓存。重复查询浪费资源。

**需求**：
- L1：Caffeine 本地缓存（sub-ms 命中）
- L2：Redis 缓存（1-2ms 命中，跨实例共享）
- 缓存 Key：query + domain + scope + channel 的哈希
- 缓存失效：publish/release 操作时清除相关域缓存
- 缓存 TTL：L1=5min，L2=30min

**期望效果**：
- 重复查询命中率 > 30%（典型使用模式）
- 缓存命中时延迟从 ~400ms 降至 < 5ms
- 不影响非重复查询

**验收标准**：
- 相同 query+domain+scope 的第二次请求返回缓存结果
- publish 操作后缓存自动失效
- 缓存命中率可通过 actuator 指标查看
- 缓存结果与实时结果结构一致

---

#### PRD-09：MCP Server 缓存透传

**背景**：MCP Server 是纯透传层，无缓存。即使 serving 端有缓存，MCP Server 层的重复 HTTP 调用仍有开销。

**需求**：
- MCP Server 层增加 TTLCache（5 分钟）
- 缓存 Key：query + domain + scope + entities 的哈希
- 缓存命中时直接返回，不调用 serving

**期望效果**：
- 高频相同查询（如 Agent 多次问同一问题）直接返回缓存
- 减少 serving 负载

**验收标准**：
- 相同查询第二次请求不产生 serving HTTP 调用
- TTL 到期后自动失效

---

## 3. 需求优先级矩阵

| PRD | 优先级 | 工作量 | 依赖 | 风险 |
|-----|--------|--------|------|------|
| PRD-01 证据角色集成 | P0 | 1天 | 无 | 低 |
| PRD-02 MCP health_check | P0 | 0.5天 | 无 | 低 |
| PRD-03 可观测性指标 | P1 | 2天 | 无 | 低 |
| PRD-04 LLM 熔断降级 | P1 | 2天 | 无 | 低 |
| PRD-05 话语角色传递 | P2 | 3天 | Mining 联动 | 中 |
| PRD-06 意图策略增强 | P2 | 2天 | PRD-05 | 中 |
| PRD-07 树索引导航 | P2 | 3天 | 无 | 中 |
| PRD-08 多级缓存 | P3 | 3天 | Redis | 中 |
| PRD-09 MCP 缓存 | P3 | 1天 | PRD-08 | 低 |

## 4. 建议交付节奏

```
Sprint 1（1 周）：P0 + P1
├── PRD-01：证据角色集成（1天）
├── PRD-02：MCP health_check（0.5天）
├── PRD-03：可观测性指标（2天）
└── PRD-04：LLM 熔断降级（2天）

Sprint 2（2 周）：P2
├── PRD-05：话语角色传递（3天）—— Mining + Serving 联动
├── PRD-06：意图策略增强（2天）—— 依赖 PRD-05
└── PRD-07：树索引导航（3天）

Sprint 3（1 周）：P3
├── PRD-08：多级缓存（3天）
└── PRD-09：MCP 缓存（1天）
```

## 5. 明确不做的事

基于 02-roadmap 的工业界研究结论：

1. **不添加 Multi-Query Fusion** — 论文证明 Fusion 在 Reranking 后增益被抵消（Hit@10 反降）
2. **不替换 RRF 为 Learned Fusion** — RRF k=60 已是工业标准
3. **不追求更大检索量** — top 50 候选已是最优范围
4. **不添加 HyDE/RAG Fusion** — 当前 Weighted RRF + 级联 Rerank 已足够，HyDE 的额外 token 成本不划算
5. **不引入图数据库** — 当前 PG 递归 CTE 模拟图遍历已满足 2-hop 需求，图数据库是 Phase 2 的事

## 6. 与演进方向文档的对齐

本 PRD 与 `12-evolution-directions-synthesis.md` 的 10 个演进方向对齐：

| 本 PRD | 对应演进方向 | Phase |
|--------|------------|-------|
| PRD-01 证据角色 | 方向 4 多信号融合、方向 9 声明式查询 | Phase 0 |
| PRD-03 可观测性 | 方向 3 策略化管线（可观测性是前提） | Phase 0 |
| PRD-04 熔断降级 | 方向 3 策略化管线（鲁棒性） | Phase 0 |
| PRD-05 话语角色 | 方向 4 多信号融合（RST 关系作为检索信号） | Phase 0 |
| PRD-06 意图策略 | 方向 8 树索引 + 推理导航（意图分类路由） | Phase 0 |
| PRD-07 树导航 | 方向 8 树索引 + 推理导航 | Phase 0 |
| PRD-08/09 缓存 | 方向 6 编译时知识制品（缓存是第一步） | Phase 0 |

**核心原则**：所有需求都落在 Phase 0（立即可做，无需新基础设施），在现有 PostgreSQL 上完成。

## 7. 成功指标

| 指标 | 当前 | 目标 |
|------|------|------|
| 证据角色覆盖率 | 0% | 100%（所有 ContextItem 标注） |
| 可观测性 | 仅 Trace 日志 | Prometheus 6 指标 |
| LLM 故障时可用性 | 报错 | 100% 降级可用 |
| section_path 利用率 | 0% | > 60%（有 scope 的查询） |
| 重复查询延迟 | ~400ms | < 5ms（缓存命中） |
| graph_expand 启用意图 | 默认启用 | 按意图动态启用 |

---

> 下一阶段：本 PRD 落地后，可启动 Phase 1（Schema 约束实体提取 + 实体归一化 + 声明式查询接口），为 Phase 2（图数据库 + 知识制品 + 时序图谱）奠定基础。
