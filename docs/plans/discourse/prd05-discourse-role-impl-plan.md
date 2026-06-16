# PRD-05 话语角色传递（Mining 侧）实施计划

> 范围：仅 Mining 侧。Serving 侧（ContextAssembler / EvidenceRoleClassifier 利用 discourse_role）不在本计划内。
> 目标：新挖掘的每个 segment 在 `asset_raw_segments.metadata_json` 中携带 `discourse_role`（`nucleus` / `satellite` / `standalone`），供下游 serving 读取。

## 一、背景与现状（写给接手者）

- RST 15 种关系已在跑：`pipeline.py` 的 **Stage 4b（discourse_relations）** 由 `DiscourseRelationBuilder.build()`（`knowledge_mining/mining/stages/relations/__init__.py`）用 LLM 滑动窗口产出，结果是 **segment 之间的有向边**，类型为 `SegmentRelationData`，最终落 `asset_raw_segment_relations` 表。
- RST 关系定义在 `knowledge_mining/mining/contracts/rst_relations.py`，`RST_RELATIONS` 字典每项是 `(小写DB值, 中文语义描述)`，**但没有 nucleus/satellite 方向信息**。
- segment 模型 `RawSegmentData`（`contracts/models.py`）是 **frozen dataclass**，有自由字段 `metadata_json`（DB 中 PG 为 JSONB / SQLite 为 TEXT，默认 `{}`）；并提供 `segment_key` property = `"{document_key}#{segment_index}"`。当前 enrich 阶段已用同样方式往 `metadata_json` 写 `content_assessment`、`llm_document_type`。
- `SegmentRelationData` 字段：`source_segment_key`、`target_segment_key`、`relation_type`、`weight`、`confidence`、`distance`、`metadata_json`。
- 落库在 `pipeline.py` 的 `db_write_stage`，对每个 segment 调 `AssetCoreDB.insert_raw_segment(..., metadata_json=seg.metadata_json)`（`infra/db.py`），整库写入是流水线最后一步。
- 全仓当前 **零 `discourse_role` / `nucleus` / `satellite` 代码**（已用 grep 确认），本计划是从零新增。

### 关键时序结论（决定方案）

PRD 原文写"在 enrich 阶段添加 discourse_role"，但 **enrich 是 Stage 3，RST 边要到 Stage 4b 才存在**——enrich 时无边可推。
**因此 discourse_role 的计算必须放在 Stage 4b 之后、`db_write_stage` 之前。** 本计划据此设计，不在 enrich 做。

## 二、方案总览

三个改动点 + 测试：

1. **rst_relations.py**：新增"核性方向表"`RST_NUCLEARITY`，标注每种关系是多核还是单核、单核时前段/后段哪端是 nucleus。
2. **新建纯函数 `infer_discourse_roles()`**（放 `stages/relations/__init__.py`）：输入 segments + relations，输出 `{segment_key: role}`，含聚合规则。
3. **discourse 步骤尾部回写**：把 role 写入每个 segment 的 `metadata_json["discourse_role"]`，用 `dataclasses.replace` 重建 frozen segment，更新 `ctx.segments`。改两处——生产用的 `discourse_stage`（主）与孪生 `MiningPipeline.process_document` Stage 4b。
4. **测试**：方向表/聚合纯函数单测 + 一个端到端断言（新 segment 含 `discourse_role`）。

> 不需要数据库迁移：复用 `metadata_json` 自由字段。这直接满足验收项"不影响已有检索性能"。

## 三、详细任务

### 任务 1：定义 RST 核性方向表（rst_relations.py）

在 `RST_RELATIONS` 之后新增映射。每种关系归为：
- `MULTINUCLEAR`：两端都是 nucleus（对比/并列/顺序类）。
- `MONO_PREV`：单核，**前段**（segment_index 较小）为 nucleus，后段为 satellite。
- `MONO_POST`：单核，**后段**（segment_index 较大）为 nucleus，前段为 satellite。

> 方向不依赖 LLM 输出的 source/target 标签（那由远程模板约定、文件系统里查不到），而是用 `SegmentRelationData` 两端 key 里携带的 `segment_index` 比较前后，再套语义。这样更稳健、可在本地单测验证。

**默认映射（按 RST_RELATIONS 中文语义推导，⚠ 标注的需与 serving 端确认 nucleus 优先方向）：**

