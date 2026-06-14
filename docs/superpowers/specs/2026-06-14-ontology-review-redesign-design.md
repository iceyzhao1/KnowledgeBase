# 本体评审（Gate）页面重新设计

> 状态：设计稿（已与用户确认，待写实现计划）
> 日期：2026-06-14
> 关联：L1 §12 / L2 §15 / L3 §10（本体归纳重排 N1–N5，已实施）

## 1. 背景与问题

N1–N5 落地后，挖掘流水线变成「先确认实体 → 机器归纳类型 → 再确认本体」的两检查点流程。但本体确认（当前叫 Gate1）这个评审页是按更早的「逃生口候选」长的，跟新的「归纳候选」字段对不上，用户在使用时遇到五个问题：

1. **编号反直觉**：先跑的实体确认却叫 Gate2，后跑的本体确认叫 Gate1。
2. **全都无证据**：归纳候选写库时没写"原文出处"那一栏，而评审页的证据区只认出处，于是一律显示无证据。其实候选自带成员实体、定义、示例，只是没展示。
3. **来源看不懂**：候选来源是英文 `global_induction`，前端中文翻译表没收录，原样吐英文。
4. **看不出与现有类型重复**：归纳时虽把现有类型清单给了 LLM 做软提醒，但仍可能产重名候选；评审页没有任何重复标记。
5. **热度列空白**：候选载荷存的是 `df`/`support`，前端热度列读的却是 `tf`/`df`，字段名错位 → 显示 `-`。

根因一句话：**后端归纳候选信息其实挺全（成员实体、定义、示例、文档/提及数），但前端评审页字段对不上，导致又是英文、又没证据、又看不出重复。**

## 2. 术语与命名规则（与用户对齐）

本体分两层，本次设计严格区分：

| 用户叫法 | 系统/数据库叫法 | 例子 | 哪个环节审 |
|---|---|---|---|
| node（节点 / 类别） | node_type（点类型） | 网络切片类 | 本体确认 |
| node_name | node_type.name（DB 列 `name`） | "网络切片类" | — |
| node_description | definition（DB 列 `definition`） | "逻辑独立的端到端网络资源" | — |
| 属性 | layer / is_strong / examples | 概念层·强·示例 | — |
| （具体实例） | entity（实体） | 网络切片、PFCP | 实体确认 |

**命名决策**：
- 界面与设计文档统一用用户命名：**node_name(名称) / node_description(描述) / 属性(层级·强弱·示例)**。
- **数据库列名不动**（`name`/`definition`/`examples_json`/`layer`/`is_strong`）——内部标识，永不展示给用户，重命名收益为零。
- 节点（类别）结构仅含**类型自身的元信息**，不引入「属性槽位 schema」（即不定义"该类实例必须有哪些属性字段"）——明确排除，避免范围膨胀。

## 3. 去掉 Gate 编号（彻底，含内部名）

用户选择 C：丢掉「Gate1/Gate2」数字编号，直接叫「实体确认 / 本体确认」，且内部名一并改干净（用户将重建数据库，存量值正好一次换净）。

| 位置 | 现在 | 改为 |
|---|---|---|
| 界面标题 | `Gate2 · 实体确认` | **实体确认** |
| 界面标题 | `Gate1 · 本体候选评审` | **本体确认** |
| `subloop_stage` 值 | `gate2_entity` | `entity_review` |
| `subloop_stage` 值 | `gate1_ontology` | `ontology_review` |
| 进度接口字段 | `gate1_proposed_candidates` | `ontology_proposed_candidates` |
| 进度接口字段 | `gate2_pending_mentions` | `entity_pending_mentions` |
| `active_gate` 取值 | `gate1_ontology`/`gate2_entity` | `ontology_review`/`entity_review` |
| 文档 L1/L2/L3 | Gate1/Gate2 措辞 | 实体确认/本体确认 |

**执行顺序不变**：实体确认在前、本体确认在后（这是 N4 已落地的逻辑，只改名不改序）。`_check_review_gate` 仍是「先查 pending mention → 再查 proposed candidate」，只是返回的字符串改名。

## 4. 后端改动（4 处）

### 4.1 归纳阶段补证据（ontology_induction）
`OntologyInductor.induce()` 写候选时，把每个成员实体的结构化信息存进候选载荷 `payload.members`：
```
members: [{entity_id, name, document_count, mention_count, quote}]
```
这些数据在归纳时本就拿在手里（`confirmed_untyped_entities` 已返回成员的 quote/segment + 实体的 doc/mention 计数），现在只是在 `_build_type_candidates` 里组装成结构化成员并落库。`quote` 取该成员任一条提及的原文摘录（截断 ~120 字）。

> 快照时机说明：归纳跑在实体确认之后、本体确认之前，其间无人工改动这些实体，故归纳时存的成员快照对评审而言足够新鲜，无需评审时再回查 DB。

