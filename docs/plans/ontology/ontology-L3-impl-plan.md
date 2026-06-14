# 本体概念层 MVP — 实施计划 〔L3 · 落地〕

> 日期：2026-06-08（2026-06-09 重构为 L3 实施计划）
> 状态：实施计划（按啥顺序做、做到什么算完）
> 范围：knowledge_mining（挖掘侧）· databases（PG）· kb-ui（前端）· agent_serving（检索侧）· 单领域 cloud_core_network
> 上游：`docs/plans/ontology/ontology-L2-impl-design.md`（L2 蓝图，"每批要实现成什么样"的依据）

---

## 0. 文档体系（本文在 L3）

本体设计文档分三层，从宏观到微观逐层收敛，**本文是最下层 L3（怎么落地）**：

| 层 | 文档 | 本文与它的关系 |
|---|---|---|
| **L1 北极星·方案** | `docs/plans/ontology/ontology-L1-solution-design.md` | 最上游，定"做什么/为什么"。本文落地它 §11.3 留给 L3 的三个待定项 |
| **L2 实现设计·蓝图** | `docs/plans/ontology/ontology-L2-impl-design.md` | 直接上游，定"怎么设计才能实现"（表字段/阶段契约/接口/质量门槛/打分）。本文每个批次都**引用 L2 的 §** 作为实现依据 |
| **L3 实施计划·落地**（本文） | `docs/plans/ontology/ontology-L3-impl-plan.md` | 写**分批次、依赖链、文件改动、kb-ui 流程、暂停/恢复、每批验收**。不重复 L2 的 DDL / 指标 / 迁图分析 |

**阅读约定**：本文出现 `〔依据 L2 §x〕` 表示"按 L2 该节的设计去实现"。本文不抄表字段和算法口径——那些在 L2；本文只回答**先做什么、按什么顺序、做到什么算完**。

> 一句话：**L1 说去哪，L2 画图纸，L3 排工期。**

---

## 1. 本期目标与边界

**一句话目标**：用户在 kb-ui 上传一篇 cloud_core_network 文档 → 挖掘流水线按当前本体抽出**概念对象 + 概念关系**、消歧（人拍板）、落成带出处的领域知识图 → 全程在前端透明可见、两道人审 Gate 异常触发 → 跑通 §2.2 的三主题代表性子集。

边界（MVP 取值）见 **L2 §1**，不在此重复。一句话收口：**只做概念层、只做 cloud_core_network、全 PostgreSQL、图走薄接口、消歧人拍板。**

---

## 2. 首波三个决策定稿（结掉 L1 §11.3 的待定项）

L1 §11.3 把三个"第一波具体怎么落"的取舍下沉到 L3 定稿。现定如下：

### 2.1 概念层关系类型：先启用 4 种

| 关系 | head → tail | 为什么首波要它 |
|---|---|---|
| `connects_to` | network_element → interface / network_element | 拓扑骨架。多跳查询"SMF 涉及哪些接口"靠它 |
| `uses_protocol` | interface / network_element → protocol | 回答"对应协议"。和 connects_to 串起来正好支撑 L1 §1.1 锚点查询 |
| `part_of` | 任意 → 任意（同层） | 建组成/从属层级（UPF part_of 5GC） |
| `is_a` | 任意 → 任意（同类型族） | 类属，连"具体对象 ↔ 类型"（PFCP is_a 协议） |

- **后置（首波不启用）**：`has_policy` / `provides_service` / `validated_by` —— 语义偏机制/方法层，等那几层上线才有落点；`instance_of` / `belongs_to` —— 与 `is_a` / `part_of` 语义重叠，早期同时上会制造歧义，先合并。
- 这 4 种就是 **L2 §6.3 / §4.1 种子文件**里写死的 `relation_types`，本文与之一致。

### 2.2 代表性子集：三个主题

- **主题**：`5G核心网基础` + `SMF会话管理功能` + `UPF用户面功能`，从 `data/knowledge_base` 选取。
- **为什么是这三个**：
  - `5G核心网基础`提供通用的网元 / 接口 / 协议骨架，能覆盖 8 类概念对象；
  - `SMF` 与 `UPF` 之间天然有 **N4 接口 + PFCP 协议**的强连接，正好能验证 `connects_to` + `uses_protocol` 的**两跳**，直接命中 L1 §1.1 的锚点查询"SMF 涉及哪些接口和对应协议"。
- **规模**：粗粒度即可——每主题取若干篇，**目标是覆盖全 8 类对象 + 4 类关系**，不是追篇数。够验证链路就行，后续再扩。

