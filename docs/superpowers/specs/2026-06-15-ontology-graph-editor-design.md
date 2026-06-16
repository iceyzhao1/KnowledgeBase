# 本体图谱编辑器 设计文档

> 状态：已与用户确认方向，待用户复核本文档后转入实现计划。

**日期：** 2026-06-15
**作者：** 协作设计（用户 + Claude）
**关联前序：** `2026-06-15-ontology-graph-page-design.md`（只读图谱页，本功能在其基础上扩展为可编辑）

---

## 1. 目标（Goal）

把本体图谱页从"只读"升级为"可编辑 + 草稿/发布"：支持人工新建/删除边、新建/删除节点、新建关系类型；编辑后可**保存草稿**，再**发布版本**生效。同时修复"节点装不下名字"的渲染问题。

## 2. 背景与现状

- 本体图谱页 `OntologyGraphView.vue` 当前为**只读**（顶部有"只读"徽标），边来自 active 版本的 `relation_types.allowed_pairs_json`（人写的"允许的头→尾配对"）。
- 已有版本机制：`ontology_versions` 有 `draft / active / superseded` 三态；`OntologyStore` 已具备 `create_version`、`activate_version`、`add_node_type`、`add_relation_type`、`next_version_no`、`active_node_types/relation_types/version` 等原语；`promote_accepted_candidates` 已示范"克隆旧 active → 新版本 → 激活"的完整套路。
- 已有 LLM 候选审核流程（Gate2）：`relation_type`/`node_type` 候选 → 『本体确认』页 review → promote 升版。候选边在图谱上已能渲染成橙色虚线（`showCandidates` 开关）。

## 3. 核心决策（已确认）

| 议题 | 决定 |
| --- | --- |
| 人工编辑 vs LLM 候选 | **两条线分开**：人工直接编辑走"草稿→发布"，不走审核；LLM 候选仍在『本体确认』页审核后升版。 |
| 保存/发布模型 | **草稿版 → 发布激活**：编辑改草稿，线上 active 不变；保存把草稿存下；发布把草稿激活为新 active，旧版转 superseded。 |
| 编辑范围 | 新建边、删除边、新建/删除节点、新建关系类型（全套）。 |
| 编辑交互 | 右侧面板表单 + 图上点选；**不做画布拖拽连线**（ECharts 力导图不适合当编辑画布）。 |
| 保存粒度 | **整份覆盖式保存**：编辑全程在浏览器本地进行，"保存草稿"一次性把整份本体提交，后端整份覆盖该领域 draft。 |

## 4. 架构与数据流

```
进入编辑 ── 克隆 active 内容 → 浏览器"可编辑副本"（本地 reactive 模型）
   │
   ├─ 改（加/删边、加/删点、加关系类型）→ 仅改本地副本 → 图谱即时重画（线上不动）
   │
   ├─ 保存草稿 ── PUT /api/ontology/draft（整份）→ 后端 get-or-create draft 版本 → 清空其旧类型 → 按提交内容重写
   │
   └─ 发布版本 ── POST /api/ontology/draft/publish → 激活 draft 为新 active（旧 active → superseded）
```

进入编辑时优先加载已存在的 draft（可"接着改"），无 draft 则从 active 克隆。增删点/边的"删"通过覆盖式保存天然实现（本地删掉、保存时就没了）。

## 5. 组件与职责

### 5.1 前端

**`OntologyGraphView.vue`（改）**
- 顶部"只读"徽标旁加 **「编辑」开关**。进入编辑态：
  - 顶部出现 `保存草稿` / `发布版本` / `退出编辑` 按钮。
  - 右侧面板从"只读详情"切换为"编辑面板"。
- 维护本地可编辑模型 `draft = { node_types[], relation_types[] }`（reactive），所有编辑改它；图谱通过 `buildOntologyGraph` 从该模型渲染。
- 轮询行为：编辑态下**暂停轮询刷新**（避免本地编辑被远端数据覆盖）；退出编辑态恢复（沿用上次修复的"指纹比对跳过重画"）。

**编辑面板（`OntologyGraphView.vue` 内或拆子组件）**
- **新建边**：关系类型下拉（现有关系类型 + "+ 新建关系类型"）+ 头节点下拉 + 尾节点下拉 → 添加一条 pair。
- **删除边**：点中一条边 → 面板显示"删除此边"。
- **新建节点**：表单（名称、层 concept/instance/property、强/弱）。
- **删除节点**：点中节点 → "删除此节点"（连带删除以它为端点的全部 pair；删前 `ElMessageBox` 确认）。
- **新建关系类型**：表单（关系名、有向/无向、反向名、定义）。
- 校验：名称非空、不重名；边的头/尾节点必须存在；删除节点时提示将连带删除 N 条边。

