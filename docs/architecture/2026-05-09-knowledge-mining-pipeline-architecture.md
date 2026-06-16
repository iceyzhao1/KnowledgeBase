# knowledge_mining_zym Pipeline 架构设计文档

> 基于当前代码（`zym/mining-stage-events-2026-05-08` 分支，2026-05-09 快照）梳理。
> 入口：[run.py:91](../../knowledge_mining_zym/mining/jobs/run.py) `run()`
> 编排：[pipeline.py](../../knowledge_mining_zym/mining/pipeline.py) `StreamingPipeline`

---

## 0. 总体架构

### 0.1 流水线分层

```
ingest_directory  →  StreamingPipeline (per-document, 并行)
                      ├─ parse        (1 worker)
                      ├─ segment      (1 worker)
                      ├─ enrich       (max_workers)
                      ├─ relations    (1 worker)
                      ├─ discourse    (min(max_workers,2))
                      └─ retrieval_units (max_workers)
                  ↓
                  主线程串行写库（segment_persist / relations_persist / retrieval_units_persist）
                  ↓
                  embedding（旁路）
                  ↓
                  全局阶段：select_snapshot → assemble_build → validate_build → publish_release
```

并行结构由 `pipeline.py` 中 `StreamingPipeline` 实现（每阶段独立线程池 + Queue）。

### 0.2 算子可插拔

`PipelineConfig` 暴露 7 个槽位，运行时由 `_init_llm` 决定填规则版还是 LLM 版：

| 槽位 | 规则实现 | LLM 实现 |
|---|---|---|
| parser_factory | `create_parser` (固定) | — |
| segmenter | `DefaultSegmenter` | `LlmSegmenter` |
| enricher | `RuleBasedEnricher` | `LlmEnricher`（失败回退规则） |
| relation_builder | `DefaultRelationBuilder` (固定) | — |
| discourse_relation_builder | — | `DiscourseRelationBuilder` |
| question_generator | `NoOpQuestionGenerator` | `LlmQuestionGenerator` |
| contextualizer | `NoOpContextualizer` | `LLMContextualizer` |
| embedding_generator | `NoOpEmbeddingGenerator` | `LLMServiceEmbeddingGenerator` / `ZhipuEmbeddingGenerator` |

LLM 服务不可达时（`client.health_check()` 失败）自动全部降级为规则版。

### 0.3 运行时追踪

每个阶段进出由 `RuntimeTracker.start_stage / end_stage` 写入 `mining_run_stage_events` 表，UI 实时订阅。`mining_runs.metadata_json.operators` 记录本次运行实际激活的算子类名（`_operator_info_map`），用于在 UI 显示 LLM/规则徽章。

---

## 1. Stage 0 — Ingest

`knowledge_mining_zym/mining/ingestion/__init__.py` `ingest_directory`

### 功能
递归扫描输入目录，识别文件类型，预处理 `.chm/.hdx` 为 Markdown，产出 `RawFileData`。

### 规则
- 文件类型映射 `_EXTENSION_MAP`：`.md/.markdown→markdown`、`.txt→txt`、`.html/.htm→html`、`.pdf→pdf`、`.doc/.docx→doc(x)`、`.chm/.hdx→markdown`（先调 `archive_to_markdown` 转换）
- 跳过 `manifest.jsonl`、`thumbs.db` 等文件名
- 仅 `.md/.markdown/.txt` 进入解析；其余只做登记
- 双 hash：`raw_content_hash = sha256(bytes)`、`normalized_content_hash = sha256(normalize(content))`，用于变更检测
- title 推断：Markdown 取首个 `# ` 行，其余用文件名

### 数据库写入
此阶段不直接写库，但 `RawFileData` 后续驱动以下表的写入：
- `asset_documents` — 文档稳定身份（`document_key=doc:/<rel_path>`）
- `asset_document_snapshots` — 内容快照（按 `normalized_content_hash` 去重）
- `asset_document_snapshot_links` — 文档↔快照的多对一关系

### LLM
无。

---

## 2. Stage 1 — Parse

