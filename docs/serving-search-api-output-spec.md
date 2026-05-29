# Serving Search API 输出结构详解

> 端点：`POST /api/v1/search`
> 服务：agent_serving_java
> 修订日期：2026-05-29

---

## 一、请求输入（SearchRequest）

```json
{
  "query": "SMF 配置 session 管理",     // 必填，原始查询
  "domain": "cloud_core_network",        // 可选，默认使用 defaultDomain
  "channel": "prod",                     // 可选，release 通道，null 用 registry 默认
  "scope": {},                           // 可选，范围约束（如 products, network_elements）
  "entities": [],                        // 可选，预识别实体
  "debug": false,                        // 可选，是否返回调试信息
  "mode": "evidence"                     // 可选，检索模式，默认 "evidence"
}
```

**Java 定义**：`SearchController.java` → `SearchService.search(SearchRequest)`

---

## 二、响应输出（顶层结构）

Controller 返回 `ResponseEntity<Map<String, Object>>`，直接展开 `ContextPack` 的字段：

```json
{
  "query": { ... },              // ContextQuery — 查询上下文
  "items": [ ... ],              // List<ContextItem> — 组装后的上下文条目
  "relations": [ ... ],          // List<ContextRelation> — 条目间关系
  "sources": [ ... ],            // List<SourceRef> — 引用的来源文档
  "evidence_groups": [ ... ],    // List<EvidenceGroup> — 按文档快照分组的证据集
  "issues": [ ... ],             // List<Issue> — 检索诊断问题
  "suggestions": [ ... ],        // List<String> — 查询改进建议
  "debug": { ... }               // Map — 仅 debug=true 时存在
}
```

**Java 定义**：`SearchController.java:23-40` → `ContextPack.java`

---

## 三、字段详解

### 3.1 `query` — ContextQuery

检索过程的查询上下文。

| 字段 | 类型 | 说明 |
|------|------|------|
| `original` | String | 用户原始查询文本 |
| `normalized` | String | 标准化后的查询文本 |
| `intent` | String | 推断的意图类型 |
| `entities` | List\<EntityRef\> | 识别到的实体列表 |
| `scope` | Map\<String, Object\> | 范围约束（products, network_elements 等） |
| `keywords` | List\<String\> | 提取的关键词 |
| `source` | String | 查询理解来源：`"llm"` 或 `"rule"` |
| `releaseId` | String | 搜索使用的 release ID |
| `buildId` | String | 搜索使用的 build ID |
| `snapshotCount` | int | 搜索范围内的文档快照数量 |

**intent 可选值**（`ServingConstants` 定义）：

| 值 | 含义 | 典型触发词 |
|----|------|-----------|
| `command_usage` | 命令用法查询 | 命令/用法/参数/格式/语法 |
| `troubleshooting` | 故障排查 | 故障/排查/告警/错误/异常 |
| `concept_lookup` | 概念查询 | 是什么/什么是/概念/原理 |
| `procedure` | 操作流程 | 步骤/流程/操作 |
| `comparison` | 对比分析 | 区别/差异/对比/比较 |
| `navigational` | 导航定位 | 在哪里/如何找到/路径 |
| `general` | 通用查询 | 默认兜底 |

**Java 定义**：`ContextQuery.java`

---

### 3.2 `items` — List\<ContextItem\>

组装后的上下文条目，是返回结果的**核心**。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | String | — | 条目唯一 ID |
| `kind` | String | — | 条目类型 |
| `role` | String | — | 上下文中的角色 |
| `text` | String | — | 实际文本内容 |
| `score` | double | — | 相关性得分（rerank 后最终分数） |
| `title` | String | — | 文档/章节标题 |
| `blockType` | String | `"unknown"` | 块类型 |
| `semanticRole` | String | `"unknown"` | 语义角色 |
| `sourceId` | String | — | 来源文档 ID |
| `relationToSeed` | String | — | 与 seed 条目的关系类型 |
| `sourceRefs` | Map\<String, Object\> | `{}` | 源引用信息 |
| `metadata` | Map\<String, Object\> | `{}` | 额外元数据 |
| `routeSources` | List\<String\> | `[]` | 贡献该条目的检索路由列表 |
| `scoreChain` | ScoreChain | — | 分数流水线（见 3.3） |
| `evidenceRole` | String | `""` | 证据角色分类 |
| `citation` | Map\<String, Object\> | `{}` | 引用信息（含 section, document_snapshot_id） |