| 关系 | 语义 | 核性 | nucleus 端 |
|------|------|------|-----------|
| elaborates | 后段详述前段 | MONO_PREV | 前段（被详述者） |
| exemplifies | 后段是前段的例子 | MONO_PREV | 前段 |
| evidences | 后段为前段提供证据 | MONO_PREV | 前段（论断） |
| justifies | 后段解释前段理由 | MONO_PREV | 前段 |
| backgrounds | 前段为后段提供背景 | MONO_POST | 后段 |
| conditions | 前段是后段触发条件 | MONO_POST | 后段 |
| purposes | 后段说明前段操作目的 | MONO_PREV | 前段（操作本身） |
| results_in | 前段操作产生后段结果 | MONO_PREV | 前段（操作）⚠ |
| enables | 前段机制使后段可能 | MONO_POST | 后段（能力）⚠ |
| causes | 前段是后段的原因 | MONO_POST | 后段（结果/现象）⚠ |
| concedes | 承认一方强调另一方 | MONO_POST | 后段（被强调方）⚠ |
| summarizes | 后段概括前段 | MULTINUCLEAR | 两端 ⚠ |
| sequences | 顺序排列 | MULTINUCLEAR | 两端 |
| contrasts_with | 对比/转折 | MULTINUCLEAR | 两端 |
| parallels | 并列功能/流程 | MULTINUCLEAR | 两端 |

> ⚠ 行是语义上 nucleus 方向有歧义、且直接影响 serving 排序的关系。建议实现首步与 serving 端（ContextAssembler nucleus 优先策略）对齐一次再定稿。其余行可直接采用默认。

**验收**：`RST_NUCLEARITY` 覆盖全部 15 种关系，键集合与 `RST_DB_VALUES` 完全一致（加一行单测断言两集合相等，防止以后加关系漏配）。

### 任务 2：实现 `infer_discourse_roles()` 纯函数

位置：`stages/relations/__init__.py`（与 `build_seg_ids` 并列，便于复用 `_make_segment_key`）。

签名（建议）：
```python
def infer_discourse_roles(
    segments: list[RawSegmentData],
    relations: list[SegmentRelationData],
) -> dict[str, str]:
    """返回 {segment_key: 'nucleus'|'satellite'|'standalone'}"""
```

逻辑：
1. 初始化每个 segment 的 key → 角色证据（默认无参与）。
2. 遍历 `relations`：
   - 跳过 `relation_type` 不在 `RST_NUCLEARITY` 的（结构边如 previous/next/references 不算话语角色）。
   - 用两端 key 里的 `segment_index` 判前后；按核性表给两端各打 `nucleus` 或 `satellite` 标记。
   - MULTINUCLEAR：两端都记 `nucleus`。
3. **聚合规则**：某 segment 只要在任一关系里被标过 `nucleus` → `nucleus`；否则若被标过 `satellite` → `satellite`；没参与任何 RST 边 → `standalone`。
4. 返回映射。

要点：
- 纯函数、无 IO，便于单测。
- 从 `segment_key`（`"doc#idx"`）解析 index 时用 `rsplit("#", 1)`，避免 document_key 内含 `#` 时拆错。
- 不读 confidence 加权（保持第一版简单）；如需按 confidence 决胜，留 TODO，不在本版实现（遵循"不为假想需求设计"）。

**验收**：单测覆盖——单核前核、单核后核、多核、一个 segment 既当 nucleus 又当 satellite（结果应为 nucleus）、孤立 segment（standalone）。

### 任务 3：discourse 步骤尾部回写 metadata_json

**重要——已读真实代码核实（`pipeline.py` 736 行 + `jobs/run.py`）：项目有两条流水线，且生产用的是 `StreamingPipeline`。**

- **生产实际跑的是 `StreamingPipeline`**：`run.py:942-943` 用 `StreamingPipeline(stages).process_all(...)`。其 discourse 步骤是 **`discourse_stage(ctx, cfg)`（`pipeline.py:450-458`）**，由 `run.py:935` 挂入，worker 数 `min(max_workers, 2)`。
- `MiningPipeline.process_document`（`pipeline.py:130-235`）是**顺序版孪生**，Stage 4b 在 `pipeline.py:211-218`，run.py 主路径**不用它**（疑似测试/备用）。
- **两处的 discourse 逻辑几乎相同**：都调 `drb.build(list(ctx.segments), seg_ids=ctx.seg_ids)`，把 RST 边存进 **`ctx.relations`**（注意字段名是 `relations`，DocumentContext 无 `discourse_relations` 字段）。