`knowledge_mining_zym/mining/stages/parse.py`

### 功能
将原始内容解析为 `SectionNode` 树（标题层级 + `ContentBlock` 列表）。

### 规则
工厂 `create_parser(file_type)`：
- **MarkdownParser**：调用 `infra/structure` `parse_structure`，基于 `markdown-it-py` token 流构建 SectionNode 树；保留 `block_type ∈ {paragraph, heading, table, list, code, blockquote, html_table, raw_html}`，table/list/code 在 `ContentBlock.structure` 中带列/行/语言
- **PlainTextParser**：按空行切段（`_split_paragraphs`）；超长段用 `_find_token_boundaries`（中文逐字符 + 英文 alnum 单词）+ `chunk_size=300, chunk_overlap=30` 滑窗切片
- **PassthroughParser**：非可解析类型（pdf/doc 等）返回 `None`，文档进入 SKIP

### 数据库写入
不直接写库。SectionNode 仅在内存中传递。

### LLM
无。

---

## 3. Stage 2 — Segment

`knowledge_mining_zym/mining/stages/segment.py`

### 功能
把 `SectionNode` 树展开成有序 `RawSegmentData` 列表，每个 segment 是检索的最小原始证据单元。

### 规则（结构性切分，恒定）
`_walk_sections`：
- DFS 遍历 SectionNode 树，维护 `section_path` 栈
- heading → 独立 segment（`block_type=heading`），用于后续 `section_header_of` 关系
- table / html_table / code / list / blockquote → 各自独立 segment
- 连续 paragraph blocks 攒成一个 run，由 `paragraph_grouper` 决定边界
- `_make_segment` 计算 `content_hash` / `normalized_hash` / `token_count`，结构化字段（columns/rows/language）落进 `structure_json`，行号落进 `source_offsets_json`

### 段内边界两种实现
- **`DefaultSegmenter`** (stage_version=1)：`_default_paragraph_grouper` 把整个 paragraph run 合并为单段
- **`LlmSegmenter`** (stage_version=2)：调用 LLM 决定段落边界

### 数据库字段（`asset_raw_segments`）
| 字段 | 类型 | 来源 |
|---|---|---|
| id | TEXT PK | uuid |
| document_snapshot_id | TEXT FK | 由 select_snapshot 注入 |
| segment_key | TEXT | `{document_key}#{segment_index}` |
| segment_index | INT ≥0 | 顺序 |
| section_path | JSONB | `[{level, title}, ...]` |
| section_title | TEXT | 当前节点标题 |
| block_type | TEXT CHECK | paragraph/heading/table/list/code/blockquote/html_table/raw_html/unknown |
| semantic_role | TEXT CHECK | 默认 `unknown`，enrich 阶段写入 |
| raw_text / normalized_text | TEXT | 原文 / 小写strip |
| content_hash / normalized_hash | TEXT | sha256 |
| token_count | INT | text_utils.token_count |
| structure_json | JSONB | columns/rows/language/items |
| source_offsets_json | JSONB | parser, block_index, line_start, line_end |
| entity_refs_json | JSONB | enrich 阶段填充 |
| metadata_json | JSONB | enrich 阶段填充 |
| UNIQUE | (document_snapshot_id, segment_key) | |

### LLM 调用（仅 `LlmSegmenter`）
- 模板：`mining-segment-boundary` (template_version=1)
- 输入：`{section_title, paragraphs: [{index, preview(<=240字符)}]}`
- 期望输出：`{"groups": [[start, end], ...]}`
- 校验：`_safe_group` + `_validate_groups` 强制 groups 完整覆盖 `[0, n)`、无重叠、无空隙；任何异常 / paragraph 数 < `min_paragraph_count_for_llm=2` → 回退默认单组

---

## 4. Stage 3 — Enrich

`knowledge_mining_zym/mining/stages/enrich/__init__.py`

### 功能
为 segment 写入实体引用、语义角色、heading 角色、表格元数据、内容质量评估。**这是 segment 字段补全的唯一阶段**。