**kind 可选值**：

| 值 | 含义 | 来源 |
|----|------|------|
| `retrieval_unit` | 检索单元（主证据） | Stage 10 组装 |
| `raw_segment` | 原始片段（上下文/支撑） | Stage 10 source resolve / graph expand |

**role 可选值**：

| 值 | 含义 | 生成时机 |
|----|------|----------|
| `seed` | 主检索命中的条目 | 从 retrieval candidates 构建 |
| `context` | 来源片段 | seed 的 source_refs 解析 |
| `support` | 图扩展的支撑片段 | BFS 图遍历 |

**evidenceRole 可选值**：

| 值 | 含义 | 使用场景 |
|----|------|----------|
| `direct_answer` | 最接近主答案的证据 | 优先信任 |
| `support` | 支撑性证据 | 前置条件、参数、限制、步骤 |
| `contrast` | 对比性证据 | 差异、区分、比较 |
| `background` | 背景信息 | 不能单独支撑操作类结论 |

**blockType 可选值**：`paragraph` | `table` | `list` | `code` | `blockquote` | `html_table` | `unknown`

**semanticRole 可选值**：`definition` | `procedure` | `example` | `troubleshooting_step` | `concept` | `note` | `parameter` | `unknown`

**Java 定义**：`ContextItem.java`

---

### 3.3 `scoreChain` — ScoreChain（嵌套在 ContextItem 中）

跟踪一个候选项在检索 Pipeline 中的分数演变。

| 字段 | 类型 | 说明 |
|------|------|------|
| `rawScore` | double | 初始检索分数（来自 Retriever） |
| `fusionScore` | double | 融合后分数（RRF / Weighted RRF） |
| `rerankScore` | double | 重排后分数（最终排序依据） |
| `routeSources` | List\<String\> | 贡献来源路由列表（如 `["fts", "dense_vector"]`） |

**示例**：
```json
{
  "rawScore": 0.85,
  "fusionScore": 0.032,
  "rerankScore": 0.92,
  "routeSources": ["fts", "dense_vector"]
}
```

**Java 定义**：`ScoreChain.java`

---

### 3.4 `entities` — EntityRef（嵌套在 ContextQuery 中）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | String | `""` | 实体类型 |
| `name` | String | — | 原始实体名（查询中出现的文本） |
| `normalizedName` | String | `""` | 标准化/规范化名称 |

**常见 type 值**（由 Domain Pack 定义）：`network_element` | `command` | `protocol` | `parameter` | `concept` | `product`

**示例**：
```json
{ "type": "network_element", "name": "SMF", "normalizedName": "SMF" }
```

**Java 定义**：`EntityRef.java`

---

### 3.5 `relations` — List\<ContextRelation\>

条目间的关系。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | String | 关系 ID |
| `fromId` | String | 源条目 ID |
| `toId` | String | 目标条目 ID |
| `relationType` | String | 关系类型 |
| `distance` | Integer | 距 seed 的跳数（null 表示不适用） |

**relationType 常见值**：

| 值 | 来源 | 含义 |
|----|------|------|
| `same_section` | 结构关系 | 同一章节 |
| `same_parent_section` | 结构关系 | 同父章节 |
| `previous` / `next` | 结构关系 | 前后顺序 |
| `section_header_of` | 结构关系 | 章节标题关系 |
| `elaborates` | RST 分析（Mining） | 详述 |
| `conditions` | RST 分析 | 条件 |
| `causes` | RST 分析 | 因果 |
| `results_in` | RST 分析 | 导致 |
| `backgrounds` | RST 分析 | 背景 |
| `evidences` | RST 分析 | 证据 |
| `contrasts_with` | RST 分析 | 对比 |
| `enables` | RST 分析 | 使能 |
| `sequences` | RST 分析 | 时序 |
| `expansion` | 图扩展（Serving） | BFS 扩展关系 |

**Java 定义**：`ContextRelation.java`

---

### 3.6 `sources` — List\<SourceRef\>

引用的来源文档。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | String | — | 来源 ID |
| `documentKey` | String | — | 文档 key/路径 |
| `title` | String | — | 文档标题 |
| `relativePath` | String | — | 在知识库中的相对路径 |
| `scopeJson` | Map\<String, Object\> | `{}` | 范围元数据 |
| `metadata` | Map\<String, Object\> | `{}` | 额外来源元数据 |

