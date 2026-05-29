# Pipeline 算法详细文档

> 日期：2026-05-29（修订）
> 用途：功能核查点 + 算法审视
> 范围：Mining Pipeline（挖掘） + Serving Pipeline（检索）全链路算法细节

---

## 一、Mining Pipeline（挖掘服务）

### 1.1 Pipeline 架构

**两种执行模式**：

| 模式 | 类 | 特点 | 使用场景 |
|------|------|------|----------|
| 顺序模式 | `MiningPipeline` | 单文档顺序执行所有 stage | 单文档处理、调试 |
| 流式模式 | `StreamingPipeline` | 每个 stage 独立线程，队列串联 | 批量文档并发处理 |

**核心数据结构**：`DocumentContext`（frozen dataclass）

- 每个文档的不可变状态，stage 间通过 `with_updates()` 创建新实例传递
- 字段：`raw_file` → `tree` → `segments` → `relations` → `retrieval_units` → `embeddings`

**StreamingPipeline 架构**：

```
Queue[0] → [parse×1] → Queue[1] → [segment×1] → Queue[2] → [enrich×N] → Queue[3]
→ [discourse×1] → Queue[4] → [retrieval_units×1] → Queue[5] → [embedding×N] → Queue[6]
→ [db_write×1] → Queue[7]
```

- 每个 stage 可配置并发数（parse=1, enrich=4, embedding=4, db_write=1）
- Worker 线程拉取→执行→推入下一队列
- `_SENTINEL` 对象通知 worker 关闭

### 1.2 Stage 1: Parse（文件解析）

**入口**：`create_parser(file_type)` 工厂函数

#### 1.2.1 MarkdownParser

- 使用 `markdown-it-py` 结构化解析
- 输出 `SectionNode` 树，每个节点含 `title`、`level`、`blocks`、`children`
- `ContentBlock` 类型：paragraph / table / list / code / blockquote / heading / html_table

#### 1.2.2 PlainTextParser

- 段落分块：双换行分割
- 滑动窗口：
  - 默认 300 tokens/块
  - 30 tokens 重叠
  - 使用 `token_count()` 统计（whitespace 分割近似）

#### 1.2.3 PdfParser

- 使用 `pdfminer.six` 的 `LTTextBox` layout API
- `parse_pdf_to_section_tree()` 将 PDF 页面映射为 SectionNode 树
- 按 Y 坐标识别标题层级

**中文标题识别**（4 种正则模式）：

| 模式 | 正则 | 映射层级 |
|------|------|----------|
| 中文章节 | `^第[一二三四五六七八九十百千零\d]+[章部篇]` | Level 1 |
| 中文小节 | `^第[一二三四五六七八九十百千零\d]+[节条款]` | Level 2 |
| 中文枚举 | `^[（(][一二三四五六七八九十\d]+[）)]\s*\S` | Level 3 |
| 中文顿号枚举 | `^[一二三四五六七八九十]+[、．.]\s*\S` | Level 3 |

**字号启发式标题检测**：

- 比较 block font_size 与 body_size
- font_size ≥ body_size + 0.1 且文本 ≤ 400 字符 → 候选标题
- `_font_size_to_level()` 按字号在所有不同字号中的排名映射层级（最大 → Level 1）

#### 1.2.4 DocxParser

- 使用 `python-docx` 读取 DOCX 文件
- 按 style.name 检测标题层级（"Heading 1"~"Heading 6"）
- 表格检测：`ContentBlock(block_type="table")`，转换为 pipe-delimited markdown
- 列表检测：段落 style 或 bullet 前缀
- 使用与 PdfParser 相同的 `_build_section_tree()` 模式构建 SectionNode 树
- 忽略 content 字符串，直接从 file_path 读取原始文件

#### 1.2.5 PassthroughParser

- 对不支持的文件类型，`tree = None`，后续 stage 全部跳过

#### 1.2.6 Ingestion 预处理（文件类型调度）

**ingestion 层在解析前根据扩展名预处理**：

