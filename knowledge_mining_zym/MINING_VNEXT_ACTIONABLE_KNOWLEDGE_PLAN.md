# Mining vNext 修改方案：可行动知识资产改造

日期：2026-05-21

范围：`knowledge_mining_zym` 后续新增能力，覆盖：

- 2.2 语义分块（Segmentation Redesign）
- 2.4 检索单元重构（Retrieval Unit Redesign）

核心目标：Mining 不再只产出文本段落，而是产出可被数字员工稳定消费的结构化、可追溯、可行动知识资产。

## 0. 总体原则

### 0.1 改造顺序

两个任务不能并行硬上，必须按依赖顺序推进：

1. 先改 `2.2 Segmentation`：稳定 segment 输出合同，减少碎片和错误边界。
2. 再改 `2.4 Retrieval Unit`：基于稳定 segment 建立 child/parent/source 合同，减少重复召回。

原因：

- retrieval unit 依赖 segment、parent section、source segment。
- 如果 segment 仍然碎片化，entity card、operation card 等结构化检索入口都会被污染。

### 0.2 三层设计

本次改造按三层落地：

1. 资产合同层：明确 segment、parent chunk、entity、retrieval unit 的 schema 和版本。
2. 生成策略层：定义分块、短段合并、实体归一、unit 去重策略。
3. 验证层：通过 golden corpus、指标和数字员工查询用例验证收益。

### 0.3 版本化要求

所有新增或语义变化的资产必须带版本信息：

- `pipeline_version`
- `segmentation_version`
- `retrieval_unit_version`
- `domain_pack_version`

推荐先写入 `metadata_json`，后续稳定后再提升为正式列。

## 1. 资产合同层

### 1.1 Segment 合同

`asset_raw_segments` 继续作为 child segment 的主表，但 segment 的语义变为：

- 用于检索命中的最小语义单元。
- 不再把普通 heading 当作独立 raw_text segment。
- 必须保留所属 section/parent 信息。

每个 segment 至少应具备：

```json
{
  "section_title": "...",
  "section_path": [],
  "parent_section_key": "...",
  "segmentation_strategy": "merge_short_blocks|semantic_split|table_row|list_group",
  "merged_block_count": 1,
  "segmentation_version": "vnext-1"
}
```

### 1.2 Parent Chunk 合同

Parent chunk 用于回答上下文，不作为默认检索主入口。

第一阶段可以不立即新建表，但必须形成显式合同：

- 每个 child segment 必须能通过 `parent_section_key` 找到 parent context。
- parent context 可暂存在 build-time 内存结构或 `metadata_json`，但 Serving 合同必须明确。

推荐最终新增表：

```text
asset_parent_chunks
  id
  document_snapshot_id
  parent_section_key
  section_title
  section_path
  text
  token_count
  created_at
  metadata_json
```

并在 `asset_raw_segments` 增加：

```text
parent_chunk_id
```

如果短期不改 schema，必须在 `asset_raw_segments.metadata_json.parent_section_key` 中写入稳定 key，并在 Serving 侧按该 key 聚合 parent context。

### 1.3 Entity 合同

实体必须分两层：

1. entity mention：某个 segment 中出现的实体。
2. entity card：同一规范化实体的聚合对象。

推荐最终新增表：

```text
asset_entities
  id
  entity_key
  canonical_name
  entity_type
  aliases_json
  summary
  metadata_json

asset_entity_mentions
  id
  entity_id
  document_snapshot_id
  source_segment_id
  mention_text
  evidence_text
  extractor
  confidence
  metadata_json
```

实体归一规则必须先定义，否则 entity_card 会重复、分散，数字员工查实体时拿不到统一对象视图：

- `SMF`
- `Session Management Function`
- `SMF网元`

应归一到同一个 `entity_id`。

第一版归一策略：

- domain pack aliases 优先。
- 大小写折叠。
- 去掉中英文括号中的重复解释。
- 常见后缀归一，如 `网元`、`接口`、`协议`。
- 无法确定时保留独立 entity，但 metadata 写 `normalization_confidence`。

### 1.4 Retrieval Unit 合同

各类 retrieval unit 的职责必须清楚：