### 4.1 RuleBasedEnricher（v1，规则）

**(1) 实体抽取** `infra/extractors.py` `RuleBasedEntityExtractor`
- 从 `DomainProfile.extractor_rules`（domain YAML 配置）加载正则规则
- cloud_core_network pack 示例规则：
  - `network_function`：`\b(SMF|UPF|AMF|PCF|UDM|UDR|AUSF|NRF|NSSF|BSF|CHF|SMSF|LMF|GMLC|NEF|SCF)\b` → `network_element`
  - `command`：`\b(ADD|SHOW|MOD|DEL|...)\s+([A-Z][A-Z0-9_]{1,20})` → `command`（双 group 拼接）
  - `interface`：`(?<![A-Za-z0-9_])(N[1-9]\d?|...|Sx[abc]|...)` → `interface`
  - `alarm`：`(?<![A-Za-z0-9_])(ALM-[A-Z][A-Z0-9_-]{2,40})` → `alarm`
- 表格结构化抽取：当 `structure_json.columns` 命中 `parameter_column_names`（如"参数标识/参数名称"）时，逐行抽取参数实体
- section_title 派生：`section_title_command_pattern` 命中时把命令名加为实体

**(2) 语义角色分类** `DefaultRoleClassifier`
- 基于 `profile.role_keyword_rules`，按 section_title 关键词命中映射到 `VALID_SEMANTIC_ROLES`（concept/parameter/example/note/procedure_step/troubleshooting_step/constraint/alarm/checklist/unknown）
- cloud_core_network 示例：`["参数","参数说明","参数标识"]→parameter`、`["操作步骤","流程","检查项"]→procedure_step`

**(3) heading 角色** `_classify_heading_role`
- 命中 `profile.heading_role_keywords` 时写入 `metadata_json.heading_role`，如 `parameter_definition`、`procedure_section`

**(4) 表格元数据**
- `metadata_json.table_column_count`、`table_has_parameter_column`

**(5) 内容评估（rule fallback）** `_rule_based_content_assessment`
- heading → `{is_substantive=False, is_navigation=False, reason="heading block"}`
- list 中 `[txt](#anchor)` 链接占比 ≥0.8 → `{is_navigation=True}`
- 其余 → `{is_substantive=True}`

### 4.2 LlmEnricher（v2）

**两阶段并发批处理**：
1. Phase 1：所有 segment 同时 `submit_task(template_key="mining-segment-understanding")`
2. Phase 2：`poll_all` 并发拉所有结果（whoever finishes first）
3. Phase 3：成功的应用 LLM 结果；任一失败的 segment 走 `fallback_enricher` (`RuleBasedEnricher`)

**LLM 模板** `mining-segment-understanding` (template_version=3)
- 输入：`{text, section_title, block_type}`
- 输出 schema 强约束：
  ```json
  {
    "entities": [{"type": "<enum from profile.entity_types>", "name": "str"}],
    "semantic_role": "<enum from VALID_SEMANTIC_ROLES>",
    "document_type": "<command|feature|...|other>",
    "content_assessment": {
      "is_substantive": "bool",
      "is_navigation": "bool",
      "assessment_reason": "str"
    }
  }
  ```
- entity type enum 由 `build_templates_from_profile` 动态注入
- `_apply_llm_result`：实体按 `allowed_entity_types` 过滤后并入 `entity_refs_json`（去重）；`semantic_role` 必须落在 `VALID_SEMANTIC_ROLES` 才覆盖；`content_assessment` 仅保留三个字段

### 数据库字段（写回 `asset_raw_segments`）
- `semantic_role` ← LLM 输出 / 规则分类
- `entity_refs_json` ← `[{type, name}, ...]`
- `metadata_json` ← `{heading_role, table_column_count, table_has_parameter_column, llm_document_type, content_assessment}`

### LLM 调用
- 当 `llm_base_url` 配置且服务可达时，`enricher` 槽位为 `LlmEnricher`；否则始终为 `RuleBasedEnricher`

---