| 扩展名 | 预处理 | 解析器 |
|--------|--------|--------|
| `.md` / `.markdown` | UTF-8 解码 | MarkdownParser |
| `.txt` | UTF-8 解码 | PlainTextParser |
| `.pdf` | `pdf_to_text()` 提取文本 | PdfParser（从 file_path 直接读取） |
| `.doc` / `.docx` | 无（二进制格式） | DocxParser（从 file_path 直接读取） |
| `.html` / `.htm` | `html_to_markdown()` 转为 markdown | MarkdownParser |
| `.chm` / `.hdx` | `archive_to_markdown()` 解压+转换 | MarkdownParser |

### 1.3 Stage 2: Segment（文档分段）

**入口**：`DefaultSegmenter.segment(tree, profile)`

#### 1.3.1 `_walk_sections()` 递归遍历

```
对每个 SectionNode:
  1. 维护 section_path（标题链 + 层级）
  2. 按块类型分组：
     - heading → 不产出独立 segment，作为后续 segment 的 section_title
     - table/list/code/blockquote → 独立 segment
     - paragraph → 累积到 current_group
  3. 递归处理子节点
```

#### 1.3.2 `_merge_small_segments()` 合并策略

**阈值常量**：

| 常量 | 值 | 含义 |
|------|------|------|
| `_MERGE_MAX_TOKENS` | 512 | 合并后最大 token 数 |
| `_TABLE_MIN_INDEPENDENT_TOKENS` | 300 | 表格保持独立的最小 token |
| `min_tokens` | 100 | 判断"小段"的阈值 |

**合并规则**（Unstructured.io CompositeElement 模式）：

1. **intro_merge**：短段落(<100 tokens) + 后续列表/表格 → 合并为复合 segment
   - 前提：`prev.block_type == "paragraph"` 且 `prev_tc < 100`
   - 目标：`seg.block_type in ("list", "table", "html_table")`
   - 限制：合并后 ≤ 512 tokens，表格 >300 tokens 不合并

2. **backward_merge**：短 segment 合入前一个
   - 目标：`seg_tc < 100` 且非 table/code
   - 前一个也非 table/code
   - 合并后 ≤ 512 tokens

3. **同 section 限制**：只合并 `section_path` 相同的 segment

#### 1.3.3 `_split_large_segments()` 拆分策略

**阈值**：`_SPLIT_MAX_TOKENS = 512`

**拆分流程**：

```
1. 遍历所有 segments
2. 如果 token_count ≤ 512 → 直接保留
3. 如果 token_count > 512：
   a. 按段落边界（\n\n）拆分
   b. 如果单段落仍超限，按句子边界（。！？\n）拆分（使用 split_sentences()）
   c. 每个 sub-segment 保持 section_path、section_title 等元数据不变
   d. token_count 重新计算
```

**block_type 优先级**（合并后取高优先级）：

| 类型 | 优先级 |
|------|--------|
| table / html_table | 4 |
| list | 3 |
| code | 2 |
| paragraph / blockquote | 1 |
| unknown | 0 |

#### 1.3.4 每个 Segment 的字段

```
document_key: 文档标识
segment_index: 段内序号（合并后重排）
block_type: paragraph/table/list/code/blockquote
semantic_role: 默认 "unknown"（enrich stage 赋值）
section_path: [{title, level}, ...] 层级链
section_title: 直接父节点的标题
raw_text: 原始文本
normalized_text: raw_text.lower().strip()
content_hash: SHA256 哈希
normalized_hash: 小写文本的 SHA256
token_count: whitespace 分割近似
structure_json: {col_count, row_count, language, items, ...}
source_offsets_json: {parser, block_index, line_start, line_end}
entity_refs_json: []（enrich stage 填充）
metadata_json: {}
```

### 1.4 Stage 3: Enrich（LLM 增强）

**入口**：`LlmEnricher.enrich_batch(segments)`

**3 阶段批量协议**：