| unit_type | 职责 |
| --- | --- |
| raw_text | 原文证据入口，检索命中 child segment |
| table_row | 表格事实入口，定位参数/取值/说明 |
| entity_card | 对象入口，查实体完整信息 |
| generated_question | 问法扩展入口，不承载新事实 |
| operation_card | 操作入口，承载前置条件、步骤、约束、预期结果 |

`contextual_text` 不再生成。LLM contextual retrieval 只增强 `raw_text.search_text`，不新增独立 unit。

所有 segment-derived unit 必须尽量带：

- `source_segment_id`
- `source_refs_json.raw_segment_ids`
- `target_type`
- `target_ref_json`

## 2. 2.2 语义分块最终方案

### 2.1 目标

每个 segment 应有足够语义密度，但不能为了追求大 chunk 把不同知识对象揉在一起。

目标不是固定 600 token，而是按文档结构和知识类型自适应：

- 命令说明、参数描述、约束句可以是 50-200 token。
- 概念说明可以是 200-600 token。
- 长 section 才进入 600-1000 token 的语义二次切分。

### 2.2 Phase 1：消除碎片

涉及文件：

- `knowledge_mining_zym/mining/stages/segment.py`
- `knowledge_mining_zym/mining/infra/text_utils.py`
- `knowledge_mining_zym/mining/infra/domain_pack.py`
- `knowledge_mining_zym/domain_packs/*/domain.yaml`

改动：

1. 普通 heading 不再独立为 raw segment。
2. heading 写入后续内容段的 `section_title`、`section_path`、`metadata_json.parent_section_key`。
3. heading 例外处理：
   - 如果 heading 本身是命令名、实体名、参数名，只作为 section/entity anchor，不生成 raw_text unit。
   - 如果 heading 下无正文，可生成一个低权重 `entity_card` 或 section metadata，不生成普通 raw segment。
4. `token_count < 10` 的普通短段合并，但必须遵守：
   - 不跨 section。
   - 不把 table/code/html_table 强行合并到 paragraph。
   - list item 可按连续列表合并为 list_group。
   - constraint/note 句优先合并到其 subject 附近。
5. CJK token 估算改为字符级近似：
   - CJK 字符按 `cjk_chars / 1.5` 估算。
   - 英文按现有分词或空白近似。
   - 命令、参数、协议名作为独立 token 保留。

短段合并方向规则：

| 短段类型 | 合并方向 |
| --- | --- |
| 前缀说明，如“说明如下：” | 向后合并 |
| note/constraint | 合并到前一个同 subject 段，无法判断则保留并标低权重 |
| list item | 与连续 list items 合并 |
| 参数说明短句 | 合并到同参数表/同 section 的参数段 |
| 孤立导航/目录 | 不生成 segment |

验收：

- `<10 token` segment 接近 0。
- heading-only raw segment 接近 0。
- 不出现跨 section 的错误合并。
- table/code 不被错误揉进 paragraph。

### 2.3 Phase 2：混合分块

流程：

```text
文档进入 segment_stage
  -> 结构切分
  -> heading 附着到 section metadata
  -> section 内短段合并
  -> 长 section 二次切分
  -> 输出 child segments + parent section key
```

策略：

- `<10 token`：按 Phase 1 合并或丢弃导航噪声。
- `10-50 token`：按 block type 和 semantic role 决定保留或合并。
- `50-800 token`：通常保持。
- `>800 token`：按语义边界二次切分。
- 重叠只用于长文本二次切分，默认 100-200 token。

注意：

- 不强制所有块达到 600 token。
- 参数表、命令示例、步骤列表是结构化知识，不按普通 paragraph 的 token 规则处理。

### 2.4 SegmentationPolicy

新增配置：

```yaml
segmentation_policy:
  merge_tiny_threshold_tokens: 10
  soft_min_tokens: 50
  long_section_threshold_tokens: 800
  target_long_chunk_tokens: 600
  max_long_chunk_tokens: 1000
  overlap_tokens: 120
  cjk_chars_per_token: 1.5
  preserve_table_blocks: true
  preserve_code_blocks: true
```

### 2.5 分块审计

新增 audit 输出：