**Java 定义**：`SourceRef.java`

---

### 3.7 `evidence_groups` — List\<EvidenceGroup\>

按文档快照分组的证据集，便于调用方按来源组织展示。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `documentSnapshotId` | String | — | 文档快照 ID |
| `itemIds` | List\<String\> | `[]` | 该组内的 ContextItem ID 列表 |
| `relationIds` | List\<String\> | `[]` | 该组内的 ContextRelation ID 列表 |

**Java 定义**：`EvidenceGroup.java`

---

### 3.8 `issues` — List\<Issue\>

检索过程中的诊断问题。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | String | — | 问题类型 |
| `message` | String | — | 人类可读描述 |
| `detail` | Map\<String, Object\> | `{}` | 结构化详情 |

**type 常见值**（`ServingConstants` 定义）：

| 值 | 含义 | 触发条件 |
|----|------|----------|
| `no_result` | 无检索结果 | items 为空 |
| `low_confidence` | 置信度低 | 最高 score < 0.1 |
| `ambiguous_scope` | 范围模糊 | 查询范围不明确 |
| `partial_context` | 上下文不完整 | source 解析不完整 |

**Java 定义**：`Issue.java`

---

### 3.9 `suggestions` — List\<String\>

基于 issues 生成的查询改进建议，纯文本字符串列表。

---

### 3.10 `debug` — Map\<String, Object\>（仅 `debug=true` 时存在）

| 字段 | 类型 | 说明 |
|------|------|------|
| `understanding` | Map | 查询理解摘要 |
| `route_plan` | Map | 路由计划摘要 |
| `domain_context` | Map | 域上下文信息 |
| `trace` | Map | 全链路追踪 |
| `candidate_count` | int | 重排后候选数量 |
| `fusion_method` | String | 融合方法 |
| `query_embedding_dim` | int | 查询向量维度（0=未使用） |
| `route_traces` | List\<Map\> | 各路由追踪 |

**debug.understanding 结构**：

```json
{
  "original_query": "SMF 配置 session 管理",
  "intent": "command_usage",
  "source": "llm",
  "keywords": ["SMF", "session", "配置"],
  "entities_count": 1
}
```

**debug.route_plan 结构**：

```json
{
  "routes_count": 4,
  "fusion_method": "weighted_rrf",
  "rerank_method": "cascade"
}
```

**debug.domain_context 结构**：

```json
{
  "domain": "cloud_core_network",
  "channel": "prod",
  "database": "default(shared)",
  "scenario_pack": "cloud_core_network",
  "release_id": "rl-xxx",
  "build_id": "bld-xxx",
  "snapshot_count": 3
}
```

**debug.trace 结构**：

```json
{
  "request_id": "req-xxx",
  "total_duration_ms": 1250.5,
  "stages": [
    { "name": "query_understanding", "duration_ms": 680.2, "output_summary": "intent=command_usage, entities=1, source=llm" },
    { "name": "retrieval_router", "duration_ms": 0.5, "output_summary": "routes=4, fusion=weighted_rrf" },
    { "name": "resolve_scope", "duration_ms": 12.3, "output_summary": "snapshots=3" },
    { "name": "embedding", "duration_ms": 45.1, "output_summary": "dim=1024" },
    { "name": "retrieve", "duration_ms": 85.7, "output_summary": "candidates=28" },
    { "name": "fusion", "duration_ms": 0.8, "output_summary": "fused=22, method=weighted_rrf" },
    { "name": "rerank", "duration_ms": 420.9, "output_summary": "ranked=10" },
    { "name": "assembly", "duration_ms": 5.0, "output_summary": "items=12" }
  ]
}
```

**debug.route_traces 结构**：

```json
[
  { "route": "fts", "attempted": true, "candidate_count": 15, "skipped_reason": null, "latency_ms": 35.2 },
  { "route": "dense_vector", "attempted": true, "candidate_count": 12, "skipped_reason": null, "latency_ms": 48.7 },
  { "route": "entity_exact", "attempted": true, "candidate_count": 3, "skipped_reason": null, "latency_ms": 2.1 },
  { "route": "graph_expand", "attempted": true, "candidate_count": 5, "skipped_reason": null, "latency_ms": 15.0 }
]
```