**`OntologyDiGraph.vue`（改）**
- **标签外移修复**：节点改用较小圆点（symbol/symbolSize 缩小），名字标签移到节点外侧（`label.position: 'bottom'`，`overflow` 不裁切），长中文名不再被裁。候选/层配色不变。
- 编辑态：保留 `node-click` / `edge-click` 事件供面板点选删除。

**`ontologyGraph.ts`（改/扩展）**
- 现有 `buildOntologyGraph(active, candidates, showCandidates)` 复用：把本地可编辑模型当作 `active` 传入即可渲染。
- 新增本地模型的增删辅助函数（纯函数，便于测试）：`addEdge`、`removeEdge`、`addNode`、`removeNode`（连带删 pair）、`addRelationType`。

**`api/mining.ts` + `types/index.ts`（改）**
- 新增 API 方法：`getOntologyDraft(domain)`、`saveOntologyDraft(domain, payload)`、`publishOntologyDraft(domain)`。
- 复用现有 `OntologyNodeType` / `OntologyRelationType` 类型描述提交载荷。

### 5.2 后端

**`routes/ontology.py`（新增 3 个路由）**
1. `GET /api/ontology/draft?domain=` → 取该领域 draft 版本及其点/边类型（无则 `{version: null, node_types: [], relation_types: []}`）。
2. `PUT /api/ontology/draft` → body `{domain, node_types[], relation_types[]}`，整份覆盖该领域 draft。返回 `{domain, draft_version_id}`。
3. `POST /api/ontology/draft/publish` → body `{domain}`，激活 draft 为新 active。无 draft → 400。返回 `{domain, new_version_id}`。

**`infra/ontology_store.py`（新增原语）**
- `get_draft_version(domain_id) -> dict | None`：取该领域 status='draft' 的版本（约定每领域至多一个 draft）。
- `node_types_for_version(version_id)` / `relation_types_for_version(version_id)`：按 version_id 读类型（现有 `active_*` 只按 active 读，需补按 id 读）。
- `replace_draft(domain_id, node_types, relation_types, *, created_by=None) -> str`：事务内 get-or-create draft（无则 `next_version_no` + `create_version(status='draft', source='human_edit')`）→ 删除该 draft 旧的点/边类型 → 用 `add_node_type`/`add_relation_type` 重写 → 返回 draft 版本 id。
- `delete_version_types(version_id)`：删一个版本名下全部点/边类型（供 replace 复用）。
- `publish_draft(domain_id) -> str | None`：取 draft → `activate_version` → 返回新 active id；无 draft 返回 None（路由转 400）。

> 所有写操作用 `with self.transaction():` 包裹，保证"清空+重写"原子。

## 6. 测试

- **后端**（`knowledge_mining/tests/`，沿用 `_Fake*Store` 假对象只覆盖 `_execute/_fetchone/_fetchall` 的风格）：
  - `replace_draft`：无 draft 时新建 draft 版本 + 写入类型；有 draft 时清空旧类型再重写（断言发出 delete + insert 的 SQL 与参数）。
  - `publish_draft`：有 draft → 调 `activate_version`；无 draft → 返回 None。
  - `get_draft_version` / `*_for_version`：SQL 拼装与参数正确。
- **前端**（`ontologyGraph.ts` 纯函数）：`addEdge`/`removeEdge`/`addNode`/`removeNode`（连带删 pair）/`addRelationType` 的输入输出。
- **类型与构建**：`vue-tsc --noEmit` 退出码 0。

## 7. 影响与边界

- 编辑器只管**人工直接编辑**；LLM 候选审核仍走『本体确认』页，互不干扰。
- **已知限制（本次不处理）**：编辑器"发布"与"候选 promote"都会升版；单用户顺序操作无冲突，两边同时改属极端并发，本次按"后写覆盖"处理，不额外加锁。
- 别名词典（`ontology_alias_dictionary`）不在本次编辑范围。
- 不做画布拖拽连线；增删一律走右侧面板表单 + 图上点选。

## 8. 涉及文件清单

- 前端：`kb-ui/src/views/knowledge/OntologyGraphView.vue`、`kb-ui/src/components/charts/OntologyDiGraph.vue`、`kb-ui/src/views/knowledge/ontologyGraph.ts`、`kb-ui/src/api/mining.ts`、`kb-ui/src/types/index.ts`
- 后端：`knowledge_mining/mining/api/routes/ontology.py`、`knowledge_mining/mining/infra/ontology_store.py`
- 测试：`knowledge_mining/tests/`（draft store 相关）、前端纯函数测试（如项目已有前端测试设施则加；否则以 vue-tsc + 手动验证为准）