### 2.3 透明前端最小展示集：四样必须可见

挖掘过程透视页（L2 §8）MVP 至少把这四样摊开（其余美化后置）：

| 展示项 | 来源 | 用户能看到 |
|---|---|---|
| **标签** | `asset_segment_entity_mentions.node_type` | 每段把哪些词标成了哪类概念对象 |
| **边** | `ontology_entity_relations` | 抽出了哪些关系（带出处片段） |
| **冲突** | `ontology_entity_relations.has_conflict=true` | 哪些事实互相矛盾（只标不合并） |
| **不确定项** | `mentions.resolve_status='pending'` + `ontology_candidates.status='proposed'` | 哪些待人拍板（消歧 / 新类型） |

---

## 3. 分批实施计划（B0–B8）

每批一个可验收的小目标。「实现依据」列指向 L2，照那里的设计做；「验收」列是"做到这样才算这批完"。

| 批次 | 目标（做什么） | 依赖 | 主要文件改动 | 验收（done =） | 实现依据 |
|---|---|---|---|---|---|
| **B0 管线可组合化地基** | 定义 `PipelineStage` Protocol + `StageWrapper`，`PipelineConfig.stages` 可配置插拔 | — | `mining/contracts/protocols.py`（加 Protocol）、`mining/pipeline.py`（StageWrapper + 按 config 组装）、`mining/infra/mining_config.py`（stages 配置） | 现有逐文档阶段（parse→segment→enrich→relations→retrieval_units→embedding→db_write）全部包成 Stage、经 config 跑通；插入一个空 Stage 默认关、可开关 | L2 §5 |
| **B1 建表 + 薄接口 + 引种** | 全部新表 DDL + 迁移；`OntologyStore`/`GraphStore` 的 PG 实现；`bootstrap_ontology` + 种子文件 | B0 | `mining/infra/pg_schema.py`（新表）、迁移脚本、`mining/infra/db.py` 或新 `stores/`（两个 Store）、`scenario_packs/cloud_core_network/ontology_seed/concept.yaml`（新）、CLI `bootstrap-ontology` | 跑引种 → DB 出现 active v1 + 8 节点类型 + 4 关系类型 + 别名种子；重复跑幂等不重复建；`GraphStore.neighbors()` 对空图不报错 | L2 §3 / §4 / §10 |
| **B2 entity_extract 受本体约束 + 逃生口**〔原 enrich，后拆出独立阶段，见 L4 §16〕 | 实体抽取读 active `ontology_node_types`（不再读 domain.yaml）；**双通道 prompt**（喂类型表，对齐已知类型 / 提议新类型）；输出 `out_of_schema` | B1 | 新 `mining/stages/entity_extract/__init__.py`（拆出）+ `mining/stages/enrich/__init__.py`（瘦身回篇章本职） | 抽出的 mention 都属当前 active 的 8 类；`out_of_schema` 项进 `ontology_candidates(source='escape_hatch')`，不混进正式实体；enrich 不再产实体 | L2 §6.1 |
| **B3 resolve 3 层归一 + 人审分流** | Tier1 精确 + 别名词典归一；不确定 → `pending`（Tier2 默认关） | B1 | 新 `mining/stages/resolve/__init__.py`、`pipeline.py`/`jobs/run.py` 注册 | 每个 mention 带 `canonical_name` + `resolve_status`；命中别名→`auto`，未命中→`pending`（进 实体确认） | L2 §6.2 |
| **B4 entity_relations + 质量门槛** | pattern 约束抽 4 类关系；过五道闸（含 NPMI 关系强度）；非法高置信走软约束分流 | B1 | 新 `mining/stages/entity_relations/__init__.py`（或并入 `stages/relations/`） | 合法边落地；自环 / 非法类型 / NPMI<阈值被拦；非法但高置信 → `ontology_candidates(kind='relation_type')` | L2 §6.3 / §6.3.1 |
| **B5 graph_write 全局落图 + 出处** | 跨文档聚合 upsert `ontology_entities`/`relations`；挂 `ontology_evidence_nodes`；冲突标记；幂等可重跑 | B2,B3,B4 | 新 `mining/stages/graph_write/`（挂 build 阶段，近 `stages/publishing.py`）、`GraphStore.upsert_*` | 跑完一篇 → DB 有 canonical 实体 + 边 + evidence（`source_refs` 非空）；重跑同一 build 幂等不重复；属性矛盾置 `has_conflict=true` | L2 §6.4 |
| **B6 暂停/恢复 + 两道检查点** | `status=awaiting_review` 暂停 + `subloop_stage` 断点续跑；本体确认/实体确认 落盘待审与回写 | B5 | `mining/jobs/run.py`（resume + 检查点）、`mining/infra/pg_schema.py`（`mining_runs` 增 `subloop_stage`/`ontology_version_id`）、两个 Store | 有候选/pending → 暂停（不 publish）；resume 据 `subloop_stage` 从 graph_write 之后续跑；无异常自动放行 publish | L2 §7 / §3.6 |
| **B7 kb-ui 透明前端 + API** | 5 个页面 + 后端端点：透视 / 本体确认 / 实体确认 / 图谱浏览 / 版本 | B6 | 后端 `mining/api/routes/`（新 `ontology.py` + 扩 `runs.py`）、`kb-ui/src/api/mining.ts` & `controlPlane.ts`、`kb-ui/src/views/knowledge/`（新评审/确认/版本页 + 复用 `GraphView.vue`）、`views/mining/RunDetailView.vue`（识别 `awaiting_review`） | 上传→看进度→本体确认 评审→实体确认 确认→看图谱 全程在 UI；§2.3 四样最小展示集可见 | L2 §8 / §9 |
| **B8 检索侧消费** | 实体链接 + 邻域多跳 + 出处回链，接入 agent_serving（新增一路通道） | B5 | `agent_serving/…`（新增图通道，调 `GraphStore.neighbors` + `get_evidence`） | 查询命中对象 → 返回 1~2 跳邻域 + 出处；作为新增通道，不替代 BM25 / 向量 / RST | L2 §11 |