### 4.2 重复检测纯函数（可单测）
新增纯函数（放 `ontology_store` 模块——判重接口 `list_candidates` 在此、且需现有类型），签名约：
```
find_duplicate_type(proposed_name: str, existing_types: list[dict]) -> str | None
```
判定规则（用户选「名字 + 别名/包含匹配」，点类型无别名字段，以"示例"代偿）：
1. 与现有类型 `name` 大小写/空白不敏感**完全相同** → 命中；
2. 提议名与现有类型 `name` **双向子串包含**（"切片类"含"切片"）→ 命中；
3. 提议名命中现有类型的某个 **example** → 命中。
命中返回该现有类型名，否则 `None`。

### 4.3 候选列表接口标注（/ontology/candidates）
`list_ontology_candidates` 返回每条 node_type 候选时附加 `duplicate_of` 字段：读取当前 active 本体的现有类型，对每条候选调 4.2 的纯函数计算。relation_type 候选暂不判重（边结构后续再定义）。

### 4.4 改名落地
`subloop_stage`/`active_gate`/两个 Gate 计数字段按 §3 重命名，连带：
- `knowledge_mining/mining/jobs/run.py`：`_check_review_gate`、`_has_pending_mentions`/`_has_proposed_candidates` 返回值、resume/first-run 编排里的字符串、`_run_pipeline` 的 `_pause(gate)` 调用。
- `knowledge_mining/mining/api/routes/runs.py`：进度接口返回字段名。
- 数据库建库脚本（PG + SQLite）：`subloop_stage` 的 CHECK 约束允许值同改。

## 5. 前端「本体确认」页（OntologyReviewView.vue）

- 标题 → **本体确认**；副标题去掉「Gate1」。
- 来源中文表补 `global_induction → 全局归纳`（保留 escape_hatch→逃生口 / seed→种子 / manual→人工）。
- 热度列读 `payload.df`（篇）/ `payload.support`（次），显示「X 篇 / Y 次」——修字段错位。
- 折叠行加红色徽标「⚠ 疑似重复：{duplicate_of}」（仅当 `row.duplicate_of` 非空）。
- 展开区改成 **B 小表式**：
  - 顶部：`描述`（definition）+ `属性`（层级 / 强弱 / 示例）。
  - 下方一张表：列「成员实体 | 热度（X篇/Y次）| 原文摘录」，数据来自 `payload.members`。

## 6. 其余前端（改名为主）

- `MentionReviewView.vue`：标题 → **实体确认**；N2 已做的功能（暂无类型 chip、建议类型、加粗提及）不动。
- `RunDetailView.vue`：`active_gate === 'gate2_entity'/'gate1_ontology'` 判断与展示文案改用新值新名；待办数读新字段。
- `router/index.ts`、`api/mining.ts`：注释与字段对齐。
- `types/index.ts`：`gate1_proposed_candidates`/`gate2_pending_mentions` 字段改名。

## 7. 测试与文档

- 后端为 4.2 重复检测纯函数加单测（仿 `test_review_gate.py` 的纯函数 + 假 store 路子）：覆盖完全重名、双向包含、命中示例、无重复四种。
- 为 4.1 归纳补证据加断言：候选 `payload.members` 含 entity_id/name/计数/quote。
- 改名涉及的现有测试（`test_review_gate.py` 里 gate 字符串）同步更新。
- 更新 L1 §12 / L2 §15 / L3 §10 中的 Gate1/Gate2 措辞为「实体确认/本体确认」。

## 8. 明确不做（YAGNI）

- 不引入属性槽位 schema（per-class property definitions）。
- 不做语义/向量重复检测（仅名字+包含+示例）。
- 不在评审页编辑 definition/layer/examples（保持 accept/改名/reject 三动作）。
- 边类型（relation_type）结构与判重不在本次范围，后续单独定义。

## 9. 影响文件清单

**后端**
- `knowledge_mining/mining/stages/ontology_induction/__init__.py`（补证据 + 可能放重复检测纯函数）
- `knowledge_mining/mining/infra/ontology_store.py`（重复检测纯函数 / list_candidates 标注）
- `knowledge_mining/mining/api/routes/ontology.py`（候选接口加 duplicate_of）
- `knowledge_mining/mining/api/routes/runs.py`（进度接口字段改名）
- `knowledge_mining/mining/jobs/run.py`（subloop_stage/active_gate 字符串改名）
- 建库脚本（PG + SQLite）：subloop_stage CHECK 约束
- `knowledge_mining/tests/test_review_gate.py` 等

**前端**
- `kb-ui/src/views/knowledge/OntologyReviewView.vue`
- `kb-ui/src/views/knowledge/MentionReviewView.vue`
- `kb-ui/src/views/mining/RunDetailView.vue`
- `kb-ui/src/router/index.ts`、`kb-ui/src/api/mining.ts`、`kb-ui/src/types/index.ts`

**文档**
- `docs/plans/ontology/ontology-L1-solution-design.md` / `-L2-impl-design.md` / `-L3-impl-plan.md`