**Java 定义**：`SearchService.java:210-236`

---

## 四、内部模型（不出现在 JSON 输出中）

以下模型用于 Pipeline 内部传递，部分信息在 debug 中间接展示。

### 4.1 QueryUnderstanding

查询理解结果（只在 `debug.understanding` 中部分展示）。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `originalQuery` | String | — | 原始查询 |
| `intent` | String | `"general"` | 意图 |
| `subQueries` | List\<SubQuery\> | `[]` | 子查询分解（仅 LLM 路径） |
| `entities` | List\<EntityRef\> | `[]` | 实体列表 |
| `scope` | Map\<String, Object\> | `{}` | 范围 |
| `keywords` | List\<String\> | `[]` | 关键词 |
| `evidenceNeed` | EvidenceNeed | `empty()` | 证据需求（仅 LLM 路径） |
| `ambiguities` | List\<String\> | `[]` | 歧义检测（仅 LLM 路径） |
| `source` | String | `"rule"` | 来源：`"llm"` 或 `"rule"` |

**Java 定义**：`QueryUnderstanding.java`

### 4.2 SubQuery

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | String | — | 子查询文本 |
| `intent` | String | `"general"` | 子查询意图 |
| `entities` | List\<EntityRef\> | `[]` | 子查询中的实体 |

**Java 定义**：`SubQuery.java`

### 4.3 EvidenceNeed

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `preferredRoles` | List\<String\> | `[]` | 偏好的证据角色 |
| `preferredBlocks` | List\<String\> | `[]` | 偏好的块类型 |
| `needsComparison` | boolean | `false` | 是否需要对比 |
| `needsCitation` | boolean | `false` | 是否需要引用 |

**Java 定义**：`EvidenceNeed.java`

### 4.4 Trace / TraceStage

| 字段 | 类型 | 说明 |
|------|------|------|
| Trace.`requestId` | String | 请求唯一 ID |
| Trace.`stages` | List\<TraceStage\> | 各阶段 |
| Trace.`totalDurationMs` | double | 总耗时（ms） |
| TraceStage.`name` | String | 阶段名 |
| TraceStage.`inputSummary` | String | 输入摘要 |
| TraceStage.`outputSummary` | String | 输出摘要 |
| TraceStage.`durationMs` | double | 阶段耗时（ms） |
| TraceStage.`error` | String | 错误信息（null=成功） |

**Java 定义**：`Trace.java` / `TraceStage.java`

### 4.5 ActiveScope

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `releaseId` | String | — | 当前 active release ID |
| `buildId` | String | — | 当前 build ID |
| `snapshotIds` | List\<String\> | `[]` | 包含的快照 ID 列表 |
| `documentSnapshotMap` | Map\<String, String\> | `{}` | document_key → snapshot_id 映射 |

**Java 定义**：`ActiveScope.java`

---

## 五、完整 JSON 示例