```
Phase 1: submit_all
  - 每个 segment 提交 LLM 任务（template_key="mining-segment-understanding"）
  - caller_service="mining"
  - 返回 task_id → seg_key 映射

Phase 2: poll_all
  - 并发轮询所有 task_id 直到完成
  - 超时：120s

Phase 3: apply_results
  - 解析 LLM 输出：
    - entities: [{type, name, ...}]  → 写入 segment.entity_refs_json
    - semantic_role: "definition"|"procedure"|"example"|...
    - content_assessment: {is_substantive, is_navigation}
```

**Enrich stage 版本**：`stage_version = "2"`

**域白名单约束**：

- 只提取 `profile.entity_types` 中定义的实体类型
- 只分类为 `profile.semantic_roles` 中定义的语义角色

### 1.5 Stage 4: Discourse Relations（篇章关系）

**入口**：`DiscourseRelationBuilder.build(segments, seg_ids)`

#### 1.5.1 算法：LLM 滑动窗口 RST 分析

**配置参数**：

| 参数 | 默认值 | 来源 |
|------|--------|------|
| `window_size` | 15 | `profile.retrieval_policy.discourse_window_size` |
| `min_confidence` | 0.5 | `profile.retrieval_policy.min_confidence` |
| `max_distance` | 5 | `profile.retrieval_policy.max_distance` |

**流程**：

```
1. 过滤掉 heading 类型 segments
2. 滑动窗口 [start : start + window_size]，步长 window_size - 1（重叠 1）
3. 每个窗口：
   a. 格式化：[{idx}] ({section_title}) {text[:150]}
   b. 提交 LLM 任务（template_key="mining-discourse-relation"）
   c. caller_service="mining"
   d. LLM 返回 [{source, target, relation, confidence}]
   d. 映射 relation → DB relation_type
4. 过滤：白名单 + confidence ≥ min_confidence
```

#### 1.5.2 12 种 RST 关系类型

| LLM 输出 | DB relation_type | 含义 |
|----------|-----------------|------|
| ELABORATES | elaborates | 详述 |
| EVIDENCES | evidences | 证据 |
| CAUSES | causes | 因果 |
| RESULTS_IN | results_in | 导致 |
| BACKGROUNDS | backgrounds | 背景 |
| CONDITIONS | conditions | 条件 |
| SUMMARIZES | summarizes | 总结 |
| JUSTIFIES | justifies | 论证 |
| ENABLES | enables | 使能 |
| CONTRASTS_WITH | contrasts_with | 对比 |
| PARALLELS | parallels | 类比 |
| SEQUENCES | sequences | 时序 |

### 1.6 Stage 5: Retrieval Units（检索单元构建）

**入口**：`build_retrieval_units(segments, seg_ids, ..., profile)`

#### 1.6.1 四种检索单元类型

**类型 1：raw_text（主要证据层）**

- 1:1 与 segment 对应
- weight = 1.0
- `search_text` 组成（Anthropic Contextual Retrieval 模式）：
  1. section 标题链（不在 raw_text 中的额外标题）
  2. LLM 生成的上下文描述（`LLMContextualizer`）
  3. 原始 raw_text
  4. 全部经过 `tokenize_for_search()` 分词

**类型 2：entity_card（实体卡片）**

- 条件：`profile.retrieval_policy.entity_card != "off"`
- 只为 `profile.strong_entity_types` 中的强类型生成
- 跳过 navigation 类型 segment（`content_assessment.is_navigation == True`）
- 全局去重（`{entity_type}:{entity_name}` 唯一）
- 每个 segment 最多 `max_entity_cards_per_segment` 张卡片
- weight = 0.5
- 文本格式：`{entity_name}（{entity_type}） {上下文}`

**类型 3：table_row（表格行级检索）**

- 条件：`profile.retrieval_policy.table_row != "off"`
- 只对 `block_type == "table"` 的 segment 生成
- 每行一个检索单元
- 文本格式：`{col1}为{val1}，{col2}为{val2}。`
- weight = 0.8

**类型 4：generated_question（LLM 生成问题）**