> 阈值（NPMI、置信度、termhood 的 α 等）首波先用 L2 §6.5 的初值，跑通三主题后按实际数据回标——属调优任务（§8）。

---

## 4. 依赖链与里程碑

```
B0 ──► B1 ──┬──► B2 ─┐
            ├──► B3 ─┼──► B5 ──┬──► B6 ──► B7
            └──► B4 ─┘         └──► B8
```

- **B2/B3/B4 可并行**（都只依赖 B1 的表与接口，互不依赖）。
- **B5 是收口点**：抽取轨三件套齐了才能全局落图。
- **B6 必须在 B5 之后**（要先有图，才有候选/pending 触发人审）。
- **B8 与 B6 并行**（都依赖 B5；检索侧不依赖人审）。
- **B7 最后**（要 B6 的暂停/恢复语义齐了，前端才能接 Gate）。

**里程碑（自检点）**：

| 里程碑 | 含 | 达成意味着 |
|---|---|---|
| **M1 地基** | B0 + B1 | 能引种本体、阶段可插拔、空图接口可用 |
| **M2 抽取轨** | B2 + B3 + B4 | 一篇文档能抽出受约束的 mention + 归一 + 合法边（还没全局落图） |
| **M3 落图 + 人审** | B5 + B6 | 全局落图、带出处、暂停/恢复、两道检查点 闭环 |
| **M4 端到端** | B7（+ B8） | UI 全程可见 + 检索可消费 → 跑通三主题子集（§7） |

---

## 5. kb-ui 落地流程

### 5.1 用户视角（一条主线）
1. 选领域 `cloud_core_network`，上传一篇文档，点「开始挖掘」。
2. 看挖掘进度条逐阶段推进；**挖掘过程透视页**实时摊开 §2.3 的四样（标签/边/冲突/不确定项）。
3. 进度停在 **「等待评审」**（`awaiting_review`，本次是 本体确认）→ 跳**本体评审页**：候选类型/关系 + 证据 + 打分，逐条 通过/改名/拒绝，提交。
4. 进度继续，再停在 **「等待评审」**（本次是 实体确认）→ 跳**实体确认页**：pending mention + 系统合并建议，逐条 合并到已有/新建/丢弃，提交。
5. 挖掘完成 → **知识图谱浏览页**：本篇抽到的对象与边、本次领域本体的新增项，点对象看邻域 + 出处回链。

> 同一个 `awaiting_review` 状态在第 3、4 步出现两次，前端靠 `mining_runs.subloop_stage`（`ontology_review` / `entity_review`）决定跳哪个评审页。

### 5.2 页面与文件落点（B7）
| 页面 | 落点 | 复用/新建 |
|---|---|---|
| 挖掘过程透视 | `views/mining/RunDetailView.vue` 扩展 | 复用现有 run 详情 |
| 本体评审（本体确认） | `views/knowledge/` 新页 | 新建 |
| 实体确认（实体确认） | `views/knowledge/` 新页 | 新建 |
| 知识图谱浏览 | `views/knowledge/GraphView.vue` | 复用现有 |
| 本体版本 | `views/knowledge/` 新页 | 新建（含「引种」按钮） |