- segment 总数。
- `<10 token` 占比。
- `<50 token` 占比。
- P50/P90 token。
- heading-only segment 数量。
- merged segment 数量。
- table/list/code segment 数量。
- command section 是否完整保留。
- parameter table 是否可追溯。
- procedure steps 是否未被拆散。
- constraint 是否挂到正确 subject。

## 3. 2.4 检索单元重构最终方案

### 3.1 目标

消除重复 unit，建立不同检索入口：

- child raw_text 用于精准命中。
- parent chunk 用于回答上下文。
- entity_card 用于查对象。
- table_row 用于查参数/表格事实。
- operation_card 用于查操作流程。
- generated_question 用于补问法。

### 3.2 Phase 1：去重和来源合同

涉及文件：

- `knowledge_mining_zym/mining/stages/retrieval_units/__init__.py`
- `knowledge_mining_zym/mining/jobs/run.py`
- `knowledge_mining_zym/mining/infra/db.py`
- `databases/asset_core/schemas/002_asset_core_postgresql.sql`

改动：

1. 停止生成 `contextual_text` unit。
2. Serving 侧不再 boost `contextual_text`。历史 release 可以兼容读取，但新 build 不产出。
3. LLM context 写入：
   - `raw_text.search_text`
   - `metadata_json.context_description`
   - `llm_result_refs_json.task_id`
4. 所有 segment-derived unit 写入 `source_segment_id`。
5. 对同一 `source_segment_id` 的 units 做召回去重支持：
   - raw_text 和 generated_question 同时命中时，优先保留 raw_text 或按 query intent 保留 question。
   - entity_card 命中时可展开 source segments，但不重复返回同源 raw_text。

### 3.3 Parent-Child 第一版

第一版必须有显式字段或稳定 metadata，不能只靠隐式约定。

短期方案：

- 在 segment metadata 写：
  - `parent_section_key`
  - `parent_section_title`
  - `parent_section_path`
  - `parent_context_hash`

中期方案：

- 新增 `asset_parent_chunks`。
- `asset_raw_segments.parent_chunk_id` 指向 parent。

Serving 行为：

1. 检索 child retrieval units。
2. 命中 child 后，通过 `source_segment_id` 找 raw segment。
3. 通过 `parent_chunk_id` 或 `parent_section_key` 找 parent context。
4. 回答时带 parent context，不把 parent 作为默认召回候选。

### 3.4 Entity Card 强化

不再把每次 mention 直接当作最终实体卡。

第一版就做轻量聚合：

1. 从 segment 的 `entity_refs_json` 生成 mentions。
2. 按 `entity_key = normalized(entity_type, canonical_name)` 聚合。
3. 每个 entity 生成一张 entity_card retrieval unit。
4. entity_card 文本必须包含：
   - 实体名
   - 实体类型
   - 简短描述
   - aliases
   - source segment ids
   - related terms

第一版 entity_card 示例：

```json
{
  "entity_name": "SMF",
  "entity_type": "network_element",
  "summary": "会话管理功能，负责会话建立、修改和释放。",
  "aliases": ["Session Management Function"],
  "related_terms": ["UPF", "AMF", "N4"],
  "source_segment_ids": ["seg_001", "seg_015"]
}
```

描述来源优先级：

1. 同段 definition 句。
2. section title + nearby sentence。
3. domain pack alias/description。
4. LLM 聚合摘要，失败则使用 evidence snippet。

### 3.5 Generated Question 条件生成

条件：

- 只对 `semantic_role in {concept, parameter, constraint, procedure_step}` 生成。
- 每段最多 1 个。
- table/code/heading 不生成。
- navigation 或 non-substantive 段不生成。
- 与 raw_text 相似度过高则丢弃。

相似度算法第一版使用可解释规则：

- 中文/英文统一使用 char 3-gram Jaccard。
- 同时计算 token Jaccard。
- 任一相似度超过阈值则认为 question 只是改写。

配置：

```yaml
retrieval_policy:
  contextual_text_unit_enabled: false
  max_questions_per_segment: 1
  question_char_ngram_similarity_threshold: 0.75
  question_token_similarity_threshold: 0.7
  entity_card_mode: aggregated
```

### 3.6 Operation Card 预留

