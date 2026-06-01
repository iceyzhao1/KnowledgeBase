# agent_serving_java 审查报告

> 审查日期：2026-06-01
> 基线版本：v1.2+（参考 docs/2026-05-28-project-status-overview.md）

## 1. 目录结构

```
agent_serving_java/
├── pom.xml
└── src/main/java/com/coremasterkb/serving/
    ├── AgentServingApplication.java          # Spring Boot 启动类
    ├── api/
    │   ├── GlobalExceptionHandler.java       # 全局异常处理
    │   ├── HealthController.java             # 健康检查
    │   └── SearchController.java             # 搜索入口
    ├── application/
    │   ├── ContextAssembler.java             # 结果组装
    │   ├── QueryUnderstandingEngine.java      # 查询理解
    │   ├── RetrievalRouter.java              # 检索路由
    │   └── SearchService.java                # 搜索编排器（核心）
    ├── config/
    │   ├── CorsConfig.java                   # CORS 配置
    │   ├── ServingBeans.java                 # Spring Bean 配置
    │   └── ServingProperties.java            # 配置属性（record）
    ├── domain/（20+ record 类型）
    │   ├── ActiveScope, AssemblyConfig, ContextItem, ContextPack
    │   ├── ContextQuery, ContextRelation, EntityRef, EvidenceGroup
    │   ├── EvidenceNeed, ExpansionConfig, FusionConfig, Issue
    │   ├── OrchestratorResult, QueryUnderstanding, RerankConfig
    │   ├── RerankTraceStep, RetrievalCandidate, RetrievalQuery
    │   ├── RetrievalRoutePlan, RouteConfig, RouteTrace, ScoreChain
    │   ├── SearchRequest, ServingConstants, SourceRef, SubQuery
    │   ├── Trace, TraceStage
    ├── domainpack/
    │   ├── DomainContext.java                # ThreadLocal 域上下文
    │   ├── DomainPackReader.java             # YAML 域配置加载
    │   ├── DomainPoolManager.java            # 多域连接池管理
    │   ├── DomainRegistry.java               # 域注册表
    │   ├── DomainRegistryEntry.java          # 域注册条目（record）
    │   ├── DomainRoutingDataSource.java       # 动态数据源路由
    │   └── ServingDomainProfile.java          # Serving 端域配置
    ├── entity/（7 个实体）
    │   ├── AssetBuildDocumentSnapshot, AssetDocument, AssetPublishRelease
    │   ├── AssetRawSegment, AssetRawSegmentRelation
    │   ├── AssetRetrievalEmbedding, AssetRetrievalUnit
    │   └── ServingQueryLog
    ├── evidence/
    │   └── EvidenceRoleClassifier.java       # 证据角色分类器
    ├── infrastructure/
    │   ├── EmbeddingClient.java              # 嵌入客户端
    │   ├── LlmClient.java                   # LLM 服务客户端
    │   ├── PgConfig.java                    # PostgreSQL 配置
    │   └── ServingTemplates.java            # LLM 模板注册
    ├── mapper/（7 个 MyBatis Mapper）
    │   ├── AssetBuildDocumentSnapshotMapper
    │   ├── AssetDocumentMapper
    │   ├── AssetPublishReleaseMapper
    │   ├── AssetRawSegmentMapper
    │   ├── AssetRawSegmentRelationMapper
    │   ├── AssetRetrievalEmbeddingMapper
    │   ├── AssetRetrievalUnitMapper
    │   ├── ServingQueryLogMapper
    │   └── result/（6 个结果行类型）
    │       ├── DocumentSourceRow, EmbeddingRow, ExpandedSegmentRow
    │       ├── FtsResultRow, NeighborRow, RelationRow, SegmentWithMetaRow
    ├── observability/
    │   ├── QueryLogAspect.java               # AOP 查询日志
    │   ├── QueryLogService.java              # 查询日志服务
    │   └── TraceCollector.java               # 阶段追踪收集器
    ├── pipeline/
    │   ├── FusionStrategy.java               # 融合策略接口
    │   ├── IdentityFusion.java               # 直通融合
    │   ├── RetrievalOrchestrator.java         # 检索编排器
    │   ├── RRFFusion.java                    # 标准 RRF 融合
    │   └── WeightedRRFFusion.java            # 加权 RRF 融合
    ├── rerank/
    │   ├── LlmReranker.java                  # LLM 直接重排
    │   ├── LlmServiceReranker.java           # LLM 服务重排
    │   ├── Reranker.java                     # 重排接口
    │   ├── RerankPipeline.java               # 级联重排管道
    │   ├── ScoreReranker.java                # 分数兜底重排
    │   └── ServiceReranker.java              # 服务重排（抽象）
    ├── repository/
    │   ├── AssetRepository.java              # 资产仓储
    │   └── SchemaAdapter.java                # Schema 适配器
    ├── retrieval/
    │   ├── DenseVectorRetriever.java         # 向量检索
    │   ├── EntityExactRetriever.java         # 实体精确匹配
    │   ├── FtsRetriever.java                 # 全文检索（三级降级）
    │   ├── GraphExpander.java                # BFS 图扩展
    │   └── Retriever.java                    # 检索接口
    └── util/
        └── JsonUtils.java                    # JSON 工具

测试文件（17个）：
src/test/java/com/coremasterkb/serving/
├── AbstractPgIntegrationTest.java
├── api/HealthControllerTest, SearchControllerTest
├── application/ContextAssemblerTest, RetrievalRouterTest
├── domain/DomainRecordWithMethodsTest
├── evidence/EvidenceRoleClassifierTest
├── mapper/AssetPublishReleaseMapperIT, AssetRawSegmentMapperIT, AssetRawSegmentRelationMapperIT
├── pipeline/IdentityFusionTest, RetrievalOrchestratorTest, RRFFusionTest, WeightedRRFFusionTest
├── repository/AssetRepositoryIT
├── rerank/RerankPipelineTest, ScoreRerankerTest
├── retrieval/EntityExactRetrieverIT, GraphExpanderIT
├── system/HealthE2ETest, SearchE2ETest
└── util/JsonUtilsTest
```

