# knowledge_mining 工业级演进规划

> 日期：2026-06-01
> 基于 2025-2026 年工业界最新研究

## 1. 核心发现

### 1.1 Disco-RAG：话语感知的检索增强生成

**论文**：arXiv 2601.04377 — "Disco-RAG: Discourse-Aware Retrieval-Augmented Generation"

关键创新：
- **Intra-chunk RST tree**：在每个 chunk 内构建 RST 树，捕获 EDU（基本话语单元）间关系
- **Inter-chunk Rhetorical Graph**：跨 chunk 构建有向修辞图，编码 chunk 间关系
- **Discourse-Aware Planning**：基于 RST 树和修辞图生成写作蓝图，指导 LLM 生成

**对我们的启示**：
- 我们的 RST 关系提取（15种）已经覆盖了 Disco-RAG 的核心需求
- heading 独立为 segment 的变更（v1.1）正好支持 intra-chunk RST 的层级构建
- **下一步可考虑**：将 RST 关系信息传递给 serving 端，用于 ContextPack 的证据角色分类

### 1.2 DISRetrieval：话语感知的分层检索

**论文**：arXiv 2506.06313 — "DISRetrieval"

核心创新：
- 句子级 RST 解析（替代 EDU 级，提升效率）
- LLM 增强 RST 树内部节点表示
- 结构引导的分层检索（dual-selection strategy）

**对我们的启示**：
- 当前 segment 阶段已做 heading 独立 + 段落分组合并
- 可参考 LLM 增强 RST 节点的思路，为 retrieval unit 添加话语角色元数据

### 1.3 大规模 Chunking 策略评估

**论文**：arXiv 2603.06976 — 36 种 chunking 策略跨 6 领域评估

关键结论：
- **Paragraph Group Chunking (PGC)** 是最优通用策略（nDCG@5 = 0.459）
- 固定大小分块严重降低性能（nDCG@5 < 0.244）
- 结构感知分块显著优于固定分块
- 无单一策略对所有领域最优

**对我们的验证**：
- ✅ 我们已使用结构感知分块（SectionNode 树 + 段落分组）
- ✅ 小段合并策略（<100 tokens 合并）
- ✅ 大段拆分策略（>512 tokens 拆分）
- ✅ 表格/列表保持独立
- **结论**：我们的分块策略已与工业界最佳实践对齐

### 1.4 TopoChunker：拓扑感知分块

**论文**：arXiv 2603.18409

关键创新：
- 拓扑中间表示（SIR）：将文档映射为层次化结构
- 双 Agent 架构：Inspector Agent 路由 + Refiner Agent 语义审计
- 上下文消歧：解决指代消解（dangling pronouns）、术语孤立

**对我们的启示**：
- 我们的 `section_path` 已提供层级信息
- **可借鉴**：Refiner Agent 的上下文消歧思路，在 enrich 阶段解决跨 section 的指代问题

## 2. 演进路线图

### Phase 1: 话语角色元数据（中等优先级）

**参考**：Disco-RAG, DISRetrieval

目标：为每个 segment/retrieval unit 添加话语角色信息

实现方案：
1. 在 enrich 阶段增加 `discourse_role` 字段：
   - `nucleus` — 核心信息
   - `satellite` — 辅助信息（背景、详述、条件等）
2. 基于已有 RST 关系推断角色：
   - elaborates → source 是 nucleus, target 是 satellite
   - causes → source 是 nucleus, target 是 satellite
3. 将话语角色写入 retrieval unit 的 `metadata_json`
4. serving 端可利用话语角色改善 ContextPack 组装

**预估工作量**：3-5 天

### Phase 2: 指代消解增强（中等优先级）

**参考**：TopoChunker 的 Refiner Agent

目标：解决跨 segment 的指代消解问题

实现方案：
1. 在 enrich 阶段添加指代消解步骤：
   - 检测代词（这/该/其/它/他/她）
   - 检测孤立术语（领域特定缩写无定义）
   - 利用 section_path 向上查找祖先节点的实体定义
2. 将消解结果作为 `context_supplement` 写入 metadata
3. retrieval unit 的 `search_text` 包含消解后的完整上下文

**预估工作量**：5-7 天

### Phase 3: 自适应 Chunking 参数（低优先级）

**参考**：36 种 chunking 策略评估

目标：根据文档类型动态调整分块参数

实现方案：
1. 在 parse 阶段检测文档类型特征：
   - 技术文档 → 保留结构、加大 token 阈值（768）
   - 叙述文档 → 使用语义分块（512）
   - 参数表格 → 行级分块（table_row 已实现）
2. Domain Profile 支持按文档类型配置分块参数

**预估工作量**：3-5 天

### Phase 4: 并发 Run 支持（高优先级）

当前限制：全局互斥锁，一次只能运行一个 Run

实现方案：
1. 移除全局互斥锁
2. 使用 PostgreSQL Advisory Lock 替代：
   ```python
   # 按 domain 加锁，不同 domain 可并行
   SELECT pg_advisory_lock(hashtext('mining_run:' || domain_id));
   ```
3. 同 domain 内保持串行（避免 DB 竞争）
4. 不同 domain 可并行运行

**预估工作量**：2-3 天

### Phase 5: Pipeline 进度持久化（中等优先级）

当前限制：进度信息仅存内存

实现方案：
1. 利用已有的 `mining_run_stage_events` 表
2. 增加 stage 级别的进度百分比字段
3. 前端通过 SSE 实时显示进度

**预估工作量**：2-3 天

## 3. 不建议做的事

1. **不要替换 RST 为 EDU 级解析** — EDU 粒度太细，计算成本高，sentence 级已足够
2. **不要使用 LLM 做 chunking** — 论文显示 LLM-assisted chunking 成本极高（133s vs 6s），收益有限
3. **不要追求 36 种分块策略** — 我们的 PGC 式策略已接近最优

## 4. 参考资源

- [Disco-RAG](https://arxiv.org/html/2601.04377v1) — 话语感知 RAG
- [DISRetrieval](https://arxiv.org/pdf/2506.06313) — RST 分层检索
- [36 种 Chunking 评估](https://arxiv.org/pdf/2603.06976) — 分块策略对比
- [TopoChunker](https://arxiv.org/pdf/2603.18409) — 拓扑感知分块