虽然本阶段重点是 2.4，但 unit 职责必须为 operation card 预留位置。

operation_card 最终结构：

```json
{
  "operation": "ADD NE",
  "preconditions": [],
  "steps": [],
  "parameters": [],
  "constraints": [],
  "expected_result": [],
  "source_segment_ids": []
}
```

短期不强制实现，但 retrieval unit schema 和 target_type 不应阻碍后续添加。

## 4. 配置方案

建议扩展 domain pack：

```yaml
segmentation_policy:
  merge_tiny_threshold_tokens: 10
  soft_min_tokens: 50
  long_section_threshold_tokens: 800
  target_long_chunk_tokens: 600
  max_long_chunk_tokens: 1000
  overlap_tokens: 120
  cjk_chars_per_token: 1.5
  preserve_table_blocks: true
  preserve_code_blocks: true

retrieval_policy:
  contextual_text_unit_enabled: false
  max_questions_per_segment: 1
  question_char_ngram_similarity_threshold: 0.75
  question_token_similarity_threshold: 0.7
  entity_card_mode: aggregated
```

## 5. 降级策略

### 5.1 Segmentation 降级

- 长 section 二次切分失败：退回结构切分结果。
- CJK token 估算异常：退回现有 token_count。
- parent context 生成失败：保留 child segment，但 metadata 写 `parent_context_missing=true`。

### 5.2 Retrieval Unit 降级

- LLM contextual retrieval 失败：只生成 raw_text，不写 context。
- entity 聚合失败：保留原始 `entity_refs_json` evidence，不生成 aggregated entity_card。
- question 生成失败：不生成 generated_question。
- 相似度计算失败：默认丢弃 generated_question，避免重复污染。

## 6. 验证方案

### 6.1 Golden Corpus

建立固定小语料，至少覆盖：

- 命令说明文档。
- 参数表。
- 操作步骤。
- 约束/限制说明。
- 告警/故障处理。
- 中英文混合技术文档。

每次改造跑 before/after。

### 6.2 指标

| 指标 | 目标 |
| --- | --- |
| `<10 token` segment 占比 | 接近 0 |
| heading-only raw segment | 接近 0 |
| segment 数量 | 降低 30%-50%，但不牺牲关键结构 |
| generated_question 重复率 | 明显下降 |
| entity_card 聚合率 | 同实体多 mention 合并为单 card |
| source trace 完整率 | 接近 100% |

### 6.3 数字员工查询验收

必须能支持以下查询：

1. 查实体：
   - `SMF 是什么？`
2. 查命令：
   - `ADD NE 有哪些参数？`
   - `执行 ADD NE 前有什么前置条件？`
3. 查约束：
   - `配置 UPF 前必须满足什么约束？`
4. 查操作：
   - `如何添加网元？`
   - `失败后应该检查哪些配置？`

这些查询不要求全部由本阶段一次完成，但每个阶段必须说明提升了哪些查询能力。

## 7. 实施拆分

### PR 1：Segmentation 底盘

- 新增 `SegmentationPolicy`。
- heading 附着。
- 短段合并。
- CJK token 修正。
- segmentation audit。
- golden corpus before/after 指标。

### PR 2：Retrieval Unit 去重和 Parent-Child 合同

- 停止生成 `contextual_text`。
- 强化 `source_segment_id`。
- 写入 `parent_section_key`。
- raw_text search_text context 增强。
- generated_question 条件生成和相似度过滤。

### PR 3：Entity Card 聚合

- entity mention 生成。
- entity normalization。
- aggregated entity_card。
- entity_card source trace。

## 8. 不建议本阶段做的事

- 不要一开始就把 parent chunk 作为默认可检索 unit。
- 不要继续增加 retrieval unit 类型来掩盖重复召回。
- 不要在本阶段引入实体关系图、语篇关系或 relation classifier。

## 9. 最终确认版结论

本次 vNext 的正确落点是：

- Segment 是语义稳定的 child evidence。
- Parent 是回答上下文，不是默认召回入口。
- Entity card 是聚合对象，不是每段 mention 的重复卡片。
- Generated question 只做 query expansion，不能复制 raw_text。

先减少错误和重复资产，再增加高级结构化资产。