- 条件：`question_generator != None` 且 segment 满足 `_is_questionworthy()`
- 批量协议：submit_all → poll_all（与 enrich 相同）
- 每个 segment 最多 `max_questions_per_segment` 个问题
- weight = 0.7
- 问题修剪：
  - 长度 < 5 字符 → 丢弃
  - 去除 `Qn:` 前缀
  - 去除后长度仍 < 5 → 丢弃

#### 1.6.2 `_is_questionworthy()` 判断门控

```
跳过条件（满足任一即跳过）：
- block_type == "heading"
- token_count < min_questionworthy_tokens（默认 10）
- raw_text.strip() 长度 < 15
- content_assessment.is_substantive == False
- semantic_role in not_questionworthy_roles
```

#### 1.6.3 `LLMContextualizer` 上下文增强

- 只处理 `block_type != "heading"` 且 `len(raw_text) > 15` 的 segment
- 提交 LLM 任务（template_key="mining-contextual-retrieval"）
- 输入：`{document: 文档预览(前2000字), segment: 段落预览(前500字)}`
- 输出：`{context: "简短上下文描述"}`
- 上下文折叠进 raw_text unit 的 `search_text`，不产生独立 unit

### 1.7 Stage 6: Embedding（向量生成）

**入口**：`embedding_stage(ctx, cfg)`

- 提取所有 retrieval unit 的 text
- 调用 `LLMServiceEmbeddingGenerator.embed_batch(texts)`
- 通过 llm_service 的 `POST /api/v1/models/embeddings` 端点（直接 HTTP 调用，不走 llm_client.py）
- 结果映射：`{unit_key: vector}`
- 可配置多 worker 并发

### 1.8 Stage 7: DB Write（数据库写入）

**入口**：`db_write_stage(ctx, cfg)`（单线程，保证 DB 线程安全）

**子步骤**：

```
1. select_or_create_snapshot: 查找或创建 document snapshot
2. UPDATE cleanup: 如果 action=="UPDATE"，清理旧 snapshot 数据
3. commit_segments: 逐个插入 raw_segments
4. build_relations: 插入 segment_relations
5. build_retrieval_units: 插入 retrieval_units
6. insert_embeddings: 插入 retrieval_embeddings（含向量）
7. commit_document: 更新 run_document 状态为 committed
```

### 1.9 Publishing（发布）

**入口**：`publishing.py`

#### 1.9.1 `classify_documents()` 文档分类

| 动作 | 条件 |
|------|------|
| NEW | 首次出现的文档 |
| UPDATE | 已有且内容变化 |
| SKIP | 已有且内容未变 |
| REMOVE | 上次有但本次没有 |

#### 1.9.2 `assemble_build()` 构建

- **全量模式**：本次 Run 的所有 snapshot 构成一个完整 build
- **增量模式**：继承 parent build 的 snapshot，加上本次新的

#### 1.9.3 `publish_release()` 发布

- 验证 build 完整性
- 创建 release，激活为当前 active release
- 支持按 channel 范围发布

---

## 二、Serving Pipeline（检索服务）

### 2.1 Pipeline 总览

```
SearchRequest → SearchService.search()
  1. Resolve Domain（解析域 → 设置 LlmClient.knowledgeDomain → 验证 DB → DomainContext.set）
  2. Load Domain Profile
  3. Query Understanding + Embedding（并行）
  4. Retrieval Router
  5. Resolve Scope（active release → build → snapshots）
  6. Collect Query Embedding
  7. Retrieve（多路并行）
  8. Fuse（多路融合）
  9. Rerank（级联重排）
  10. Assemble ContextPack
  11. Build Debug Info（可选）
```

**并发模型**：`Executors.newVirtualThreadPerTaskExecutor()`（Java 21 虚拟线程）

> **设计要点**：domain 解析在所有并行任务启动前完成，确保 query understanding、embedding、rerank 的 LLM 调用都携带正确的 `knowledge_domain`（用于 llm_service 审计和计费）。

### 2.2 Stage 1: Resolve Domain