## 2. API 端点

| 路由 | HTTP方法 | 功能 | 状态 |
|------|----------|------|------|
| `/api/v1/search` | POST | 主检索端点（返回 ContextPack） | ✅ |
| `/health` | GET | 健康检查 | ✅ |

## 3. 核心 Pipeline（SearchService.search）

**11 阶段流水线**：

1. **Resolve Domain** — 解析 effectiveDomain，设置 `LlmClient.knowledgeDomain`（确保后续所有 LLM 调用携带正确域），验证 DB 可达，设置 DomainContext
2. **Load Domain Profile** — 加载领域配置（scenario_pack）
3. **Query Understanding**（并行 Embedding）— LLM 理解查询意图、提取实体、关键词
4. **Retrieval Router** — 根据领域配置路由到不同检索通道
5. **Resolve Scope** — 解析 active release → build → snapshot IDs
6. **Collect Query Embedding** — 获取查询向量（与步骤 3 并行发起）
7. **Retrieve**（多路并行）— 从所有启用的检索通道召回候选
8. **Fuse** — 融合多路结果（RRF / Weighted RRF / Identity）
9. **Rerank** — 级联重排（Model → LLM → Score）
10. **Assemble ContextPack** — 组装最终结果包
11. **Build Debug Info** — 可选调试信息

**并发模型**：`Executors.newVirtualThreadPerTaskExecutor()`（Java 21 虚拟线程）

## 4. 检索通道实现

| 通道 | 实现类 | 算法 | 状态 |
|------|--------|------|------|
| `fts` | `FtsRetriever` | 三级降级：tsvector → pg_trgm → LIKE | ✅ |
| `dense_vector` | `DenseVectorRetriever` | pgvector cosine distance（服务端 ANN） | ✅ |
| `entity_exact` | `EntityExactRetriever` | JSONB @> containment（固定分 0.95） | ✅ |
| `graph_expand` | `GraphExpander` | BFS 图遍历（relation graph） | ✅ |

### FtsRetriever 三级降级

- Level 1: `websearch_to_tsquery` + jieba 分词 + scope 下推
- Level 2: `pg_trgm` 三元组相似度（Level 1 无结果时）
- Level 3: `LIKE '%token%'` + 关键词命中率评分（Level 2 无结果时）
- 每级都有 scope 消除后自动重试机制

### DenseVectorRetriever

- pgvector `<=>` cosine distance 操作符
- 服务端 ANN（避免 JVM 加载所有向量）
- Scope 过滤下推（JSONB containment）
- 无结果时自动去掉 scope 重试

### EntityExactRetriever

- 从 `query.entities()` 提取实体名
- Fallback：keywords 中 ≥2 字符的词作为类实体词
- JSONB `@>` containment 查询（GIN 索引）
- 固定分数 = **0.95**

### GraphExpander

- BFS 遍历 segment relation 图
- 支持 maxDepth、relationTypes 过滤、maxResults 限制
- 每个 expanded segment 记录 depth 和 root seed ID
- 支持 snapshotIds 范围限制

## 5. 融合策略