API 端点清单见 **L2 §9**；前端走现有 `api/mining.ts` / `controlPlane.ts` 模式新增。

---

## 6. 暂停 / 恢复落地（B6 细节）

复用现有 `mining_runs.status` 协作式检查点（与现有 `_check_cancelled` 同款模式），**不空转占线程**：

1. **暂停**：全局阶段（graph_write 后）检查——有 `ontology_candidates.status='proposed'` 或 `mentions.resolve_status='pending'` → 落盘待审数据，置 `status='awaiting_review'` + 写 `subloop_stage`，**任务退出**。无异常 → 直接 publish（**自动放行**，人审非必经）。
2. **轮询**：kb-ui 轮询 run 状态，遇 `awaiting_review` 按 `subloop_stage` 弹对应评审页。
3. **恢复**：人提交后调 `POST /mining/runs/{id}/resume` → 写回人审结果 → 置 `status='running'` → 后端据 `subloop_stage` **跳过已完成步骤、从该检查点之后续跑**。

- 检查点取值：`ontology_review`（本体评审）→ `entity_review`（实体确认）→ `done`。
- 字段定义见 L2 §3.6（`subloop_stage` / `ontology_version_id`）。
- 本体确认 通过后可触发**增量回灌**；MVP 先用"全量重跑代表性子集"兜底，真增量后置（§8）。

---

## 7. 端到端联调验收（M4 终检）

在三主题子集（§2.2）上，从**空本体**开始走完整链路，全部通过算 MVP 达成：

1. **引种**：`bootstrap_ontology` → active v1（8 类型 + 4 关系 + 别名）。
2. **首篇抽取**：上传 `5G核心网基础` 一篇 → 透视页能看到标签/边/不确定项。
3. **本体确认**：若有逃生口候选 → 评审页可通过/拒绝 → 升 v2（或本篇无候选则自动放行，符合预期）。
4. **实体确认**：pending mention → 确认页能合并/新建 → 回写 canonical。
5. **落图**：DB 中 `ontology_entities` / `ontology_entity_relations` 有数据，每条边 `source_refs` 非空、可追到原文片段。
6. **跨篇累积**：再传 `SMF` / `UPF` 两篇 → SMF 与 UPF 之间经 N4（`connects_to`）+ PFCP（`uses_protocol`）连通。
7. **检索闭环**：查"SMF 涉及哪些接口和对应协议" → 经实体链接 + 两跳邻域返回 N4/PFCP 等 + 出处回链。
8. **幂等**：对同一 build 重跑 graph_write，实体/边不重复。

> 这是"端到端能不能跑通"的验收；L2 §13 是"设计本身自不自洽"的纸面自检，两者分工不重叠。

---

## 8. 开放项与排期（调优 / 后置）

| 项 | MVP 怎么兜底 | 后置计划 |
|---|---|---|
| **判断阈值**（冷启动 vs 常规） | MVP 用种子文件引种，本期无"自由抽定第一版"，暂不需要 | 加机制层时再引覆盖率判断 |
| **冷启动语料范围** | 引种走种子文件，与上传语料无关 | 专家本体文档上传 UI（post-MVP） |
| **指标阈值回标**（NPMI / α / 置信度） | 用 L2 §6.5 初值 | 跑通三主题后按真实分布回标 |
| **增量回灌粒度** | 全量重跑代表性子集 | 真增量（只重抽贡献文档），见 L2 §14-2 |
| **并发**（多篇同挖 + 本体并发升级） | 先串行 / 加锁 | 并发冲突处理 post-MVP |
| **Tier2 向量归一** | 默认关，只 Tier1 | 召回不足再开，仍走人审（L2 §6.2） |
| **结果可视化** | 先列表 + GraphView 基础视图 | 富交互图谱后置 |

---

## 9. 落地任务清单（对应批次，可勾选）

- [ ] **B0** 管线可组合化地基（Stage Protocol + StageWrapper + 可配 stages）〔依据 L2 §5〕
- [ ] **B1** 建表 + `OntologyStore`/`GraphStore` + `bootstrap_ontology` + 种子文件〔依据 L2 §3/§4/§10〕
- [ ] **B2** entity_extract 受本体约束 + 逃生口（双通道；原寄生 enrich，后拆独立阶段，见 L4 §16）〔依据 L2 §6.1〕
- [ ] **B3** resolve 3 层归一 + 人审分流〔依据 L2 §6.2〕
- [ ] **B4** entity_relations pattern 约束 + 五道质量闸〔依据 L2 §6.3/§6.3.1〕
- [ ] **B5** graph_write 全局落图 + 出处 + 幂等〔依据 L2 §6.4〕
- [ ] **B6** 暂停/恢复 + 两道检查点（`subloop_stage` 断点）〔依据 L2 §7/§3.6〕
- [ ] **B7** kb-ui 五页面 + 后端 API〔依据 L2 §8/§9〕
- [ ] **B8** 检索侧消费（实体链接 + 邻域 + 出处回链）〔依据 L2 §11〕
- [ ] **M4** 端到端联调（三主题子集，§7 八条全过）