## 5. Stage 4 — Build Relations（结构关系）

`knowledge_mining_zym/mining/stages/relations/__init__.py` `build_relations`

### 功能
从有序 segment 列表构造五类结构关系；同时为每个 segment 分配 UUID（`seg_ids`），后续阶段共享。

### 规则（纯规则）

**(1) previous / next**
- 相邻 segment 双向边，`distance=1`

**(2) same_section**
- 同 `section_path` 的 segments 两两建边
- v1.2 起按 `max_distance=5` 截断，避免 O(n²) 爆炸

**(3) section_header_of**
- 每个 path 内的 heading segment → 同 path 内非 heading 的内容 segment

**(4) same_parent_section**
- 共享父 section_path 且组内 >2 时，组内两两建边

**(5) references** (v1.5)
- 仅对 `metadata_json.content_assessment.is_navigation == True` 的 segment 处理
- 正则 `\[([^\]]+)\]\(#[^)]+\)` 抽链接文本
- `_find_heading_by_title` 优先精确匹配 heading，回退 substring 匹配
- `metadata_json={source: "toc_link", link_text: ...}`

### 数据库字段（`asset_raw_segment_relations`）
| 字段 | 说明 |
|---|---|
| id | uuid |
| document_snapshot_id | FK to asset_document_snapshots |
| source_segment_id / target_segment_id | FK to asset_raw_segments |
| relation_type | CHECK：`previous,next,same_section,same_parent_section,section_header_of,references,elaborates,condition,contrast,evidences,causes,results_in,backgrounds,conditions,summarizes,justifies,enables,contrasts_with,parallels,sequences,unrelated,other` |
| weight / confidence | REAL DEFAULT 1.0 |
| distance | INT |
| metadata_json | JSONB |
| UNIQUE | (source_segment_id, target_segment_id, relation_type) |

### LLM
无。

---

## 6. Stage 4b — Discourse Relations（仅 LLM）

`knowledge_mining_zym/mining/stages/relations/__init__.py` `DiscourseRelationBuilder`

### 功能
基于 RST（修辞结构理论）补充语篇级关系（elaborates/evidences/causes/...），与结构关系合并入同一张表。

### 规则
- 过滤掉所有 `block_type=heading` 的 segment
- 滑窗：`window_size=15`，步长 `window_size-1=14`（窗口间相邻有 1 个重叠以保证跨窗口连续性）
- 单窗口 <2 段时跳过

### LLM 调用
- 模板：`mining-discourse-relation` (template_version=1)
- 输入：`segments` 字符串，每行 `[i] (section_title) <raw_text 前150字>`
- 输出：`json_array`，每项 `{source: int, target: int, relation: str, confidence: float}`
- 13 类关系（大写）：ELABORATES, EVIDENCES, CAUSES, RESULTS_IN, BACKGROUNDS, CONDITIONS, SUMMARIZES, JUSTIFIES, ENABLES, CONTRASTS_WITH, PARALLELS, SEQUENCES, UNRELATED
- `_parse_llm_results`：丢弃 `UNRELATED`；越界 source/target 丢弃；`relation.lower()` 写入；`weight=confidence`、`metadata_json.source="discourse_llm"`
- 异常时返回 `[]`，不阻断管道

### 数据库
合并写入 `asset_raw_segment_relations`，与结构关系共表。

---

## 7. Stage 5 — Build Retrieval Units

`knowledge_mining_zym/mining/stages/retrieval_units/__init__.py` `build_retrieval_units`

### 功能
从 enriched segments 派生检索就绪单元，每个 segment 可产出 1~N 个单元，实现 v1.3 的 2-2.5x 检索密度。

### 规则（骨架）
对每个 segment 产生：

**(1) raw_text 单元（1:1，必产）**
- `unit_key = ru:{document_key}#{segment_index}:raw_text`
- `weight=1.0`
- `search_text` 拼接（Anthropic 上下文检索模式）：
  1. 不在 `raw_text` 中的 section_path 标题（` > ` join）
  2. LLM 生成的 context（如有）
  3. 原始 raw_text
  → `tokenize_for_search` 处理