| 策略 | 实现类 | 算法 |
|------|--------|------|
| RRF | `RRFFusion` | score = Σ(1 / (k + rank_i)), k=60 |
| Weighted RRF | `WeightedRRFFusion` | score = Σ(weight_j / (k + rank_j)) |
| Identity | `IdentityFusion` | 直通，不做融合 |

由 Domain Profile 的 `fusion.method` 控制。

## 6. 重排管道（级联）

```
Model Reranker (LlmServiceReranker / ZhipuModelReranker)
  → 成功则用
  → 失败则继续

LLM Reranker (LlmReranker)
  → 仅在 routePlan.rerank.method 为 "llm" 或 "cascade" 时尝试
  → 失败则继续

Score Reranker (ScoreReranker)
  → 永远成功（兜底）
```

统一后处理：
- 标注 rerankScore 到 ScoreChain
- 最低分阈值过滤（< 0.01 移除）
- 截断到 maxItems（默认 10）

## 7. 多域支持

- `DomainPoolManager` + `DomainRoutingDataSource`：按域路由到不同数据库
- `DomainContext`（ThreadLocal）：设置当前线程的域上下文
- `DomainRegistry`：从 domain_registry.yaml 加载域注册表
- 域隔离连接池：每个域独立 HikariCP（lazy 创建）
- 域验证：请求开始时验证 DB 可达

## 8. 可观测性

- `TraceCollector`：记录每阶段耗时，生成 Trace 对象
- `QueryLogAspect`：AOP 切面记录查询日志
- `QueryLogService`：查询日志持久化
- `RerankTraceStep`：重排步骤追踪
- `RouteTrace`：路由级别追踪（候选数、延迟、跳过原因）
- Debug 模式：请求中 `debug=true` 返回完整调试信息

## 9. 与其他服务的交互

| 服务 | 交互方式 | 用途 |
|------|----------|------|
| llm_service | HTTP（RestTemplate） | 查询理解、模板注册、Embedding、Rerank |
| main_control_service | 间接（通过 YAML 文件） | 加载 domain_registry.yaml 和 scenario_pack |
| PostgreSQL | JDBC（MyBatis-Plus + HikariCP） | 数据读取（asset_core 库） |

## 10. 技术栈

- Java 21+（virtual threads, records, sealed types, pattern matching）
- Spring Boot 3（constructor injection, @ConfigurationProperties）
- MyBatis-Plus（mapper XML + 注解）
- PostgreSQL + pgvector + pg_trgm
- HikariCP（连接池）
- jieba-java（中文分词）
- RestTemplate（HTTP 客户端）

## 11. 测试覆盖

共 **17 个测试文件**：
- 单元测试：Controller, Service, Domain, Pipeline, Rerank, Util
- 集成测试（IT）：Mapper, Repository, Retriever（需要 PostgreSQL）
- E2E 测试：Health, Search

## 12. 相比基线的变化

与 05-28 基线一致，核心功能未发生重大变化。实现细节：
- 域优先执行顺序已固化（DomainContext 在并行任务前设置）
- 虚拟线程已稳定使用
- 重排管道级联逻辑完整

## 13. 已知问题与待完善项

### 高优先级
1. **缺少结果缓存** — 重复查询每次都走完整 Pipeline，缺少 Redis / Caffeine 缓存层
2. **缺少流式响应** — 当前只支持同步返回，不支持 SSE 流式输出

### 中优先级
3. **缺少指标收集** — 无 Micrometer/Prometheus 指标，无法监控 QPS、延迟分布、错误率
4. **实体检索精确度** — `escapeJson` 手动拼接 JSONB 参数，可能存在边缘情况
5. **缺少 API 文档** — 无 Swagger/OpenAPI 自动生成

### 低优先级
6. **Retriever 实现类重复代码** — `putIfNotNull` helper 在每个 Retriever 中重复
7. **LlmClient 线程安全** — `knowledgeDomain` 使用 volatile，但高并发下可能错乱
8. **缺少 Circuit Breaker** — 对 llm_service 调用无熔断机制

## 14. 完成度评估

| 维度 | 完成度 | 备注 |
|------|--------|------|
| 核心 Pipeline | **98%** | 11 阶段完整实现 |
| 检索通道 | **95%** | 4 通道完整，三级降级 |
| 多域支持 | **95%** | 连接池路由完整 |
| 可观测性 | **80%** | Trace 完整，缺指标 |
| 测试覆盖 | **75%** | 17 个测试文件，集成测试需 PG |
| API 文档 | **30%** | 缺 Swagger |
| 缓存 | **0%** | 未实现 |
