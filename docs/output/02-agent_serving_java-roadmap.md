# agent_serving_java 工业级演进规划

> 日期：2026-06-01
> 基于 2025-2026 年工业界最佳实践研究

## 1. 核心发现

### 1.1 RAG Fusion 的局限性

**关键论文**：arXiv 2603.02153 — "Scaling RAG with RAG Fusion: Lessons from Industry Deployment"

核心发现：
- Retrieval Fusion 增加召回但 **不改善最终效果**
- Reranking + 截断后，Fusion 增益被抵消
- Hit@10 从 0.51 下降到 0.48
- Fusion 引入额外延迟（+0.89s），无下游收益
- **结论**：在成熟企业系统中，ranking capacity 和 context selection 是主导因素

**对我们的启示**：当前 Weighted RRF + 级联 Rerank 架构已经是最优解。不建议增加 multi-query fusion。

### 1.2 2026 Production RAG 标准架构

来源：Cadence Blog, Appycodes, HLD Handbook

```
Hybrid Retrieval (BM25 + Dense) → RRF Fusion → Cross-Encoder Rerank → Context Assembly
                                      ↑                    ↑
                            50-100 candidates      Top 50 → Top 5-8
```

**我们已具备**：
- ✅ BM25 (FtsRetriever 三级降级)
- ✅ Dense Vector (pgvector cosine)
- ✅ Entity Exact (JSONB containment)
- ✅ Graph Expand (BFS)
- ✅ Weighted RRF Fusion
- ✅ Cascade Rerank (Model → LLM → Score)
- ✅ Context Pack Assembly

**我们缺少的**：
- ❌ Contextual Retrieval（索引时注入上下文）
- ❌ 语义缓存
- ❌ 查询分类路由
- ❌ 可观测性（Micrometer/Prometheus）

## 2. 演进路线图

### Phase 1: 可观测性（优先级最高）

**参考**：Spring Boot Actuator + Micrometer + Prometheus

实现方案：
1. 添加 `micrometer-registry-prometheus` 依赖
2. 在 `SearchService` 中注入 `MeterRegistry`
3. 记录关键指标：
   - `serving_search_duration_ms` — 全链路延迟
   - `serving_retrieval_candidates` — 各路由候选数
   - `serving_rerank_duration_ms` — 重排延迟
   - `serving_cache_hit_rate` — 缓存命中率（Phase 2 后）
4. 暴露 `/actuator/prometheus` 端点
5. 配合 Grafana Dashboard（Spring Boot 3.x Statistics #19004）

**预估工作量**：1-2 天

### Phase 2: 多级缓存

**参考**：Caffeine L1 + Redis L2 (Cache-Aside Pattern)

架构：
```
Request → L1 Caffeine (sub-μs) → L2 Redis (1-2ms) → Full Pipeline (400ms+)
```

实现方案：
1. 查询哈希（query + domain + scope）作为缓存 Key
2. L1: Caffeine (maximumSize=500, TTL=5min, W-TinyLFU)
3. L2: Redis (TTL=30min, 跨实例共享)
4. 缓存失效：publish/release 操作时清除相关缓存
5. 通过 Redis pub/sub 实现跨实例 L1 失效

关键代码示例：
```java
@Configuration
@EnableCaching
public class SearchCacheConfig {
    @Bean
    @Primary
    public CacheManager cacheManager(RedisConnectionFactory factory) {
        CaffeineCacheManager l1 = new CaffeineCacheManager();
        l1.setCaffeine(Caffeine.newBuilder()
            .maximumSize(500)
            .expireAfterWrite(5, TimeUnit.MINUTES)
            .recordStats());

        RedisCacheManager l2 = RedisCacheManager.builder(factory)
            .cacheDefaults(RedisCacheConfiguration.defaultCacheConfig()
                .entryTtl(Duration.ofMinutes(30)))
            .build();

        return new LayeredCacheManager(l1, l2);
    }
}
```

**预估工作量**：3-5 天

### Phase 3: 查询分类路由

**参考**：2026 Production RAG — intent-based routing

当前 `QueryUnderstandingEngine` 已有 7 种意图分类。下一步：
1. 根据意图类型动态调整检索策略：
   - `command_usage` / `entity_lookup` → 加大 entity_exact 权重
   - `concept_lookup` → 加大 dense_vector 权重
   - `troubleshooting` → 加大 graph_expand 权重
2. 根据意图调整 rerank candidate 数量：
   - 高精度意图 → top 20 重排
   - 模糊意图 → top 50 重排

**预估工作量**：2-3 天

### Phase 4: Circuit Breaker

**参考**：Resilience4j

为 LLM Service 调用添加熔断：
```java
@CircuitBreaker(name = "llmService", fallbackMethod = "fallbackUnderstanding")
public QueryUnderstanding understand(String query, ServingDomainProfile profile) {
    // ...
}
```

**预估工作量**：1 天

### Phase 5: API 文档

添加 SpringDoc OpenAPI：
```xml
<dependency>
    <groupId>org.springdoc</groupId>
    <artifactId>springdoc-openapi-starter-webmvc-ui</artifactId>
</dependency>
```

自动生成 Swagger UI，包括 SearchRequest/ContextPack 的完整 Schema。

**预估工作量**：0.5 天

## 3. 不建议做的事

基于工业界研究的明确建议：

1. **不要添加 Multi-Query Fusion** — 论文证明 Fusion 在 Reranking 后增益被抵消
2. **不要替换 RRF 为 Learned Fusion** — RRF k=60 已是工业标准，无需调优
3. **不要追求更大检索量** — top 50 候选已是最优范围
4. **不要过度调参** — 除非有 ≥200 标注查询的评估集

## 4. 参考资源

- [RAG Fusion 论文](https://arxiv.org/pdf/2603.02153) — Fusion 在生产环境的效果评估
- [Cadence Production RAG Architecture](https://cadence.withremote.ai/blog/production-rag-architecture) — 2026 标准架构
- [HLD Enterprise RAG](https://hld.handbook.academy/curriculum/case-studies/enterprise-rag/) — 企业级 RAG 设计
- [Hybrid RAG + BM25 + RRF Guide](https://aiworkflowlab.dev/article/how-to-build-hybrid-search-rag-bm25-rrf-fusion-cross-encoder-reranking) — 实践指南
- [Spring Boot Caching: Caffeine + Redis](https://devops-monk.com/2026/05/spring-boot-caching-caffeine-redis/) — 多级缓存实现
- [Atlas Search](https://github.com/nunosilva-dev/atlas-search) — Spring Boot 3 + 多级缓存参考实现