**(2) entity_card（条件）**
- 仅当 entity 类型 ∈ `profile.strong_entity_types`（cloud_core_network: command/network_element/parameter/protocol/interface/alarm/feature）
- `content_assessment.is_navigation=True` 跳过
- 全文档去重（`seen_entity_cards` 集合）
- 截断 `max_entity_cards_per_segment=3`
- `weight=0.5`
- text 模板：`{name}（{type}） {ctx前后80字}`

**(3) table_row（条件）**
- 仅 `block_type=table` 且 `structure_json.columns/rows` 都非空
- 每行一个单元，`text="<列1>为<值1>，..."`，`title=行N: <前3列拼接>`
- `weight=0.8`

**(4) generated_question（条件）**
- `_is_questionworthy` 通用闸：非 heading、`token_count>=10`、`len(raw_text)>=15`、未被标 non-substantive
- 截断 `max_questions_per_segment=2`
- `_prune_invalid_questions` 结构校验：长度≥5；剥离 `Q1:` 前缀
- `weight=0.7`

### 数据库字段（`asset_retrieval_units`）
| 字段 | 说明 |
|---|---|
| id | uuid |
| document_snapshot_id | FK |
| unit_key | `ru:{document_key}#{idx}:<kind>` 或 `ru:entity:{type}:{name}` |
| unit_type | CHECK：`raw_text/contextual_text/summary/generated_question/entity_card/table_row/other` |
| target_type | CHECK：`raw_segment/section/document/entity/synthetic/other` |
| target_ref_json | 指向 segment 的引用 |
| title / text / search_text | TEXT |
| block_type / semantic_role | 继承 segment |
| facets_json | `{block_type, semantic_role, section_depth, entity_type}` |
| entity_refs_json | 继承 segment |
| source_refs_json | `{document_key, segment_index, raw_segment_ids, offsets}` |
| llm_result_refs_json | LLM 任务 ID 溯源 `{source, task_id, question_index}` |
| source_segment_id | FK to asset_raw_segments |
| weight | REAL（raw_text=1.0, table_row=0.8, gen_q=0.7, entity_card=0.5） |
| metadata_json | JSONB |
| search_vector | TSVECTOR (trigger 自动生成) |
| UNIQUE | (document_snapshot_id, unit_key) |

**FTS 触发器**：
```sql
search_vector := setweight(to_tsvector(title),'A')
              || setweight(to_tsvector(search_text),'B')
              || setweight(to_tsvector(text),'C');
```
另有 `gin_trgm_ops` 索引支持中文 trigram 模糊检索。

### LLM 调用

**(a) `LlmQuestionGenerator`** — 模板 `mining-question-gen` (v3)
- Phase 1: 对所有 questionworthy segment 同时 submit_task，输入 `{title, content}`
- Phase 2: `poll_all` 并发拉
- 输出 `[{question: str}, ...]`，schema 允许空数组（LLM 自行判断该段是否值得生成问题）
- prompt 显式要求：导航/锚点列表 → `[]`；稀疏表格 → `[]`；过短/模糊 → `[]`

**(b) `LLMContextualizer`** — 模板 `mining-contextual-retrieval` (v2)
- 仅对 `block_type≠heading 且 len(raw_text.strip())>15` 的 segment 调用（节省约 30%）
- 输入：`{document: 全文前 2000 字, segment: raw_text 前 500 字}`
- 输出：`{context: "20-40 字中文上下文标签"}`
- 结果折进 raw_text 单元的 `search_text`，并存入 `metadata_json.context_description`

---

## 8. Stage 5b — Embedding（旁路）

`knowledge_mining_zym/mining/infra/embedding.py`

### 功能
为已落库的 retrieval_unit 生成向量。在 `retrieval_units_persist` 之后，主线程内同步调用。

### 规则
- 仅对 `ru.text` 非空的单元生成
- 失败时只 warning，不阻断（"Embedding generation failed"）

