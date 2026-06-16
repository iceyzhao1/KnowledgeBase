# Mining Pipeline 演进 PRD：从 v2.0 到 v3.0

> 日期：2026-06-04
> 基线：Mining Pipeline v2.0（已完成 orphan 吸收 + 面包屑上下文 + contextualized embedding + enrich 预过滤）
> 目标：系统性改进实体管理、关系质量、层级检索、跨文档连接、管线可组合性
> 面向：开发实现参考文档，按功能点分章节，每个章节可独立开发

---

## 目录

- [EPIC-1: Schema 约束的实体提取](#epic-1-schema-约束的实体提取)
- [EPIC-2: 实体归一化与去重（3 层瀑布）](#epic-2-实体归一化与去重3-层瀑布)
- [EPIC-3: 切片↔实体双向链接](#epic-3-切片实体双向链接)
- [EPIC-4: Schema 约束的关系提取](#epic-4-schema-约束的关系提取)
- [EPIC-5: Section 级 RST 分析（替代 Chunk 级）](#epic-5-section-级-rst-分析替代-chunk-级)
- [EPIC-6: 层级检索结构（Parent-Child Hierarchy）](#epic-6-层级检索结构parent-child-hierarchy)
- [EPIC-7: 跨文档实体连接与社区检测](#epic-7-跨文档实体连接与社区检测)
- [EPIC-8: Pipeline 可组合化重构](#epic-8-pipeline-可组合化重构)
- [附录 A: 工业系统参考矩阵](#附录-a-工业系统参考矩阵)
- [附录 B: 实施优先级与依赖关系](#附录-b-实施优先级与依赖关系)

---

## EPIC-1: Schema 约束的实体提取

### 1.1 当前问题

当前 `enrich` 阶段 LLM 自由提取实体，没有 schema 约束：
- `entity_refs_json = [{"type": "network_element", "name": "SMF"}]` — type 是自由文本
- 同一段落可能提取几十个"实体"，大部分是通用名词（"配置"、"参数"、"特性"）
- 无"该不该提取"的门槛判断

### 1.2 工业做法

#### Graphiti 的"特异性测试"

Graphiti 的提取 prompt 中有一条核心规则：

> "Only extract entities that are specific enough to be uniquely identifiable. Ask: 'Could this have its own Wikipedia article?'"

**明确不提取的列表**：
- 代词
- 抽象概念（joy、balance、growth）
- 通用名词（day、life、people、work、stuff、things）
- 裸关系词（dad、mom、friend、boss）除非带限定词
- 裸通用对象（"supplies"）除非有区分细节
- 时间引用（日期、时间）
- 句子片段

**明确提取的**：
- 命名实体（人名、组织名、地点名）
- 品牌命名项（"Gamecube"、"Ford Mustang"）
- 带限定词的对象（"wool coat"、"cracked windshield"）
- 有区分性描述的项（颜色、材质、所有者）

#### WhyHow 的 Schema 约束

WhyHow 用 JSON Schema 定义合法实体：

```json
{
  "entities": [
    {"name": "medication", "description": "The brand name for a medication..."},
    {"name": "side_effect", "description": "Harmful bodily effects..."}
  ]
}
```

只有匹配预定义类型的实体才会被提取。提取过程不是"把 schema 扔给 LLM"，而是多阶段 pipeline：实体定义 → 相关性检查 → 关系检测 → 模式对齐。

#### LlamaIndex SchemaLLMPathExtractor

用 Pydantic 模型 + `Literal` 类型约束 LLM 输出：

```python
entities = Literal["PERSON", "PLACE", "THING"]
relations = Literal["PART_OF", "HAS", "IS_A"]

# 动态构建 Pydantic 模型
class Triplet(BaseModel):
    subject: Entity   # type 字段受 Literal 约束
    relation: Relation
    object: Entity
```

LLM 的 structured output 被迫遵循 schema。提取后再用 `_prune_invalid_triplets` 剪枝：
1. Schema 验证：检查 `(subject_type, relation, obj_type)` 是否在合法模式中
2. 自引用移除：`subject.lower() == obj.lower()` 的跳过

#### GraphRAG 的频率排序

GraphRAG 先提取所有实体，然后用频率/度数排序：
- `frequency`：被多少个 chunk 提到
- `rank`：图中连接数
- 只保留高频/高连接度的实体

典型密度：1200 token chunk → 5-15 个实体。

### 1.3 具体实现方案

#### Step 1: domain.yaml 定义 Entity Schema

```yaml
# scenario_packs/cloud_core_network/domain.yaml 新增
entity_schema:
  types:
    - name: NetworkFunction
      description: "核心网网元功能模块，如 SMF、UPF、AMF、PCF、UDM 等"
      examples: ["SMF", "UPF", "AMF", "PCF", "UDM", "NRF", "AUSF", "NSSF", "SMSF", "SEPP"]

    - name: Protocol
      description: "通信协议和接口协议"
      examples: ["GTP-U", "GTP-C", "PFCP", "DIAMETER", "BGP", "OSPF", "SIP", "RTP"]

    - name: Interface
      description: "网元间的标准化接口"
      examples: ["N4", "N3", "N2", "N6", "N7", "N11", "N15", "S1-MME", "S1-U"]

    - name: Parameter
      description: "具体可配置参数名（非通用词'参数'本身）"
      examples: ["heartbeat_interval", "session_timeout", "max_retries", "mtu_size"]

    - name: Fault
      description: "具体故障现象或错误"
      examples: ["PFCP_session_setup_failure", "handover_failure", "GTP_tunnel_timeout"]

  extraction_rules:
    specificity_test: "该术语能否有自己的百科词条？不能则不提取"
    never_extract:
      - "代词"
      - "通用名词：配置、参数、特性、功能、流程、接口（除非带限定词如 N4 接口）"
      - "抽象概念：性能、安全、可靠性"
      - "时间引用"
      - "句子片段"
    max_per_segment: 10
```

#### Step 2: 修改 enrich LLM prompt

在 `mining-segment-understanding` 模板中增加 schema 约束：

```
你是云核心网领域知识提取专家。

从以下文本中提取实体。严格遵守以下规则：

【实体类型】只提取以下类型的实体：
{entity_types_with_descriptions}

【特异性门槛】只有足够具体、可以被唯一标识的术语才提取。
问自己：这个术语能有自己独立的百科词条吗？
- "SMF" → 提取（有独立词条）
- "网元" → 不提取（太泛）
- "N4 接口" → 提取（有标准定义）
- "接口" → 不提取（太泛）

【绝不提取】
- 代词、抽象概念、通用名词
- "配置"、"参数"、"特性"、"功能" 等无限定通用词
- 同一实体在本段只出现一次

【数量限制】本段最多提取 {max_per_segment} 个实体。

文本：
{text}
```

#### Step 3: 修改 `_apply_llm_result` 中的实体过滤

```python
# 在 _apply_llm_result 中增加 schema 过滤
allowed_types = {t["name"] for t in entity_schema.get("types", [])}
entity_refs = [
    {"type": e.get("type", ""), "name": e.get("name", "")}
    for e in entities
    if e.get("name")
    and (not allowed_types or e.get("type") in allowed_types)  # schema 过滤
    and len(e.get("name", "")) > 1  # 排除单字
    and e.get("name", "") not in never_extract_set  # 排除通用词
]
```

#### Step 4: 修改 RetrievalPolicy

```python
@dataclass(frozen=True)
class RetrievalPolicy:
    # ... existing fields ...
    max_entities_per_segment: int = 10  # 每 segment 最大实体数
```

### 1.4 验收标准

- 每个 segment 最多 10 个实体
- 无通用名词（"配置"、"参数"、"功能"）出现在 entity_refs_json 中
- 所有实体的 type 必须在 schema 定义的 types 列表中
- 已有测试全部通过

### 1.5 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/infra/domain_pack.py` | 新增 EntitySchema dataclass，RetrievalPolicy 加 max_entities_per_segment |
| `knowledge_mining/mining/stages/enrich/__init__.py` | schema 过滤逻辑 |
| `llm_service/` 对应模板 | 修改 mining-segment-understanding prompt |
| `scenario_packs/cloud_core_network/domain.yaml` | 新增 entity_schema 配置 |
| `knowledge_mining/tests/test_v11_pipeline.py` | 新增测试 |

---

## EPIC-2: 实体归一化与去重（3 层瀑布）

### 2.1 当前问题

- "UPF" / "User Plane Function" / "用户面功能" 被视为不同实体
- 跨文档的同一实体无法关联
- 无实体级去重

### 2.2 工业做法：Graphiti 的 3 层瀑布

Graphiti 的 `resolve_extracted_nodes()` 实现了最成熟的实体归一化管线：

#### Tier 1: 精确字符串匹配

```python
def _normalize_string_exact(name: str) -> str:
    normalized = re.sub(r'[\s]+', ' ', name.lower())
    return normalized.strip()
```

- 归一化（小写 + 合并空白）后精确比较
- 捕获 **60-80%** 的重复，零成本

#### Tier 2: 模糊 MinHash/LSH 匹配

**关键常量**：
- `_NAME_ENTROPY_THRESHOLD = 1.5`（Shannon 熵）
- `_MIN_NAME_LENGTH = 6`
- `_MIN_TOKEN_COUNT = 2`
- `_FUZZY_JACCARD_THRESHOLD = 0.9`
- `_MINHASH_PERMUTATIONS = 32`
- `_MINHASH_BAND_SIZE = 4`

**熵门槛**（避免短名/通用名误匹配）：
```python
def _has_high_entropy(normalized_name):
    # 必须 >= 6 字符 AND >= 2 token AND 熵 >= 1.5
    # 短名/低熵名（"Bob"、"cat"）跳过模糊匹配
```

**MinHash 签名**：
```python
def _shingles(normalized_name: str) -> set[str]:
    cleaned = normalized_name.replace(' ', '')
    return {cleaned[i:i+3] for i in range(len(cleaned) - 2)}  # 3-gram

def _minhash_signature(shingles):
    # 32 个排列，每个取最小哈希值
    return tuple(min(hash_shingle(s, seed) for s in shingles) for seed in range(32))

def _lsh_bands(signature):
    # 32 个元素分成 8 个 band（每 band 4 个元素）
    return [tuple(signature[i:i+4]) for i in range(0, 32, 4)]
```

**LSH 查找**：预构建 `lsh_buckets: dict[(band_index, band_tuple), list[uuid]]`。查询时计算 MinHash → 分 band → 收集候选 → 计算 Jaccard ≥ 0.9。

#### Tier 3: LLM 推理

仅对 Tier 1+2 未解决的实体调用 LLM。

**Prompt 规则**：
- 实体必须指同一现实对象才算重复
- "NYC" 匹配 "New York City"（同一地点）
- "Java"（编程语言）不匹配 "Java"（岛屿）
- "Marco's car" 匹配 "Marco's vehicle"（同义词，同一所有者）

**响应模型**：
```python
class NodeDuplicate(BaseModel):
    id: int                           # 未解决实体的 id
    name: str                         # 最佳完整名称
    duplicate_candidate_id: int       # 匹配的候选 id，或 -1（新实体）
```

### 2.3 具体实现方案

#### Step 1: domain.yaml 定义别名表

```yaml
entity_schema:
  # ... types 定义（来自 EPIC-1） ...

  aliases:
    UPF: ["用户面功能", "User Plane Function", "UPF网元"]
    SMF: ["会话管理功能", "Session Management Function"]
    AMF: ["接入和移动性管理功能", "Access and Mobility Management Function"]
    GTP-U: ["GTP用户面", "GTP User Plane"]
    PFCP: ["Packet Forwarding Control Protocol", "报文转发控制协议"]
    # ...
```

#### Step 2: 新增 Resolve 阶段

在 `knowledge_mining/mining/stages/` 下新增 `resolve/` 模块。

```python
# knowledge_mining/mining/stages/resolve/__init__.py

class EntityResolver:
    """3 层瀑布实体归一化。"""

    def __init__(self, aliases: dict[str, list[str]], embedding_generator=None, llm_client=None):
        self._alias_map = self._build_alias_map(aliases)  # {normalized_name: canonical_name}
        self._embedder = embedding_generator
        self._llm = llm_client

    def resolve(self, segments: list[RawSegmentData]) -> list[RawSegmentData]:
        """对所有 segment 的 entity_refs_json 执行归一化。"""
        # Tier 1: 精确匹配 + 别名表
        # Tier 2: Embedding cosine > 0.92
        # Tier 3: LLM 推理（可选）
        ...

    def _build_alias_map(self, aliases: dict) -> dict:
        """构建 {normalized_alias: canonical_name} 映射。"""
        result = {}
        for canonical, variants in aliases.items():
            result[canonical.lower().strip()] = canonical
            for v in variants:
                result[v.lower().strip()] = canonical
        return result

    def _tier1_exact(self, name: str) -> str | None:
        """精确匹配 + 别名表查找。"""
        return self._alias_map.get(name.lower().strip())

    def _tier2_embedding(self, name: str, candidates: list[str]) -> str | None:
        """Embedding cosine > 0.92 匹配。"""
        if not self._embedder:
            return None
        # 嵌入 name，与所有候选的预计算嵌入比较
        ...

    def _tier3_llm(self, name: str, candidates: list[str]) -> str | None:
        """LLM 推理判断是否同一实体。"""
        if not self._llm:
            return None
        ...
```

#### Step 3: Pipeline 接入

在 `pipeline.py` 的 enrich 和 discourse 之间插入 resolve 阶段：

```python
# pipeline.py process_document()
# Stage 3: Enrich (已有)
# Stage 3.5: Resolve (新增)
resolver = cfg.entity_resolver
if resolver is not None and ctx.segments:
    resolved = resolver.resolve(list(ctx.segments))
    ctx = ctx.with_updates(segments=tuple(resolved))
```

#### Step 4: 归一化结果写入 metadata

```python
# 每个 segment 的 entity_refs_json 中，实体增加 canonical_name 字段
entity_refs = [
    {
        "type": "NetworkFunction",
        "name": "用户面功能",
        "canonical_name": "UPF",  # 新增：归一化后的标准名
    }
    for ref in seg.entity_refs_json
]
```

### 2.4 实施建议

- **第一批只做 Tier 1**（别名表 + 精确匹配）：无需额外基础设施，立即生效
- Tier 2（embedding）需要在 build 阶段预计算实体名嵌入，可后做
- Tier 3（LLM）成本高，只对高价值实体使用

### 2.5 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/stages/resolve/__init__.py` | 新增 EntityResolver |
| `knowledge_mining/mining/infra/domain_pack.py` | EntitySchema 增加 aliases 字段 |
| `knowledge_mining/mining/pipeline.py` | 新增 resolve 阶段 |
| `scenario_packs/cloud_core_network/domain.yaml` | 新增 aliases |
| `knowledge_mining/tests/test_v11_pipeline.py` | 新增归一化测试 |

---

## EPIC-3: 切片↔实体双向链接

### 3.1 当前问题

当前 `seg.entity_refs_json` 提供了**正向链接**（segment → entities），但缺少**反向链接**（entity → 哪些 segments 提到了它）。

### 3.2 工业做法

#### GraphRAG 的双向 ID 列表

```
TextUnit:
  entity_ids: [id1, id2, ...]       # 正向：本 chunk 包含哪些实体

Entity:
  text_unit_ids: [id1, id2, ...]    # 反向：哪些 chunk 提到本实体
```

> "Every entity, relationship, and claim maintains text_unit_ids that point back to the text units from which they were extracted."

#### FalkorDB 的 MENTIONED_IN 边

FalkorDB 用 `MENTIONED_IN` 边类型连接 Entity 和 Chunk：

```
Entity -[:MENTIONED_IN]-> Chunk
```

检索时的 4 路通道之一就是 MENTIONED_IN 遍历：从查询发现的实体出发，遍历 MENTIONED_IN 边找到相关 chunk。

#### Mem0 的实体索引

Mem0 在主向量存储之外维护一个独立的 `{collection}_entities` 实体集合。检索时用 spaCy 从查询中提取实体，在实体索引中匹配，命中实体的 memory 获得额外分数（entity_boost）。

### 3.3 具体实现方案

#### Step 1: 新增 asset_entity_mentions 表

```sql
CREATE TABLE asset_entity_mentions (
    mention_id        TEXT PRIMARY KEY,
    document_snapshot_id TEXT NOT NULL REFERENCES asset_document_snapshots(id),
    segment_id        TEXT NOT NULL REFERENCES asset_raw_segments(id),
    entity_type       TEXT NOT NULL,           -- NetworkFunction, Protocol, ...
    entity_name       TEXT NOT NULL,           -- 原始名
    canonical_name    TEXT,                    -- 归一化后的标准名（EPIC-2）
    confidence        REAL DEFAULT 1.0,
    metadata_json     JSONB DEFAULT '{}'
);

CREATE INDEX idx_em_entity ON asset_entity_mentions(canonical_name, entity_type);
CREATE INDEX idx_em_segment ON asset_entity_mentions(segment_id);
CREATE INDEX idx_em_snapshot ON asset_entity_mentions(document_snapshot_id);
```

#### Step 2: Build 阶段写入 mentions

在 `db_write_stage` 中，写入 segments 后，遍历所有 segment 的 `entity_refs_json`，为每个实体写入一条 mention 记录。

```python
# db_write_stage 中新增
for seg in segments:
    seg_key = f"{seg.document_key}#{seg.segment_index}"
    seg_id = seg_id_map.get(seg_key, uuid.uuid4().hex)
    for ref in seg.entity_refs_json:
        canonical = ref.get("canonical_name", ref.get("name", ""))
        asset_db.insert_entity_mention(
            mention_id=uuid.uuid4().hex,
            document_snapshot_id=snapshot_id,
            segment_id=seg_id,
            entity_type=ref.get("type", ""),
            entity_name=ref.get("name", ""),
            canonical_name=canonical,
        )
```

#### Step 3: Serving 端利用 mentions

Serving 的检索可新增"实体遍历"通道：
- 查询中提取实体 → `asset_entity_mentions` 查 canonical_name → 找到所有 segment_id
- 这就是 FalkorDB 的 MENTIONED_IN 遍历模式

### 3.4 改动文件

| 文件 | 改动 |
|------|------|
| `databases/asset_core/schemas/` | 新增 entity_mentions 建表 SQL |
| `knowledge_mining/mining/infra/pg_schema.py` | 新增表定义 |
| `knowledge_mining/mining/pipeline.py` | db_write_stage 写入 mentions |
| `reset_db.py` / `db_tables.py` | 新增表 |
| `knowledge_mining/tests/test_v11_pipeline.py` | 新增测试 |

---

## EPIC-4: Schema 约束的关系提取

### 4.1 当前问题

当前 RST 关系用 sliding window + 150 字符预览分析，两个关键缺陷：
1. **分析粒度错误**：在 chunk 级别分析，不是 section 级别（EPIC-5 解决）
2. **关系无 schema 约束**：15 种 RST 关系自由组合，没有 (head_type, relation, tail_type) 模式限制

### 4.2 工业做法

#### Graphiti 的 Edge 提取规则

Graphiti 的 edge 提取 prompt 强制执行：

1. **source 和 target 必须来自已提取的实体列表**（不能凭空发明）
2. **每个关系必须附带自然语言事实描述**（fact: str）
3. **不能泛化**（"Gamecube" 不泛化为 "gaming console"）
4. **不能有语义冗余**（重复事实不提取）
5. **不能有自关系**（source ≠ target）

**Edge 数据模型**：
```python
class Edge(BaseModel):
    source_entity_name: str       # 必须来自 ENTITIES 列表
    target_entity_name: str       # 必须来自 ENTITIES 列表
    relation_type: str            # SCREAMING_SNAKE_CASE
    fact: str                     # 自然语言事实描述
    valid_at: str | None          # 何时变为真
    invalid_at: str | None        # 何时变为假
    episode_indices: list[int]    # 来源文本索引
```

#### WhyHow 的 Pattern 约束

```json
{
  "patterns": [
    {"head": "medication", "relation": "contains", "tail": "active_ingredient"},
    {"head": "medication", "relation": "treats", "tail": "condition"}
  ]
}
```

三元组只在匹配预定义 pattern 时才创建。

#### LlamaIndex 的 SchemaLLMPathExtractor

用 Pydantic 动态模型约束 LLM 输出，然后 `_prune_invalid_triplets` 剪枝：
- Schema 验证：`(subject_type, relation, obj_type)` 必须在合法列表中
- 自引用移除

### 4.3 具体实现方案

#### Step 1: domain.yaml 定义 Relation Patterns

```yaml
entity_schema:
  # ... types 和 aliases（来自 EPIC-1, EPIC-2） ...

  relation_types:
    - name: IMPLEMENTS
      description: "网元实现了某个协议"
    - name: CONFIGURED_BY
      description: "网元/协议通过某个参数配置"
    - name: CONNECTED_VIA
      description: "两个网元通过某个接口连接"
    - name: CAUSES
      description: "故障由某个参数/配置引起"
    - name: RESOLVED_BY
      description: "故障通过某个操作/参数解决"
    - name: DEPENDS_ON
      description: "功能/参数依赖另一个功能/参数"
    - name: APPLIES_TO
      description: "配置/规则适用于某个网元或场景"

  patterns:
    - head: NetworkFunction
      relation: IMPLEMENTS
      tail: Protocol
    - head: NetworkFunction
      relation: CONFIGURED_BY
      tail: Parameter
    - head: NetworkFunction
      relation: CONNECTED_VIA
      tail: Interface
    - head: NetworkFunction
      relation: CONNECTED_VIA
      tail: NetworkFunction
    - head: Fault
      relation: CAUSES
      tail: Parameter
    - head: Fault
      relation: RESOLVED_BY
      tail: Parameter
    - head: Parameter
      relation: DEPENDS_ON
      tail: Parameter
    - head: Parameter
      relation: APPLIES_TO
      tail: NetworkFunction
    - head: Protocol
      relation: APPLIES_TO
      tail: Interface
```

#### Step 2: 新增 EntityRelationExtractor

与现有的 RST DiscourseRelationBuilder **并行运行**，不替代：

```python
# knowledge_mining/mining/stages/relations/entity_relations.py

class EntityRelationExtractor:
    """Schema 约束的实体间关系提取。"""

    def __init__(self, patterns: list[dict], llm_client=None):
        self._patterns = {(p["head"], p["relation"], p["tail"]) for p in patterns}
        self._llm = llm_client

    def extract(self, segments: list[RawSegmentData]) -> list[SegmentRelationData]:
        """从 segments 中提取实体间关系。"""
        relations = []
        for seg in segments:
            entities = seg.entity_refs_json
            if len(entities) < 2:
                continue
            # 对同一 segment 内的实体对，检查是否匹配 pattern
            pairs = self._find_pattern_matches(entities)
            if pairs:
                # 可选：LLM 验证关系 + 生成 fact 描述
                seg_relations = self._extract_relations(seg, pairs)
                relations.extend(seg_relations)
        return relations

    def _find_pattern_matches(self, entities: list[dict]) -> list[tuple]:
        """找到匹配 pattern 的实体对。"""
        matches = []
        for i, e1 in enumerate(entities):
            for j, e2 in enumerate(entities):
                if i == j:
                    continue
                for (h, r, t) in self._patterns:
                    if e1.get("type") == h and e2.get("type") == t:
                        matches.append((e1, e2, r))
        return matches

    def _extract_relations(self, seg, pairs):
        """提取关系（纯规则 或 LLM 增强）。"""
        relations = []
        for src, tgt, rel_type in pairs:
            # 纯规则模式：直接创建关系
            relations.append(SegmentRelationData(
                source_segment_key=f"{seg.document_key}#{seg.segment_index}",
                target_segment_key=f"{seg.document_key}#{seg.segment_index}",
                relation_type=rel_type.lower(),
                weight=0.7,
                confidence=0.7,
                metadata_json={
                    "source": "entity_schema",
                    "source_entity": src.get("canonical_name", src.get("name", "")),
                    "target_entity": tgt.get("canonical_name", tgt.get("name", "")),
                    "fact": f"{src.get('name', '')} {rel_type} {tgt.get('name', '')}",
                },
            ))
        return relations
```

#### Step 3: LLM 增强版（可选）

对匹配 pattern 的实体对，可以调用 LLM 生成 fact 描述：

```
给定以下文本和两个实体，判断它们之间的关系并描述具体事实。

文本：{raw_text}
实体A：{entity_a_name}（{entity_a_type}）
实体B：{entity_b_name}（{entity_b_type}）
候选关系：{relation_type}

请回答：
1. 关系是否存在？（true/false）
2. 关系的事实描述（用一句话描述具体关系）
3. 置信度（0-1）
```

### 4.4 与现有 RST 的关系

| 维度 | RST 关系（DiscourseRelationBuilder） | 实体关系（EntityRelationExtractor） |
|------|------|------|
| 分析对象 | segment 与 segment 之间 | 实体与实体之间 |
| 关系类型 | elaborates, causes, contrasts 等 15 种 | IMPLEMENTS, CONFIGURED_BY 等 domain-specific |
| 约束 | RST 白名单 | Schema pattern 约束 |
| 存储位置 | `asset_raw_segment_relations` | 同表，relation_type 加前缀区分 |
| 用途 | 话语结构理解 | 知识图谱构建 |

两者**共存**，不替代。

### 4.5 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/stages/relations/entity_relations.py` | 新增 |
| `knowledge_mining/mining/infra/domain_pack.py` | EntitySchema 增加 relation_types + patterns |
| `knowledge_mining/mining/pipeline.py` | 新增实体关系提取阶段 |
| `scenario_packs/cloud_core_network/domain.yaml` | 新增 relation_types + patterns |
| `knowledge_mining/tests/test_v11_pipeline.py` | 新增测试 |

---

## EPIC-5: Section 级 RST 分析（替代 Chunk 级）

### 5.1 当前问题

当前 `_analyze_window()` 取每个 segment 前 150 字符，在 chunk 级别分析 RST 关系。一个段落被切成 3 个 segment，RST 看到的是 3 个碎片。

### 5.2 工业做法

#### 层级 RST 解析（Top-Down）

现代 RST 解析采用**自顶向下**方式：

1. 从整个文档开始，递归分割
2. 先在文档级分割（章节边界），再在章节内分割（段落边界）
3. 句子边界被强制作为分割候选（1S-1S 约束）
4. 每次分割预测 nuclearity（Nucleus/Satellite）和 relation type

**关键洞察**：先在宏观结构上做决策，再做微观决策。这符合人类阅读习惯。

#### LlamaIndex 的层级检索

AutoMergingRetriever 用 3 级层级：
- Level 1 (root): 2048 tokens
- Level 2 (intermediate): 512 tokens
- Level 3 (leaf): 128 tokens

**只有 leaf node 做 embedding**。当检索时 >50% 的 children 被召回，自动合并为 parent。

### 5.3 具体实现方案

#### Step 1: 按 section_path 分组 segments

```python
def _group_by_section(segments: list[RawSegmentData]) -> dict[tuple, list[RawSegmentData]]:
    """按 section_path 分组 segments。"""
    groups = {}
    for seg in segments:
        # section_path 是 dict list，如 [{"level":1,"title":"SMF配置"}]
        path_key = tuple(
            p.get("title", "") for p in seg.section_path
        )
        if path_key not in groups:
            groups[path_key] = []
        groups[path_key].append(seg)
    return groups
```

#### Step 2: 合并同一 section 的 segments 为完整文本

```python
def _merge_section_text(segments: list[RawSegmentData]) -> str:
    """合并同一 section 下的 segments 为完整文本。"""
    parts = []
    for seg in sorted(segments, key=lambda s: s.segment_index):
        if seg.block_type != "heading":  # heading 不参与合并
            parts.append(seg.raw_text)
    return "\n".join(parts)
```

#### Step 3: Section 级 RST 分析

修改 `DiscourseRelationBuilder._analyze_window`，改为 section 级：

```python
def build(self, segments, *, seg_ids=None, **kwargs):
    # 1. 按 section 分组
    section_groups = _group_by_section(segments)

    # 2. 构建 section 摘要
    section_summaries = []
    for path_key, segs in section_groups.items():
        full_text = _merge_section_text(segs)
        summary = full_text[:300]  # 取前 300 字符
        section_summaries.append({
            "path_key": path_key,
            "title": path_key[-1] if path_key else "文档",
            "summary": summary,
            "segment_count": len(segs),
            "representative_seg_index": segs[0].segment_index,
            "segment_indices": [s.segment_index for s in segs],
        })

    # 3. 对 section 列表做 RST 分析（sliding window）
    for start in range(0, len(section_summaries), self._window_size - 1):
        window = section_summaries[start : start + self._window_size]
        if len(window) < 2:
            continue
        section_relations = self._analyze_section_window(window)
        # 4. 关系传播到 segments
        all_relations.extend(self._propagate_to_segments(section_relations, section_groups))

    return all_relations
```

#### Step 4: 关系传播

Section 级关系传播到下属 segment：

```python
def _propagate_to_segments(self, section_relations, section_groups):
    """将 section 间关系传播为 segment 间关系。"""
    propagated = []
    for rel in section_relations:
        # source section 的第一个非 heading segment
        src_segs = section_groups.get(rel.source_path, [])
        tgt_segs = section_groups.get(rel.target_path, [])
        if src_segs and tgt_segs:
            src_seg = next((s for s in src_segs if s.block_type != "heading"), src_segs[0])
            tgt_seg = next((s for s in tgt_segs if s.block_type != "heading"), tgt_segs[0])
            propagated.append(SegmentRelationData(
                source_segment_key=_make_segment_key(src_seg),
                target_segment_key=_make_segment_key(tgt_seg),
                relation_type=rel.relation_type,
                weight=rel.weight * 0.8,  # 传播时降低权重
                confidence=rel.confidence * 0.8,
                metadata_json={**rel.metadata_json, "propagated_from": "section_level"},
            ))
    return propagated
```

### 5.4 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/stages/relations/__init__.py` | 改为 section 级分析 |

---

## EPIC-6: 层级检索结构（Parent-Child Hierarchy）

### 6.1 当前问题

所有 segment 扁平存储，没有层级关系。section_path 有层级信息但检索时完全忽略。

### 6.2 工业做法：LlamaIndex AutoMergingRetriever

**数据结构**：
```
Node:
  node_id: UUID
  text: string
  parent_node: RelatedNodeInfo | None
  child_nodes: List[RelatedNodeInfo] | None
```

**3 级层级**：
- Level 1 (root): 2048 tokens
- Level 2 (intermediate): 512 tokens
- Level 3 (leaf): 128 tokens

**存储**：所有 node 存入 DocumentStore，**只有 leaf node 做 embedding 和索引**。

**合并算法**：
```python
# 当 >50% 的 parent 的 children 被召回时，合并为 parent
ratio = retrieved_children / total_children
if ratio > 0.5:  # simple_ratio_thresh
    # 移除 children，添加 parent（平均分数）
    new_nodes.append(parent_node_with_avg_score)
```

**填充算法**：如果两个连续召回的 node 之间有缺失 sibling，自动补入。

### 6.3 具体实现方案

#### Step 1: 利用 section_path 构建层级

当前 `segment_document()` 已经产生了带 `section_path` 的 segments。利用这些信息构建层级关系：

```python
# segment.py 中新增
def _build_hierarchy(segments: list[RawSegmentData]) -> list[RawSegmentData]:
    """为 segments 建立 parent-child 关系。"""
    # 1. 按 section_path 深度分组
    # 2. 同一 section 下的 segments 是 siblings
    # 3. 父 section 的 segments 是 parent
    for seg in segments:
        meta = dict(seg.metadata_json)
        # 记录 parent section path（去掉最后一个元素）
        parent_path = seg.section_path[:-1] if len(seg.section_path) > 1 else []
        meta["parent_section_path"] = parent_path
        # 记录 sibling segment indices
        # ...
```

#### Step 2: 新增 Section Summary 类型检索单元

```python
# retrieval_units/__init__.py 中新增
def _make_section_summary_unit(segments_in_section, ...):
    """为每个 section 生成摘要检索单元。"""
    full_text = "\n".join(s.raw_text for s in segments_in_section if s.block_type != "heading")
    return RetrievalUnitData(
        unit_key=f"ru:{doc_key}:section_summary:{section_path_hash}",
        unit_type="section_summary",
        text=full_text[:2000],  # section 全文（用于 embedding）
        search_text=tokenize_for_search(full_text[:2000]),
        weight=0.3,  # 低于 raw_text 的权重
        ...
    )
```

#### Step 3: 检索时的层级合并

Serving 端实现（不在 mining 端）：
1. 检索 leaf segments
2. 按 section 分组
3. 如果某 section >50% 的 segments 被召回，合并为 section 全文返回

### 6.4 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/stages/segment.py` | 新增层级关系元数据 |
| `knowledge_mining/mining/stages/retrieval_units/__init__.py` | 新增 section_summary unit |
| `knowledge_mining/mining/infra/domain_pack.py` | RetrievalPolicy 新增 section_summary 开关 |

---

## EPIC-7: 跨文档实体连接与社区检测

### 7.1 当前问题

- 关系只在单文档内建立
- 同一实体在不同文档中被视为独立
- 无法回答"SMF 在不同文档中的配置差异"

### 7.2 工业做法

#### Microsoft GraphRAG 的 Leiden 社区检测

**完整 Pipeline**：
```
Source Documents
  → Text Chunks
    → Entity + Relationship Extraction
      → Entity Summarization（合并跨 chunk 描述）
        → Knowledge Graph
          → Leiden 层级聚类（max_cluster_size=10, resolution=1.0, randomness=0.001）
            → Community Report Generation（LLM，bottom-up）
              → Embed community reports
```

**Community Report Prompt** 输出：
```json
{
    "title": "描述性名称",
    "summary": "执行摘要",
    "rating": 0-10,
    "rating_explanation": "评分理由",
    "findings": [
        {"summary": "洞察", "explanation": "详细解释"}
    ]
}
```

**检索模式**：
- **Global Search**：map-reduce 在社区报告上回答全局性问题
- **Local Search**：实体 → 社区 → 社区成员 → 关联段 → 回答局部问题

#### 跨文档实体链接

**4 层瀑布**：
1. 精确字符串匹配（归一化后）→ 捕获 60-80%
2. 模糊字符串匹配（Dice > 0.6）→ 捕获 10-15%
3. Embedding 相似度（cosine > 0.85）→ 捕获 5-10%
4. LLM/cross-encoder 验证 → 消歧（"Apple" 公司 vs "Apple" 水果）

### 7.3 具体实现方案

#### Step 1: Build 阶段的跨文档实体聚合

在 build（非 document-level）阶段：

```python
def build_stage(asset_db, build_id):
    """Build 阶段：跨文档聚合实体。"""
    # 1. 查询本次 build 所有 entity_mentions
    mentions = asset_db.query("""
        SELECT em.canonical_name, em.entity_type,
               array_agg(DISTINCT em.segment_id) as segment_ids,
               array_agg(DISTINCT ds.document_id) as document_ids
        FROM asset_entity_mentions em
        JOIN asset_raw_segments seg ON em.segment_id = seg.id
        JOIN asset_document_snapshots ds ON seg.document_snapshot_id = ds.id
        WHERE ds.build_id = %s
        GROUP BY em.canonical_name, em.entity_type
        HAVING count(DISTINCT ds.document_id) > 0
    """, (build_id,))

    # 2. 创建跨文档实体记录
    for m in mentions:
        asset_db.insert_cross_document_entity(
            entity_id=uuid.uuid4().hex,
            canonical_name=m["canonical_name"],
            entity_type=m["entity_type"],
            document_count=len(m["document_ids"]),
            segment_count=len(m["segment_ids"]),
        )
```

#### Step 2: 社区检测（简化版）

不引入 Leiden，先用简单的 K-means 在实体 embedding 上聚类：

```python
from sklearn.cluster import KMeans

def detect_communities(entity_embeddings, n_clusters=None):
    """简化社区检测。"""
    if n_clusters is None:
        n_clusters = max(2, len(entity_embeddings) // 10)  # 每 10 个实体一个社区
    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(entity_embeddings)
    return labels  # 每个实体的社区标签
```

后续可替换为 Leiden（需要 graspologic 或 networkx）。

#### Step 3: 社区摘要

对每个社区，收集所有实体描述和关系描述，用 LLM 生成摘要：

```
你是一个知识管理专家。以下是属于同一个主题集群的实体和它们之间的关系。

实体列表：
{entities_with_descriptions}

关系列表：
{relationships_with_facts}

请生成：
1. 集群标题（10字以内）
2. 集群摘要（200字以内）
3. 关键发现（3-5条）
```

### 7.4 改动文件

| 文件 | 改动 |
|------|------|
| `databases/asset_core/schemas/` | 新增 cross_document_entities、communities 建表 SQL |
| `knowledge_mining/mining/stages/publishing.py` | build 阶段新增跨文档聚合 + 社区检测 |
| `knowledge_mining/mining/infra/domain_pack.py` | RetrievalPolicy 新增 community_detection 开关 |

---

## EPIC-8: Pipeline 可组合化重构

### 8.1 当前问题

`MiningPipeline.process_document()` 中阶段顺序硬编码。`PipelineConfig` 用 dataclass 字段注入依赖，但无法灵活添加/删除/重排阶段。

### 8.2 工业做法

#### FalkorDB SDK 的 6 个 ABC 策略

```python
class RetrievalStrategy(ABC):
    async def search(self, query, ctx=None, **kwargs) -> RetrieverResult:
        self._validate(query)
        raw = await self._execute(query, ctx, **kwargs)  # 子类实现
        return self._format(raw)

    @abstractmethod
    async def _execute(self, query, ctx, **kwargs) -> RawSearchResult: ...
```

9 步摄取管线，其中 4 步基于策略可替换：Loader、Chunker、Extractor、Resolver。

#### Cognee 的 Task/Pipeline 模式

```python
@task(batch_size=20)
async def extract_graph(chunks, graph_model=None):
    ...

# 组合管线
await run_pipeline([
    classify_task(),
    extract_graph_task(graph_model=KnowledgeGraph),
], data=raw_input)
```

`run_pipeline()` 接收 `List[Task]`，按顺序流式处理。支持 `asyncio.Semaphore` 并发控制。

### 8.3 具体实现方案

#### Step 1: 定义 Stage Protocol

```python
# knowledge_mining/mining/contracts/protocols.py 新增

from typing import Protocol, runtime_checkable

@runtime_checkable
class PipelineStage(Protocol):
    """可组合的 pipeline 阶段。"""

    @property
    def stage_name(self) -> str: ...

    def __call__(self, ctx: DocumentContext, cfg: PipelineConfig) -> DocumentContext: ...
```

#### Step 2: 让现有阶段函数适配 Protocol

现有阶段函数（`parse_stage`, `segment_stage` 等）已经符合签名 `(ctx, cfg) -> ctx`，只需包装：

```python
@dataclass
class StageWrapper:
    """将函数包装为 PipelineStage。"""
    name: str
    fn: Callable[[DocumentContext, PipelineConfig], DocumentContext]

    @property
    def stage_name(self) -> str:
        return self.name

    def __call__(self, ctx: DocumentContext, cfg: PipelineConfig) -> DocumentContext:
        return self.fn(ctx, cfg)
```

#### Step 3: 可配置的阶段列表

```python
@dataclass
class PipelineConfig:
    # ... existing fields ...
    stages: list[PipelineStage] | None = None  # None = 默认阶段列表

    def get_stages(self) -> list[PipelineStage]:
        if self.stages is not None:
            return self.stages
        # 默认阶段列表
        return [
            StageWrapper("parse", lambda ctx, cfg: parse_stage(ctx, cfg)),
            StageWrapper("segment", lambda ctx, cfg: segment_stage(ctx, cfg)),
            StageWrapper("enrich", lambda ctx, cfg: enrich_stage(ctx, cfg)),
            StageWrapper("resolve", lambda ctx, cfg: resolve_stage(ctx, cfg)),  # 新增
            StageWrapper("discourse_relations", lambda ctx, cfg: discourse_stage(ctx, cfg)),
            StageWrapper("entity_relations", lambda ctx, cfg: entity_relations_stage(ctx, cfg)),  # 新增
            StageWrapper("build_retrieval_units", lambda ctx, cfg: retrieval_units_stage(ctx, cfg)),
        ]
```

#### Step 4: StreamingPipeline 使用可配置阶段

```python
# jobs/run.py 中构建 stages 列表
stages_for_streaming = []
for stage in cfg.get_stages():
    stages_for_streaming.append((stage.stage_name, stage, worker_count))

# embedding 和 db_write 保持固定
stages_for_streaming.append(("embedding", embedding_stage_fn, 4))
stages_for_streaming.append(("db_write", db_write_stage_fn, 1))
```

### 8.4 改动文件

| 文件 | 改动 |
|------|------|
| `knowledge_mining/mining/contracts/protocols.py` | 新增 PipelineStage Protocol |
| `knowledge_mining/mining/pipeline.py` | PipelineConfig 新增 stages 字段 |
| `knowledge_mining/mining/jobs/run.py` | 使用可配置阶段列表 |

---

## 附录 A: 工业系统参考矩阵

| 能力 | GraphRAG | Graphiti/Zep | LlamaIndex | FalkorDB | WhyHow | Mem0 | Cognee |
|------|---------|-------------|-----------|----------|--------|------|--------|
| Schema 约束提取 | ✅ entity types | ✅ specificity test | ✅ Literal + Pydantic | ✅ ontology prune | ✅ patterns | ❌ | ❌ |
| 实体归一化 | exact match | 3-tier cascade | graph store | 4 strategies | rule-based | embedding link | ID skip |
| 双向 chunk↔entity | text_unit_ids | MENTIONS edge | parent-child | MENTIONED_IN | triple→chunk | entity collection | DataPoint |
| 关系质量 | merge+summarize | fact + contradiction | schema prune | prune step | multi-agent | N/A | N/A |
| 层级检索 | community reports | 3-layer subgraph | AutoMerging | multi-path | N/A | N/A | N/A |
| 跨文档连接 | Leiden community | incremental resolve | graph index | shared graph | N/A | user-scope | shared store |
| Pipeline 可组合 | N/A | N/A | N/A | 6 ABC strategies | N/A | N/A | Task/Pipeline |

---

## 附录 B: 实施优先级与依赖关系

### 依赖图

```
EPIC-1 (Schema 实体定义)
  ├── EPIC-2 (实体归一化) — 依赖 EPIC-1 的 entity schema
  ├── EPIC-3 (双向链接) — 依赖 EPIC-1 的规范化 entity_refs
  └── EPIC-4 (Schema 关系) — 依赖 EPIC-1 的 entity types + EPIC-2 的归一化

EPIC-5 (Section 级 RST) — 独立，可并行

EPIC-6 (层级检索) — 依赖 EPIC-3 的 mentions

EPIC-7 (跨文档连接) — 依赖 EPIC-2 + EPIC-3

EPIC-8 (Pipeline 可组合) — 独立，可并行
```

### 推荐实施批次

| 批次 | EPIC | 预计工作量 | 前置依赖 |
|------|------|-----------|---------|
| **Batch 1** | EPIC-1: Schema 实体定义 | 3 天 | 无 |
| **Batch 2** | EPIC-2: 实体归一化（Tier 1 别名表） | 2 天 | EPIC-1 |
| **Batch 2** | EPIC-5: Section 级 RST | 3 天 | 无（并行） |
| **Batch 3** | EPIC-3: 双向链接 | 2 天 | EPIC-1 |
| **Batch 3** | EPIC-4: Schema 关系（纯规则模式） | 3 天 | EPIC-1, EPIC-2 |
| **Batch 4** | EPIC-8: Pipeline 可组合化 | 3 天 | 无（并行） |
| **Batch 5** | EPIC-6: 层级检索结构 | 3 天 | EPIC-3 |
| **Batch 6** | EPIC-7: 跨文档连接 | 5 天 | EPIC-2, EPIC-3 |

**总预计**：~26 天工作量，分 6 个批次。

### 核心原则

1. **每个 EPIC 可独立开发和测试**，通过 feature flag 控制
2. **Batch 1 是基础**，后续 EPIC 都依赖 schema 定义
3. **EPIC-5 和 EPIC-8 可并行**，与其他 EPIC 无依赖
4. **EPIC-7 是最复杂的**，建议放到最后

---

> 文档维护说明：本文档随开发进展持续更新。每个 EPIC 完成后在对应章节标注完成状态和实际改动文件。