- 从 `request.domain()` 解析 `effectiveDomain`，fallback 到 `defaultDomain`
- 调用 `llmClient.setKnowledgeDomain(effectiveDomain)` — **必须在并行任务之前**
- 通过 `domainPoolManager.getDataSource(effectiveDomain)` 验证 DB 可达
- 设置 `DomainContext.set(effectiveDomain)` 供后续 DB 操作路由

### 2.3 Stage 2: Load Domain Profile

- `DomainPackReader.getProfile(domain)` 从 YAML 加载领域配置
- 包含：ontology、semantic_roles、retrieval_policy、entity_types、extractor_rules

### 2.4 Stage 3: Query Understanding

**入口**：`QueryUnderstandingEngine.understand(query, profile)`

#### 2.3.1 双路径架构

```
LLM 路径（优先）：
  → llmClient.execute("query_understanding", "serving-query-understanding", {query})
  → 解析 LLM 结构化输出
  → 失败则 fallback 到规则路径

规则路径（fallback）：
  → 实体提取 + 意图检测 + 关键词提取 + 范围提取
```

#### 2.3.2 7 种意图类型

| 意图 | 检测关键词 | 对应证据角色 |
|------|-----------|-------------|
| `command_usage` | 命令/用法/参数/格式/语法 | parameter, example, procedure_step |
| `troubleshooting` | 故障/排查/告警/错误/异常 | troubleshooting_step, alarm, constraint |
| `concept_lookup` | 是什么/什么是/概念/原理 | concept, note |
| `procedure` | 步骤/流程/操作 | procedure_step, parameter, example |
| `comparison` | 区别/差异/对比/比较 | concept, parameter |
| `navigational` | 在哪里/如何找到/路径 | （空） |
| `general` | 默认 | （空） |

#### 2.3.3 实体提取

**三层提取**：

1. **Domain Pack 规则**：`profile.extractor_rules` 中的正则模式
2. **命令正则**：`(ADD|MOD|DEL|SET|SHOW|LST|DSP|REG|DEREG)\s+([A-Z][A-Z0-9_]*)`
3. **中文操作词映射**：`{新增→ADD, 修改→MOD, 删除→DEL, 查询→SHOW, ...}`

**范围提取**：

- products：默认 `["UDG", "UNC", "CloudCore"]`（Domain Pack 可覆盖）
- network_elements：默认 `["AMF", "SMF", "UPF", "UDM", "PCF", "NRF", ...]`
- 使用 word boundary 正则匹配，大小写不敏感

**关键词提取**：

- jieba 分词
- 过滤停用词（中英文各 ~30 个）
- 过滤 <2 字符的非 CJK token

**LLM 路径额外能力**（规则路径不具备）：

- SubQuery 分解：复杂查询拆分为子查询
- 模糊性检测：识别歧义查询
- EvidenceNeed 分类：`preferred_roles` + `preferred_blocks`

### 2.5 Stage 4: Retrieval Router

**入口**：`RetrievalRouter.route(understanding, profile)`

- 根据 Domain Profile 的 `retrieval_policy` 确定启用的路由
- 输出 `RetrievalRoutePlan`：routes + fusion config + rerank config + assembly config

### 2.6 Stage 5: Resolve Scope

**入口**：`assetRepository.resolveActiveScope(domain, channel)`

```
active_release → build_id → snapshot_ids
```

### 2.7 Stage 6: Collect Query Embedding

- 与 Query Understanding 并行启动
- 只在 dense_vector 路由启用时使用
- 失败则跳过向量检索（graceful degradation）

### 2.8 Stage 7: Retrieve（多路检索）

**入口**：`RetrievalOrchestrator.execute(understanding, routePlan, queryEmbedding, snapshotIds)`

**并发模型**：

- 有 Executor → `CompletableFuture.allOf()` 并行执行所有路由
- 无 Executor → 顺序执行（测试模式）
- 每个路由独立的 `DomainContext` 传播

**异常隔离**：单个路由失败不影响其他路由

#### 2.8.1 FtsRetriever（全文检索）

**三级降级策略**：