### 实现选择优先级
1. `llm_base_url` 可用 → `LLMServiceEmbeddingGenerator`（走 llm_service 的共享 embedding 端点）
2. `EMBEDDING_API_KEY` 配置 → `ZhipuEmbeddingGenerator`（直连智谱 `embedding-3`，dim=1024）
3. 都没有 → `NoOpEmbeddingGenerator`（返回 `[]`）

### 数据库字段（`asset_retrieval_embeddings`）
| 字段 | 说明 |
|---|---|
| id | uuid |
| retrieval_unit_id | FK |
| embedding_model | 如 `embedding-3` |
| embedding_provider | 如 `zhipu` |
| text_kind | 如 `full` |
| embedding_dim | 1024 |
| embedding_vector | TEXT (JSON 字符串) |
| embedding_vector_vec | `vector(1024)` (pgvector，trigger 自动从 JSON 转) |
| content_hash | TEXT |
| metadata_json | JSONB |

**索引**：HNSW + `vector_cosine_ops` 支持原生向量检索。

---

## 9. Stage 6 — Select / Create Snapshot

`knowledge_mining_zym/mining/snapshot/__init__.py` `select_or_create_snapshot`

### 功能
对每个文档：
- 按 `normalized_content_hash` 查 `asset_document_snapshots`，命中复用 snapshot_id
- 否则新建 `asset_document_snapshots` + `asset_documents`（首次） + `asset_document_snapshot_links`

### 数据库字段
- `asset_documents`：document_key UNIQUE
- `asset_document_snapshots`：normalized_content_hash UNIQUE，mime_type CHECK
- `asset_document_snapshot_links`：N:1 link with batch / scope / tags

### LLM
无。

---

## 10. Stage 7 — Assemble Build

`knowledge_mining_zym/mining/stages/publishing.py` `classify_documents` + `assemble_build`

### 功能
跨文档构建一次完整或增量 build，作为快照集合的命名版本。

### 规则
**(1) classify_documents**：与上一个 active build 对比每个 document
- prev 中没有 → `NEW` / `add`
- prev 中有但 snapshot_id 变化 → `UPDATE` / `update`
- prev 中有且 snapshot_id 一致 → `SKIP` / `retain`
- prev 中有但本次未出现 → `REMOVE` / `remove`

**(2) determine_build_mode**：有上一个 active build → `incremental`，否则 `full`

**(3) assemble_build**：
- 若 incremental，先把 parent build 中**未在本次 decisions 内**的 snapshot 以 `reason=retain` 复制过来
- 把本次 decisions 写入 `asset_build_document_snapshots`
- 调 `validate_build`（active snapshot ≥1，每个 active snapshot ≥1 segment，incremental 父 build 存在）
- `update_build_status(build_id, "validated")`

### 数据库字段
- `asset_builds`：`status ∈ {building,validated,failed,published,archived}`、`build_mode ∈ {full,incremental}`、`parent_build_id` 链
- `asset_build_document_snapshots`：(build_id, document_id) PK，`selection_status ∈ {active,removed}`、`reason ∈ {add,update,retain,remove}`

### LLM
无。

---

## 11. Stage 8 — Validate Build

集成在 `assemble_build` 内部（同一事务）。

### 规则
- 至少 1 个 `selection_status=active` snapshot
- 每个 active snapshot 至少 1 segment（`count_segments_by_snapshot`）
- incremental build 的 `parent_build_id` 必须存在

失败抛 `ValueError`，build 留在 `building` 状态。

---

## 12. Stage 9 — Publish Release

`knowledge_mining_zym/mining/stages/publishing.py` `publish_release`

### 功能
把 validated build 提升为 channel 上的 active release。

### 规则
- 只接受 `status ∈ {validated, published}` 的 build
- 找当前 channel 上的 active release 做 `previous_release_id` 链
- 新建 release（`status=staging`），调 `activate_release`：把同 channel 的旧 active 改 `retired`，新的改 `active`
- `phase1_only=True` 或本次 run `failed_count>0 且 publish_on_partial_failure=False` 时跳过此阶段