---

## 10. 本体归纳重排（实体先确认、再归纳本体）分批〔提案，待审，依据 L1 §12 / L2 §15〕

> **状态**：**已实施**（2026-06-12，N1–N5 全部落地）。这是在 B0–B8 已落地之上的一次架构演进：把"抽取时直接产类型候选 + 本体确认 优先"改成"先 实体确认 确认实体 → 再用干净实体归纳类型 → 本体确认"。各批在现有阶段上改，**风险集中在编排（两检查点）与数据模型（本体外概念变 pending mention）**。

| 批次 | 目标（做什么） | 依赖 | 主要文件改动 | 验收（done =） | 依据 |
|---|---|---|---|---|---|
| **N1 本体外概念改走 mention** | `entity_extract` 通道 B 不再直接产候选；改写成 `node_type='__untyped__'` 的 pending mention（meta 记 proposed_type/reason） | B2 | `stages/entity_extract/__init__.py`、（必要时）schema 哨兵约束 | 本体外概念出现在 实体确认 待确认列表，不再直接进 `ontology_candidates` | L2 §15.2 |
| **N2 实体聚合前移 + 实体确认 收"暂无类型"** | 在 实体确认 之前算出聚合实体 + 跨文档频率；实体确认 前端加"确认为实体（暂无类型）"裁决 | N1 | `graph_write`（拆出实体聚合轻量步前移）、`MentionReviewView.vue`、`GraphStore` | 实体确认 能确认"暂无类型"实体并落 `ontology_entities(node_type='__untyped__')` | L2 §15.1/§15.2 |
| **N3 ontology_induction 阶段（LLM 调用 2）** | 新阶段：吃"已确认·暂无类型"实体清单 → 聚类/命名/定义新类型 → 产候选 | N2 | 新 `stages/ontology_induction/__init__.py`、`domain.yaml` 加 `mining-ontology-induction` 模板 | 对一批确认实体能产出 `ontology_candidates(source='global_induction')`，DF<2 去噪 | L2 §15.3 |
| **N4 编排：两检查点 + 反转闸序** | `subloop_stage` 扩 `entity_review→ontology_review→done`；两段顺序检查；快速通道；entity_relations + 终态落图后移到 本体确认 之后 | N3 | `jobs/run.py`（`_check_review_gate` 改两段、resume 分档）、`pipeline.py` | 有歧义→停 实体确认；归纳出候选→停 本体确认；都无→快速通道直发；resume 按档续跑 | L2 §15.1/§15.4 |
| **N5 本体确认 通过后回贴类型 + 收尾** | 升版后把成员实体 `__untyped__`→新类型名；跑关系抽取 + 终态落图 + 发布 | N4 | `jobs/run.py`、`OntologyStore`/`GraphStore` | 本体确认 通过 → 成员实体补绑新类型 → 边只连已定类型对象 → 正常发布 | L2 §15.2 |

**实施顺序**：N1→N2→N3→N4→N5（严格链式，每批可独立回归）。建议先在 N1/N2 落地"本体外概念走 实体确认"这条主干（即使 N3 归纳还没上，也已比现状干净——至少类型候选来自人确认过的实体）。

**与现有批次的关系**：N 系列**取代** B2 的通道 B（直接产候选）与 B6 的"单检查点 + 本体确认 优先"，**复用** B6 的 `subloop_stage` 暂停/恢复机制、B3 的 resolve、B5 的落图聚合。

### 10.1 落地任务清单（N 系列，可勾选）

- [x] **N1** 本体外概念 → `__untyped__` pending mention（不再直接产候选）〔L2 §15.2〕
- [x] **N2** 实体聚合前移 + 实体确认 收"暂无类型"裁决〔L2 §15.1/§15.2〕
- [x] **N3** `ontology_induction` 阶段 + 归纳 prompt 模板〔L2 §15.3〕
- [x] **N4** 两检查点编排 + 反转闸序 + 建边后移〔L2 §15.1/§15.4〕
- [x] **N5** 本体确认 通过后回贴类型 + 收尾发布〔L2 §15.2〕