| 级别 | 方法 | 算法 | 条件 |
|------|------|------|------|
| Level 1 | `tryTsvector` | PostgreSQL `websearch_to_tsquery` | 默认 |
| Level 2 | `tryTrigram` | PostgreSQL `pg_trgm` 三元组相似度 | Level 1 无结果 |
| Level 3 | `tryLike` | `LIKE '%token%'` + 关键词命中率评分 | Level 2 无结果 |

**分词**：

- jieba 分词（与 Mining 端一致）
- 过滤停用词（中英文）
- 过滤 <2 字符非 CJK token
- 组装为 `token1 OR token2 OR token3` 的 tsquery

**Scope 过滤下推**：

- 将 scope（products/network_elements）转为 JSONB 参数
- 使用 `facets_json @> '{"key":["value"]}'` 下推到 SQL
- 无结果时自动去掉 scope 重试

**recall 扩大**：`recallLimit = topK * 5`

#### 2.8.2 DenseVectorRetriever（向量检索）

- 使用 pgvector `<=>` cosine distance 操作符
- 服务端 ANN（避免加载所有向量到 JVM）
- Scope 过滤同样下推到 SQL（facets_json JSONB containment）
- 无结果时去掉 scope 重试

#### 2.8.3 EntityExactRetriever（实体精确匹配）

- 从 `query.entities()` 提取 entity name
- Fallback：keywords 中长度 ≥2 的词作为类实体词
- 使用 JSONB `@>` containment 查询 `entity_refs_json`（GIN 索引）
- 固定分数 = **0.95**（高置信度）

#### 2.8.4 GraphExpander（图扩展）

**算法：BFS 图遍历**

```
输入：seed segment IDs + maxDepth + relationTypes + maxResults
初始化：visited = seeds, frontier = seeds

for depth = 1 to maxDepth:
  neighbors = SQL 查询 frontier 的所有关联 segment
  for each neighbor not in visited:
    visited.add(neighbor)
    nextFrontier.add(neighbor)
    if expandedIds.size >= maxResults: 返回结果
  frontier = nextFrontier

结果：查询 expanded segments 的完整数据
```

- 每个 segment 记录 expansion distance 和 root seed ID
- 支持 relationTypes 过滤
- 支持 snapshotIds 范围限制

### 2.9 Stage 8: Fuse（多路融合）

**三种融合策略**：

#### 2.9.1 RRF（标准 Reciprocal Rank Fusion）

```
score(uid) = Σ(1 / (k + rank_i))
```

- 默认 k = 60
- 每个 source 内按原始 score 降序排列
- 按 RRF score 降序输出

#### 2.9.2 Weighted RRF

```
score(uid) = Σ(weight_j / (k + rank_j))
```

- weight 来自 `RouteConfig.weight()`
- 额外记录 `routeSources`（贡献来源路由列表）到 `ScoreChain`
- 更新 `fusionScore` 到 `ScoreChain`

#### 2.9.3 Identity（直通）

- 不做融合，直接透传原始候选列表

### 2.10 Stage 9: Rerank（级联重排）

**入口**：`RerankPipeline.rerank(candidates, routePlan, understanding)`

**级联策略**：

```
1. Model Reranker（ZhipuModelReranker / LlmServiceReranker）
   → 成功则使用其结果
   → 失败则继续

2. LLM Reranker（LlmReranker）
   → 仅在 routePlan.rerank.method 为 "llm" 或 "cascade" 时尝试
   → 失败则继续

3. Score Reranker（ScoreReranker，兜底）
   → 永远成功
   → 按 score 降序排列
```

**统一后处理**：

1. 标注 `rerankScore` 到 `ScoreChain`
2. 最低分阈值过滤：`score < 0.01` → 移除
3. 截断到 `routePlan.assembly.maxItems`（默认 10）

**Reranker 实现明细**：

| Reranker | 实现类 | 算法 | 状态 |
|----------|--------|------|------|
| Model Reranker | `ZhipuModelReranker` | 智谱 rerank API | ✅ |
| LLM Service Reranker | `LlmServiceReranker` | 通过 llm_service 调用 | ✅ |
| LLM Direct Reranker | `LlmReranker` | 直接调用 LLM | ✅ |
| Score Reranker | `ScoreReranker` | 按 score 降序 | ✅ |