**关键校正**：RST 边在 `ctx.relations`。`db_write_stage`（`pipeline.py:506`）正是从 `ctx.segments`（行 546）+ `ctx.relations`（行 547）落库。所以 discourse_role 必须在 db_write **之前**写进 `ctx.segments`——两条编排都是 discourse → retrieval_units → db_write，在 discourse 步骤尾部回写正合适。

改动（**两处都要改**，主改 `discourse_stage`，孪生 `process_document` 同步以防分叉）：
1. 在算出 `discourse_relations` 之后，调 `roles = infer_discourse_roles(list(ctx.segments), discourse_relations)`。
2. 重建 segments：对每个 seg，复制 `metadata_json`，写入 `meta["discourse_role"] = roles.get(seg.segment_key, "standalone")`，用 `dataclasses.replace(seg, metadata_json=meta)` 生成新对象（frozen 安全做法，与 enrich `_apply_llm_result` 一致）。
3. 同时更新两个字段：`return ctx.with_updates(relations=tuple(discourse_relations), segments=tuple(new_segments))`（`ctx.segments`/`ctx.relations` 都是 tuple，重建后转回 tuple）。
4. 无 discourse 边时（`discourse_relations` 为空）：仍应把所有 segment 标 `standalone` 再回写，保证"每个新 segment 都含 discourse_role"的验收成立。

> 落库无需改动：`db_write_stage` 已透传 `seg.metadata_json`，`discourse_role` 自动随之入库。
> 线程安全：`discourse_stage` 虽有 ≤2 worker，但每个 worker 处理不同文档的独立 `DocumentContext`，role 推断只读当前 ctx 的 segments+relations，无共享状态，安全。

**验收**：跑一篇含多段的文档，落库后 `asset_raw_segments.metadata_json` 每行都含 `discourse_role`。

### 任务 4：测试

- `tests/` 下新增 `test_discourse_role.py`：
  - 任务 1 的键集合一致性断言（`set(RST_NUCLEARITY) == RST_DB_VALUES`）。
  - 任务 2 的纯函数全分支（见上）。
- 端到端：在现有 pipeline 测试（参考 `tests/test_pipeline_operators.py` 风格）里加一条：构造 segments + 模拟 relations，跑到 segments 重建后断言每个 seg `metadata_json["discourse_role"]` 存在且取值合法。
- 回归：确认 `test_review_gate.py` 等现有测试不受影响。

> 测试遵循仓库现有 conftest（`tests/conftest.py`）的 mock LLM 约定，不要真连远程 LLM。

## 四、验收标准对照（PRD 原文）

| PRD 验收项 | 本计划对应 |
|-----------|-----------|
| 新挖掘的 segment 包含 `discourse_role` 字段 | 任务 3 + 任务 4 端到端断言 |
| 不影响已有检索性能 | 复用 JSONB 自由字段、无迁移、纯增量元数据；回归测试 |
| Serving 返回 nucleus 平均排序高于 satellite | ⚠ 属 Serving 侧，不在本计划；本计划只保证数据正确产出 |

## 五、实现首步必须先确认的决策点

1. **方向表 ⚠ 行的 nucleus 方向**：与 serving 端 ContextAssembler 的"nucleus 优先"语义对齐后定稿（见任务 1 表）。
2. **是否覆盖结构边**：本计划只用 RST 15 种话语关系推角色，结构边（previous/next/same_section/references）**不参与**。若 serving 希望 standalone 段也细分，再议——本版不做。
3. **走哪条流水线（已核实，无需再确认）**：生产线上跑 **`StreamingPipeline`**（`run.py:942` `process_all`），discourse 步骤是 `discourse_stage`（`pipeline.py:450-458`）——**这是必改的主目标**。`MiningPipeline.process_document`（Stage 4b，`pipeline.py:211-218`）是顺序版孪生、主路径不用，但应同步修改以防分叉。测试优先覆盖 `discourse_stage` 路径。

## 六、不做的事（划清边界）

- 不改数据库 schema、不做迁移。
- 不动 enrich 阶段。
- 不实现 confidence 加权决胜（留 TODO）。
- 不碰 Serving / PRD-06。