### 数据库字段（`asset_publish_releases`）
- `channel + status='active'` 上的 partial UNIQUE 约束保证**单 channel 单活跃 release**
- `previous_release_id` 形成发布链
- `activated_at / deactivated_at`

---

## 13. 运行时观测

### 13.1 mining_runs

| 字段 | 说明 |
|---|---|
| id | uuid |
| source_batch_id | TEXT |
| input_path | TEXT |
| status | CHECK：`queued/running/completed/interrupted/failed/cancelled` |
| build_id | TEXT |
| total_documents / new / updated / skipped / failed / committed | 计数 |
| started_at / finished_at | TEXT |
| error_summary | TEXT |
| metadata_json | `{ingest_summary, operators, domain_pack, has_failures, failed_count}` |

### 13.2 mining_run_documents

每文档一行，`action ∈ {NEW,UPDATE,SKIP,REMOVE}`，`status ∈ {pending,processing,committed,failed,skipped}`，UNIQUE (run_id, document_key)。

### 13.3 mining_run_stage_events

每个阶段进出各产生一对 (started, completed/failed/skipped) 事件：
- 含 `run_document_id` 的：`parse / segment / enrich / relations / discourse / retrieval_units / segment_persist / relations_persist / retrieval_units_persist / select_snapshot`
- 全局（run_document_id 为 NULL）：`assemble_build / validate_build / publish_release`
- 字段：`duration_ms, output_summary, error_message, metadata_json`
- 双索引：`(run_id, stage, created_at)`、`(run_document_id, stage, created_at)`

### 13.4 协作取消

`_check_cancelled` 在每个文档处理点 / phase 边界做轻量轮询：检测到 `mining_runs.status='cancelled'` 抛 `MiningCancelled`，顶层 `run()` 直接吞掉返回 `{"status":"cancelled"}`，不触发 `fail_run`。

---

## 14. LLM 模板汇总

domain pack (`knowledge_mining_zym/domain_packs/cloud_core_network/domain.yaml`) 中定义 5 个模板：

| template_key | 阶段 | 输出类型 | 使用者 |
|---|---|---|---|
| `mining-segment-boundary` | segment | `json_object` | LlmSegmenter |
| `mining-segment-understanding` | enrich | `json_object` | LlmEnricher |
| `mining-discourse-relation` | discourse | `json_array` | DiscourseRelationBuilder |
| `mining-question-gen` | retrieval_units | `json_array` | LlmQuestionGenerator |
| `mining-contextual-retrieval` | retrieval_units | `json_object` | LLMContextualizer |

启动时 `_init_llm` 调用 `client.register_template(tpl)` 把 5 个模板注册到 llm_service（idempotent）。enrich 模板的 `entities[].type.enum` 由 `build_templates_from_profile` 按 `profile.entity_types` 动态注入。

---

## 15. 设计要点

1. **算子可热插拔**：所有 LLM 算子都有规则版 fallback；服务不可达 → 自动降级，pipeline 仍可跑通
2. **结构性切分恒定**：heading/table/code/list 永远是规则切分；LLM 仅决策「连续段落 run 内的边界」
3. **结构关系恒定**：`previous/next/same_section/section_header_of/same_parent_section/references` 始终由规则产出；LLM 只补充 RST 语篇关系
4. **LLM 输出结构强约束**：所有模板带 JSON Schema；`_validate_groups` / `_apply_llm_result` / `_parse_llm_results` / `_prune_invalid_questions` 多重校验，不合规直接丢弃
5. **可溯源**：retrieval_units 的 `llm_result_refs_json` 保留 `task_id`，能回溯到 `agent_llm_runtime` 的具体 LLM 调用
6. **域可替换**：所有领域知识（实体类型/正则/关键词/模板/检索策略）集中在 `domain_packs/<id>/domain.yaml`，切换 `domain_pack` 参数即可换领域
7. **增量发布**：snapshot 内容 hash 去重 + build_mode auto-detect (`full`/`incremental`) + parent_build retain 合并，保证文档级最小变更范围