### 2.11 Stage 10: Assemble ContextPack（结果组装）

**入口**：`ContextAssembler.assemble(query, understanding, scope, candidates, routePlan)`

**组装流程**：

```
1. Build seed items → 从 retrieval candidates 构建 ContextItem
   - kind = "retrieval_unit", role = "seed"
   - 包含 score, title, block_type, semantic_role, citation

2. Resolve source segments → 从 source_refs_json 解析 segment IDs
   - 查询完整的 raw_segment 数据
   - kind = "raw_segment", role = "context"

3. Graph expansion → BFS 扩展关联 segment
   - 条件：routePlan.assembly.relationExpansion == true
   - maxDepth, maxResults 由配置控制
   - kind = "raw_segment", role = "support"

4. Fetch direct relations → 查询 segment 间的直接关系

5. Deduplicate → 去重 relations（只保留两端都在 items 中的关系）

6. Build source refs → 查询文档来源信息（document_key, title, path）

7. Build issues → 检索质量诊断
   - no_result：无结果
   - low_confidence：最高 score < 0.1

8. Evidence role classification → 对 seed items 分类证据角色
   - 使用 EvidenceRoleClassifier

9. Truncate → maxItems + maxExpanded 总数限制

10. Build evidence groups → 按 document_snapshot_id 分组
    - 每组包含 items 和相关 relations

11. Build suggestions → 基于 issues 生成建议
```

**RST → Evidence Role 映射**（图扩展时使用）：

| RST 类型 | Evidence Role |
|----------|--------------|
| elaborates, conditions, causes, results_in, enables | support |
| backgrounds | background |
| parallels | context |
| contrasts_with | contrast |
| previous, next, same_section, same_parent_section | context |
| section_header_of | context |

### 2.12 ContextPack 输出结构

```json
{
  "query": {
    "original": "SMF 配置 session 管理",
    "understanding": "intent=command_usage type=command name=SMF ...",
    "intent": "command_usage",
    "entities": [{"type": "network_element", "name": "SMF"}],
    "scope": {"products": [], "network_elements": ["SMF"]},
    "release_id": "...",
    "build_id": "...",
    "snapshot_count": 3
  },
  "items": [
    {
      "id": "ru-uuid",
      "kind": "retrieval_unit",
      "role": "seed",
      "text": "...",
      "score": 0.85,
      "title": "...",
      "block_type": "paragraph",
      "semantic_role": "procedure",
      "evidence_role": "direct_answer",
      "citation": {"section": "...", "document_snapshot_id": "..."}
    }
  ],
  "relations": [
    {"id": "rel-...", "from_id": "...", "to_id": "...", "type": "expansion", "distance": 1}
  ],
  "sources": [
    {"id": "...", "document_key": "...", "title": "...", "relative_path": "..."}
  ],
  "evidence_groups": [
    {"snapshot_id": "...", "item_ids": ["..."], "relation_ids": ["..."]}
  ],
  "issues": [],
  "suggestions": [],
  "debug_info": {}
}
```

---

## 三、Domain Pack 配置体系

### 3.1 Domain Profile 结构