```json
{
  "query": {
    "original": "SMF 配置 session 管理",
    "normalized": "smf 配置 session 管理",
    "intent": "command_usage",
    "entities": [
      { "type": "network_element", "name": "SMF", "normalizedName": "SMF" }
    ],
    "scope": {
      "products": ["UDG", "UNC", "CloudCore"],
      "network_elements": ["SMF"]
    },
    "keywords": ["SMF", "session", "配置", "管理"],
    "source": "llm",
    "releaseId": "rl-a1b2c3d4",
    "buildId": "bld-e5f6g7h8",
    "snapshotCount": 3
  },
  "items": [
    {
      "id": "ru-001",
      "kind": "retrieval_unit",
      "role": "seed",
      "text": "SMF Session 管理配置\n1. 使用 ADD SMFSESSION 命令创建 session ...\n2. 通过 SET SMFSESSION:ID=1,TYPE=IPv4 参数配置 ...",
      "score": 0.92,
      "title": "SMF Session 管理",
      "blockType": "paragraph",
      "semanticRole": "procedure",
      "sourceId": "src-doc001",
      "relationToSeed": null,
      "sourceRefs": {},
      "metadata": {},
      "routeSources": ["fts", "dense_vector"],
      "scoreChain": {
        "rawScore": 0.85,
        "fusionScore": 0.032,
        "rerankScore": 0.92,
        "routeSources": ["fts", "dense_vector"]
      },
      "evidenceRole": "direct_answer",
      "citation": {
        "section": "3.2 SMF Session 管理",
        "document_snapshot_id": "snap-i9j0k1l2"
      }
    },
    {
      "id": "ru-002",
      "kind": "retrieval_unit",
      "role": "seed",
      "text": "SMF 支持 IPv4 和 IPv6 两种 session 类型 ...",
      "score": 0.78,
      "title": "SMF Session 类型",
      "blockType": "paragraph",
      "semanticRole": "definition",
      "sourceId": "src-doc001",
      "relationToSeed": null,
      "sourceRefs": {},
      "metadata": {},
      "routeSources": ["fts"],
      "scoreChain": {
        "rawScore": 0.72,
        "fusionScore": 0.018,
        "rerankScore": 0.78,
        "routeSources": ["fts"]
      },
      "evidenceRole": "support",
      "citation": {
        "section": "3.1 Session 类型概述",
        "document_snapshot_id": "snap-i9j0k1l2"
      }
    },
    {
      "id": "seg-003",
      "kind": "raw_segment",
      "role": "support",
      "text": "session 超时参数配置：使用 SET SMFSESSION_TIMER 命令 ...",
      "score": 0.65,
      "title": "SMF Session 超时配置",
      "blockType": "paragraph",
      "semanticRole": "parameter",
      "sourceId": "src-doc002",
      "relationToSeed": "expansion",
      "sourceRefs": {},
      "metadata": { "expansion_distance": 1, "root_seed_id": "ru-001" },
      "routeSources": [],
      "scoreChain": {
        "rawScore": 0.0,
        "fusionScore": 0.0,
        "rerankScore": 0.65,
        "routeSources": []
      },
      "evidenceRole": "support",
      "citation": {
        "section": "3.3 超时参数",
        "document_snapshot_id": "snap-m3n4o5p6"
      }
    }
  ],
  "relations": [
    {
      "id": "rel-001",
      "fromId": "ru-001",
      "toId": "seg-003",
      "relationType": "expansion",
      "distance": 1
    },
    {
      "id": "rel-002",
      "fromId": "ru-001",
      "toId": "ru-002",
      "relationType": "same_section",
      "distance": null
    }
  ],
  "sources": [
    {
      "id": "src-doc001",
      "documentKey": "smf-session-guide.md",
      "title": "SMF Session 管理指南",
      "relativePath": "docs/smf-session-guide.md",
      "scopeJson": { "products": ["UDG"], "network_elements": ["SMF"] },
      "metadata": {}
    },
    {
      "id": "src-doc002",
      "documentKey": "smf-parameters.md",
      "title": "SMF 参数参考",
      "relativePath": "docs/smf-parameters.md",
      "scopeJson": { "products": ["UDG"], "network_elements": ["SMF"] },
      "metadata": {}
    }
  ],
  "evidence_groups": [
    {
      "documentSnapshotId": "snap-i9j0k1l2",
      "itemIds": ["ru-001", "ru-002"],
      "relationIds": ["rel-002"]
    },
    {
      "documentSnapshotId": "snap-m3n4o5p6",
      "itemIds": ["seg-003"],
      "relationIds": ["rel-001"]
    }
  ],
  "issues": [],
  "suggestions": []
}
```

---

## 六、源文件索引

| 模型 | Java 文件 |
|------|-----------|
| SearchRequest | `domain/SearchRequest.java` |
| ContextPack | `domain/ContextPack.java` |
| ContextQuery | `domain/ContextQuery.java` |
| ContextItem | `domain/ContextItem.java` |
| ContextRelation | `domain/ContextRelation.java` |
| SourceRef | `domain/SourceRef.java` |
| EvidenceGroup | `domain/EvidenceGroup.java` |
| Issue | `domain/Issue.java` |
| EntityRef | `domain/EntityRef.java` |
| ScoreChain | `domain/ScoreChain.java` |
| QueryUnderstanding | `domain/QueryUnderstanding.java` |
| SubQuery | `domain/SubQuery.java` |
| EvidenceNeed | `domain/EvidenceNeed.java` |
| ActiveScope | `domain/ActiveScope.java` |
| Trace / TraceStage | `domain/Trace.java` / `domain/TraceStage.java` |
| ServingConstants | `domain/ServingConstants.java` |
| SearchController | `api/SearchController.java` |
| SearchService | `application/SearchService.java` |
