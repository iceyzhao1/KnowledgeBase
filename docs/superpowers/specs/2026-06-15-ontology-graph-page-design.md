# 本体图谱页（功能③）设计稿

> 状态：已与用户确认（2026-06-15）。下一步可据此写实现计划（writing-plans）。
> 范围：仅功能③ —— 一个**只读的、实时刷新的**本体图谱可视化页。
> 不含：功能①（实体图谱页，单独设计稿 2026-06-15-entity-graph-page-design.md）。

## 1. 目标与背景

把现有 [OntologyView.vue](../../../kb-ui/src/views/knowledge/OntologyView.vue)（节点平铺成卡片、关系平铺成标签、**没有图**）升级成一张**真图谱**：
- **节点** = 本体类型（node_type）；
- **箭头** = 关系类型（relation_type），从头类型指向尾类型。

**只读**：单击节点/关系看定义、示例、连接约束；不在本页增删改本体。

### 关键事实（决定了整个方案，与功能①根本不同）

- **本体图谱的边是人写的，不是算出来的。** `ontology_relation_types.allowed_pairs_json`
  存 `[{head: 类型名, tail: 类型名}, ...]`，明确记着每条关系连哪两类节点 → 本体本身就是一张**有类型约束的真图**。
- 关系类型来源：**引种 YAML** + **人在关里审过的候选**（ontology_induction 只产 node_type 候选，不产 relation_type；
  off-schema 关系作为候选交人审）。**因此本体图谱没有 NPMI 重算这回事**——这是与功能①最大的区别。
- 前端 TS 类型 `OntologyRelationType` 里**早就有** `allowed_pairs_json`，当前 UI 只是没把它画成图。

## 2. 用户已拍定的范围决策

- **只看，不改**（暂时不可在图上修改本体；增删改仍走现有引种 / 审候选流程）。
- 因为只读 → **无版本变更、无下游连锁**（不存在"改本体类型 → 已打标实体怎么办"的问题，那是未来若开放编辑才需要）。

## 3. 图上画什么

### 3.1 节点（来自 active node_types）
- 形状：圆角矩形，按 **layer** 上色（concept 蓝 / instance 绿 / property 紫）。
- 标注：类型名、层标签、强/弱（is_strong）。
- 单击 → 右侧只读面板：definition、examples（examples_json）、**出边/入边**列表（该类型作为 head / tail 参与了哪些关系）。

### 3.2 边（来自 active relation_types）
- 从 `allowed_pairs_json` 的每个 `{head, tail}` 画一条有向箭头（is_directed=false 则无箭头）。
- **一个关系类型可能有多对 head/tail → 画成多条同名边。**
- 标签 = 关系名。单击 → 右侧只读面板：head→tail、有向/无向、inverse_name、definition。

### 3.3 候选叠加层（"实时"的核心价值）
- 一个「显示待审候选」开关（默认关）。
- 打开后：把挖掘新 induce 出、仍在关里**待人审**的 node_type / relation_type 候选，用**橙色虚线**叠在图上。
  - 节点候选：虚线边框矩形。
  - 关系候选：虚线箭头（payload 里有 head_type / tail_type）。
- 这样人能看着本体随挖掘一点点长出来；但确认/驳回仍在审候选页做，本页不做评审动作。

## 4. "实时"实现

- **轮询刷新**：页面打开期间每隔数秒拉一次 active（+ 候选），或窗口重新聚焦时刷新。**不用 websocket。**
- 刷新时局部更新图（新节点/边淡入，消失的淡出），不整页重渲染抖动。
- 顶部「● 实时」指示 + 「只读」标记。

## 5. 数据来源（全部复用现有只读接口）

| 用途 | 接口 | 说明 |
|------|------|------|
| active 本体 | `GET /api/ontology/active` | 已返回 node_types（name/layer/is_strong/definition/examples_json）+ relation_types（name/is_directed/inverse_name/allowed_pairs_json/definition）。**够用，无需改。** |
| 待审候选 | 现有候选列表接口（kind=node_type/relation_type）| 关系候选 payload 含 head_type/tail_type。若无现成"列待审候选"端点，补一个只读列表端点。 |

前端 TS 类型 `ActiveOntology` / `OntologyNodeType` / `OntologyRelationType`（含 allowed_pairs_json）已具备，基本无需改类型。

## 6. 要新写的东西

1. **前端图视图**（新 Vue 组件 / 改造 OntologyView）：
   - 由 node_types 生成节点、由 relation_types 的 allowed_pairs 生成边；
   - 布局用力导向（d3-force）或分层（dagre/elk）——实现计划里定具体库；
   - 右侧只读详情面板（节点 / 关系两种）。
2. **候选叠加层** + 显隐开关（拉候选、橙色虚线渲染）。
3. **轮询刷新**做"实时"（含局部增量更新、聚焦刷新）。
4. **后端**：基本不用新端点；仅当"列待审候选"无现成只读接口时补一个。

## 7. 非目标
- 图上不增删改本体（仍走引种 / 审候选流程）。
- 无版本变更、无下游实体连锁（只读前提下不存在）。
- 无 NPMI / 边重算（本体边是人写的）。
- 不做功能①（实体图谱，独立稿）。

## 8. 验收要点
- 打开页面：active 的全部 node_types 成节点、全部 relation_types 按 allowed_pairs 成有向边，颜色/强弱/方向正确。
- 单击节点/关系：右侧正确显示定义、示例、连接约束、出入边。
- 开「显示待审候选」：待审 node_type/relation_type 候选以橙色虚线出现；关掉则隐藏。
- 实时：跑挖掘产生新候选后，页面在数秒内自动反映（开候选叠加时可见新虚线节点/边）。