```yaml
domain_id: cloud_core_network

# 实体类型定义
entity_types:
  - name: command
    strong: true          # 用于 entity_card 生成
  - name: protocol
    strong: true
  - name: network_element
    strong: true
  - name: parameter
    strong: true
  - name: concept
    strong: false

# 语义角色定义
semantic_roles:
  - definition
  - procedure
  - example
  - troubleshooting_step
  - concept
  - note
  - parameter

# 检索策略
retrieval_policy:
  # 通道开关
  entity_card: on
  table_row: on
  contextual_retrieval: on

  # 数量控制
  max_questions_per_segment: 2
  max_entity_cards_per_segment: 3
  min_questionworthy_tokens: 10
  not_questionworthy_roles: [navigation]

  # RST 篇章分析参数
  discourse_window_size: 15
  min_confidence: 0.5
  max_distance: 5

# 提取器规则（Serving 端使用）
extractor_rules:
  - pattern: "(?i)\\b(SMF|AMF|UPF)\\b"
    entity_type: network_element
  - pattern: "(?i)\\b(ADD|MOD|DEL)\\s+[A-Z_]+"
    entity_type: command

# 查询理解配置
query_understanding:
  command_regex: "(ADD|MOD|DEL|SET|SHOW|LST|DSP|REG|DEREG)\\s+([A-Z][A-Z0-9_]*)"
  op_map:
    新增: ADD
    修改: MOD
    删除: DEL
  network_elements: [AMF, SMF, UPF, UDM, PCF, NRF]
  products: [UDG, UNC, CloudCore]
```

### 3.2 RetrievalRoutePlan 结构

```yaml
routes:
  - name: fts
    enabled: true
    topK: 20
    weight: 1.0
  - name: dense_vector
    enabled: true
    topK: 20
    weight: 1.2
  - name: entity_exact
    enabled: true
    topK: 10
    weight: 1.5
  - name: graph_expand
    enabled: true
    topK: 10
    weight: 0.8

fusion:
  method: weighted_rrf    # rrf | weighted_rrf | identity
  k: 60

rerank:
  method: cascade          # model | llm | cascade | score

assembly:
  maxItems: 10
  maxExpanded: 5
  relationExpansion: true
  maxRelationDepth: 2
  relationTypes: [elaborates, conditions, same_section]
```

---

## 四、关键算法总结

### 4.1 Mining 侧

| 算法 | 位置 | 核心思路 |
|------|------|----------|
| PDF 中文标题识别 | `pdf_parser.py` | 4 种中文编号正则 + 字号启发式 |
| DOCX 结构解析 | `docx_parser.py` | python-docx Heading 样式/表格/列表检测 |
| HTML 预处理 | `preprocessing.py` | html_to_markdown() 复用渲染器 |
| 文档分段合并 | `segment.py` | Unstructured CompositeElement 模式，<100 token 小段合并 |
| 大段拆分 | `segment.py` | >512 token 在段落/句子边界拆分，CJK 感知 |
| LLM 实体提取 | `enrich/__init__.py` | Domain Pack 白名单约束，3阶段批量提交（template: mining-segment-understanding） |
| RST 篇章分析 | `relations/__init__.py` | 滑动窗口 LLM，12 种关系类型（template: mining-discourse-relation） |
| 检索单元生成 | `retrieval_units/__init__.py` | 4 种类型：raw_text/entity_card/table_row/gen_question |
| 上下文增强 | `retrieval_units/__init__.py` | Anthropic Contextual Retrieval，LLM 生成上下文描述（template: mining-contextual-retrieval） |
| 问题生成 | `retrieval_units/__init__.py` | LLM 批量生成 + 结构校验修剪（template: mining-question-gen） |

### 4.2 Serving 侧

| 算法 | 位置 | 核心思路 |
|------|------|----------|
| 查询理解 | `QueryUnderstandingEngine.java` | LLM 优先 + 规则 fallback，7 种意图 |
| 全文检索 | `FtsRetriever.java` | 三级降级：tsvector → trigram → LIKE |
| 向量检索 | `DenseVectorRetriever.java` | pgvector cosine distance，服务端 ANN |
| 实体检索 | `EntityExactRetriever.java` | JSONB @> containment，固定高分 0.95 |
| 图扩展 | `GraphExpander.java` | BFS 遍历 segment relation 图 |
| RRF 融合 | `RRFFusion.java` / `WeightedRRFFusion.java` | 标准或加权 RRF，k=60 |
| 级联重排 | `RerankPipeline.java` | Model → LLM → Score 三级级联 |
| 证据分类 | `EvidenceRoleClassifier.java` | direct_answer / support / contrast / background |
| 结果组装 | `ContextAssembler.java` | seed → source → expansion → dedup → pack |
