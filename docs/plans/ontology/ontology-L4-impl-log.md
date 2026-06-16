# 本体概念层 MVP — 实现实录 〔L4 · 代码〕

> 日期：2026-06-09（随 B0–B5 落地同步记录）
> 状态：实现实录（每批"实际改了哪些文件、关键函数长什么样、为什么这么写"）
> 范围：knowledge_mining（挖掘侧）· databases（DDL）· 单领域 cloud_core_network
> 上游：`docs/plans/ontology/ontology-L3-impl-plan.md`（L3 排期，"按什么顺序做、做到什么算完"）

---

## 0. 文档体系（本文在 L4）

本体文档共四层，从"去哪"逐层收敛到"代码长什么样"，**本文是最下层 L4（实际写成了什么）**：

| 层 | 文档 | 本文与它的关系 |
|---|---|---|
| **L1 北极星·方案** | `ontology-L1-solution-design.md` | 定"做什么/为什么" |
| **L2 实现设计·蓝图** | `ontology-L2-impl-design.md` | 定"怎么设计"（表字段/阶段契约/接口/门槛/打分） |
| **L3 实施计划·落地** | `ontology-L3-impl-plan.md` | 定"分几批、依赖链、每批验收" |
| **L4 实现实录·代码**（本文） | `ontology-L4-impl-log.md` | 记**每批实际改/建的文件、关键函数签名、设计取舍、与 L2 的偏差、测试覆盖**。是事后可追溯的施工日志 |

> 一句话：**L1 说去哪，L2 画图纸，L3 排工期，L4 记账（实际怎么施工的）。**

**阅读约定**：本文每个批次先列"文件清单"，再列"关键实现"，最后列"测试 + 取舍/偏差"。代码以实际仓库为准，本文只摘关键签名与意图，不抄全文。

---

## 1. 批次依赖与完成状态

```
B0 ── B1 ──┬── B2 ──┐
           ├── B3 ──┼── B5 ──┬── B6
           └── B4 ──┘        └── B8
                              B6 ── B7
```

| 批次 | 主题 | 状态 | 测试 |
|---|---|---|---|
| B0 | 管线挂载点（补 resolve/entity_relations 阶段位） | ✅ 完成 | 随 B2/B3/B4 |
| B1 | 本体地基（DDL + Store 接口 + 引种 + 种子文件） | ✅ 完成 | — |
| B2 | enrich 受本体约束 + 逃生口 | ✅ 完成 | 4 passed |
| B3 | resolve 三层归一 + 人审分流 | ✅ 完成 | 7 passed |
| B4 | entity_relations pattern 约束抽关系 | ✅ 完成 | 7 passed |
| B5 | graph_write 全局落图 + 出处 | ✅ 完成 | 7 passed |
| B6 | 人审 Gate1/Gate2 + 暂停恢复 | ✅ 完成 | 11 passed（见 §9） |
| B7 | kb-ui 透明前端 + 后端 API | ✅ 完成 | 17 passed（含 B6）+ 前端 vue-tsc 通过（见 §10/§13） |
| §15 批一 | 逃生口可视化（Gate1 评审页增强） | ✅ 完成 | 前端 vue-tsc 通过（见 §16.1） |
| §15 批二 | 实体抽取拆独立阶段 + 双通道 | ✅ 完成 | 全量 135 passed（见 §16.2） |
| §15 批三 | 前端阶段重设计（篇章线/本体线） | ✅ 完成 | 前端 vue-tsc 通过（见 §16.3） |
| §17 修正 | 进度条纳入全局尾段 + 阶段状态去孤儿 | ✅ 完成 | 后端语法校验 + 前端 vue-tsc（见 §17） |
| §14 | Gate2 评审增强（①证据原文 ②相似推荐 ③多选批量） | ✅ 完成 | ③见 §18、①②见 §19（④⑤为设计澄清无代码） |
| §18 | Gate2 多选批量（③丢弃/新建/合并） | ✅ 完成 | test_review_gate 20 passed + 前端 vue-tsc（见 §18） |
| §19 | Gate2 证据原文（①）+ 相似实体推荐（②） | ✅ 完成 | test_review_gate 22 passed + 前端 vue-tsc（见 §19） |
| §20 | 前端体验：批量转圈 + Gate1 改为挖掘子页 | ✅ 完成 | 前端 vue-tsc（见 §20） |
| N 系列 | 本体归纳重排（实体先确认→再归纳本体） | 📋 计划稿 | 未实施；设计见 L1 §12 / L2 §15 / L3 §10 |
| §22 修复 | Gate2 确认实体补写 ontology_evidence_nodes 出处 | ✅ 完成 | test_review_gate 26 passed（见 §22） |
| §23 改名 | 本体层 4 张表加 ontology_ 前缀 | ✅ 完成 | 33 passed + 全仓库引用同步（见 §23） |
| B8 | 检索侧消费 | ⏳ 待做 | — |

---

## 2. 贯穿全局的两个架构决策

落地过程中反复用到、且和 L2 蓝图略有取舍的两条主线，先在此说清，后面各批不再重复：

### 2.1 「逐段流式」与「全局统计」的拆分

挖掘流水线是**逐段流式**的（每段一进一出走 enrich→resolve→entity_relations）。但有些计算**本质是全局的**——NPMI（共现强度）要数一个实体在整个 build 里出现在多少段、候选的跨文档 DF 要数它在多少篇不同快照出现过。这些**没法在逐段阶段里算**。

**取舍**：逐段阶段只负责"产出候选 + 标注"，把结果塞进**段的 metadata**；所有全局统计推迟到 **B5 graph_write**，它在一轮 build 跑完后、对内存里的全部 ctx 跑一次。

- B2 把本体外概念写进 `meta["out_of_schema"]`；
- B4 把类型合法的边写进 `meta["candidate_relations"]`、本体外的边写进 `meta["relation_candidates"]`；
- B5 把这三样跨文档聚合，算 NPMI / DF，落成 `ontology_entities` / `ontology_entity_relations` / `ontology_candidates`。

这是从 B2 就定下、B4/B5 一路沿用的统一节奏。

### 2.2 两个 Store：规则层 vs 事实层

- **OntologyStore**（规则层 / 治理）：管 `ontology_node_types` / `relation_types` / `ontology_candidates` / 版本与别名。
- **GraphStore**（事实层 + 出处）：管 `ontology_entities` / `ontology_entity_relations` / `ontology_evidence_nodes` / mention，强约束"边必须带非空出处"。

两个 Store 都建在 `asset_db.pool`（共享连接池）上。`knowledge_mining/mining/infra/ontology_store.py` 同时容纳这两个类。

---

## 3. B0 — 管线挂载点

**意图**：在不破坏现有流水线的前提下，给 resolve / entity_relations / graph_write 三个新阶段预留位置（阶段名、DDL 的 CHECK 白名单），让后续批次能直接挂上去。

**文件清单**
- `databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql` — `mining_run_stage_events.stage` 的 CHECK 增加 `'resolve'`、`'entity_relations'`、`'graph_write'`。
- `databases/mining_runtime/schemas/001_mining_runtime.sqlite.sql` — 同步加这三个阶段名到 CHECK。

**取舍**：Stage Protocol（阶段接口）本就存在，B0 只是补白名单。**没有动远程库**——按用户约束，DDL 改动只写进文件，等用户重建库时生效。

---

## 4. B1 — 本体地基

**意图**：建起"规则层"的表、Store 接口、引种逻辑，并把首波本体抽成一个**可人工上传的种子文件**（模拟冷启动）。

**文件清单（关键）**
- `databases/asset_core/schemas/002_asset_core_postgresql.sql` — 本体相关表 DDL（节点类型 / 关系类型 / 候选 / 版本 / 别名 / 实体 / 关系 / 证据）。
- `knowledge_mining/mining/infra/ontology_store.py` — OntologyStore + GraphStore 两个类。
- 本体种子文件（首波 8 类概念对象 + 4 类关系，cloud_core_network），单文件、可人工上传。

**关键实现**
- 关系强约束：`ontology_entity_relations.source_refs_json` 必须非空（DB 层 CHECK + `GraphStore.upsert_relation` 代码层兜底双保险）。
- 首波 4 种关系：`connects_to` / `uses_protocol` / `part_of` / `is_a`（见 L3 §2.1）。

**取舍**：种子不在代码里建表、不直连远程库写入——按用户明确要求"你只把 SQL/种子写好，我来手动重建库 + 上传种子"，以此模拟真实冷启动。

---

## 5. B2 — enrich 受本体约束 + 逃生口

**意图**：让 LLM 抽取时**只在当前本体允许的类型里**打标；遇到本体外的概念**不丢弃**，而是经逃生口收集起来，等 B5 全局打分后变成候选。

**文件清单**
- `knowledge_mining/mining/stages/enrich/__init__.py` — LlmEnricher 受本体约束 + 逃生口落地。

**关键实现**
- `LlmEnricher.__init__` 新增 `ontology_store` / `domain_id`；`_resolve_allowed_types()` 读当前 active 本体节点类型，读不到则回退 profile 默认类型。
- 逃生口落地（`_apply_llm_result` 内，紧跟 `meta = dict(seg.metadata_json)`）：
  ```python
  if out_of_schema:
      existing_oos = {(d["type"], d["name"]) for d in meta.get("out_of_schema", [])}
      merged_oos = list(meta.get("out_of_schema", [])) + [
          d for d in out_of_schema if (d["type"], d["name"]) not in existing_oos
      ]
      meta["out_of_schema"] = merged_oos
  ```
  用 `(type, name)` 去重，避免同段重复塞。

**测试**：`knowledge_mining/tests/test_enrich_ontology_constraint.py`（4 passed）。

**取舍**：逃生口**只暂存进段 metadata**，不在此处写候选表——跨文档 DF 是全局量，得等 B5（见 §2.1）。

---

## 6. B3 — resolve 三层归一 + 人审分流

**意图**：把抽出来的实体名归一到 canonical（标准名）。命中别名/本身就是标准名 → 自动定（`auto`）；都不命中 → 挂起（`pending`）交人审，不瞎猜。

**文件清单**
- `knowledge_mining/mining/stages/resolve/__init__.py`（新建）— EntityResolver。
- `knowledge_mining/mining/infra/ontology_store.py` — 新增 `all_aliases(domain_id)`（返回 alias_normalized / canonical_name / node_type）。
- `knowledge_mining/mining/pipeline.py` — PipelineConfig 加 `resolver` 字段；加 `resolve_stage(ctx, cfg)`；顺序版 `process_document` 插入 Stage 3b。
- `knowledge_mining/mining/jobs/run.py` — `_init_resolver(asset_db, profile)`（line 370）。

**关键实现**
- `EntityResolver`（stage_name=`"resolve"`）：`_ensure_index()` 启动时调 `store.all_aliases` 建一次内存别名索引。
- `_resolve_one(name)`：归一化 → 别名表命中→`auto`；surface 本身就是某 canonical→`auto` 自指；否则 →`(None, "pending")`。
- `resolve_batch(segments)`：给每个 entity_ref 标 `canonical_name` + `resolve_status`，用 `dataclasses.replace` 改 frozen 段。
- 复用 ontology_bootstrap 的 `_normalize_alias`。`enable_tier2_vector` 向量归一开关默认关。

**测试**：`knowledge_mining/tests/test_resolve_entity.py`（7 passed）。

**踩坑修复**：初稿调了不存在的 `seg.with_entity_refs(...)`。`RawSegmentData` 是 frozen dataclass、没有 `with_*` 辅助方法，改用 `dataclasses.replace(seg, entity_refs_json=new_refs)`。

---

## 7. B4 — entity_relations pattern 约束抽关系

**意图**：在段内对实体两两配对，按本体 `relation_types` 的 `allowed_pairs`（类型对白名单）判断能不能连边。命中→候选边；类型对不在任何 pattern→本体外关系候选（逃生口）。

**文件清单**
- `knowledge_mining/mining/stages/entity_relations/__init__.py`（新建）— `npmi()` 纯函数 + EntityRelationBuilder。
- `knowledge_mining/mining/infra/ontology_store.py` — `active_relation_types(domain_id)`（返回 name + allowed_pairs_json）。
- `knowledge_mining/mining/pipeline.py` — PipelineConfig 加 `entity_relation_builder`；加 `entity_relations_stage`；顺序版插入 Stage 3c。
- `knowledge_mining/mining/jobs/run.py` — `_init_relation_builder(asset_db, profile)`（line 386）。

**关键实现**
- 模块级 `npmi(p_h, p_t, p_ht)` 纯函数：退化输入（无共现 / 概率为 0）返回 `-1.0`；完全共现（p_ht==1）约定返回 `1.0`。口径见 L2。
- `EntityRelationBuilder`（stage_name=`"entity_relations"`）：`_ensure_index()` 读 `active_relation_types`，建 `_patterns: {relation_name: [(head_type, tail_type), ...]}`，支持 `"*"` 通配。
- `build_batch(segments)` 三道闸：
  - **闸 1** 端点齐全（head/tail 都有）；
  - **闸 2** 非自环（`_node_key` = canonical_name 优先，否则 name，相同则跳过）；
  - **闸 3** 类型对命中 allowed_pairs。
  - 命中→`meta["candidate_relations"]`；本体外→`meta["relation_candidates"]`（带 `reason="off_schema_pair"`）。

**测试**：`knowledge_mining/tests/test_entity_relations.py`（7 passed，含 npmi 数学口径用例）。

**取舍**：NPMI 本身在 B4 只提供纯函数，**不在逐段阶段算**——共现统计是全局的，B5 才会真正用 `npmi()` 给每条候选边打分（见 §2.1）。

---

## 8. B5 — graph_write 全局落图 + 出处

**意图**：一轮 build 跑完后，把所有文档的候选**跨文档聚合**，算共现强度与 DF，落成带出处的领域知识图；本体外候选汇成 `ontology_candidates` 等人审。这是"逐段产出 → 全局收口"的收口点。

**文件清单**
- `knowledge_mining/mining/stages/graph_write/__init__.py`（新建）— `aggregate_build`（纯）+ `persist_build_graph`（薄 DB 层）+ 一组 dataclass。
- `knowledge_mining/mining/infra/ontology_store.py` — 新增三个 helper：
  - `set_entity_counts(entity_id, *, mention_count, document_count)`：**SET 不累加**（幂等）。
  - `delete_snapshot_artifacts(document_snapshot_id)`：删某快照的 ontology_evidence_nodes + mention。
  - `upsert_candidate(domain_id, *, kind, proposed_name, payload, source, evidence, score, layer)`：按 `(domain, kind, proposed_name)` 去重；**不回退**已 accepted/rejected 的候选。
  - `list_candidates(domain_id, *, status="proposed")`。
- `knowledge_mining/mining/jobs/run.py` — `_run_graph_write(asset_db, tracker, run_id, ctxs, domain_id)`（line 402），在 build 阶段作为 "Phase 1d" 插在 "Phase 2: Build & Publish" 之前。

**关键实现 — `aggregate_build`（纯函数，无 DB）**
```python
def aggregate_build(docs, *, domain_id, npmi_threshold=0.3) -> BuildGraph: ...
```
- 遍历 docs（读 `.snapshot_id / .seg_ids / .profile.document_key / .segments`）。
- 建 mention；`auto` 且有 canonical → EntityRec（带 mention/document 计数）；`pending` 只记 mention、不升实体。
- `name_segs` 记每个实体串出现的全局段集 → 算 NPMI；`kept = npmi >= threshold`。
- `out_of_schema` → NodeCandidateRec（DF = 不同快照数，`score = df + 0.01*tf`）；`relation_candidates` → RelCandidateRec（按类型对聚合 cooccur）。
- 产出 `BuildGraph`，含 `.kept_edges` 与 `.gate1_node_candidates(min_df)`。

**关键实现 — `persist_build_graph`（薄 DB 层）**
```python
def persist_build_graph(graph_store, ontology_store, bg, *,
                        domain_id, ontology_version_id=None,
                        min_candidate_df=2) -> dict[str, int]: ...
```
1. **幂等**：先按快照 `delete_snapshot_artifacts(snap)` 清旧 mention/evidence。
2. canonical 实体 `upsert_entity` + `set_entity_counts`（SET 非累加）+ 实体出处。
3. mention + mention 出处。
4. **只落 kept 边**：`upsert_relation`，source_refs 用证据 id（强约束非空），confidence=`(npmi+1)/2`。
5. 候选：DF≥min_df 的节点候选 + 关系候选 → `upsert_candidate`。
6. 返回各类写入计数 dict。

**常量**：`_DEFAULT_NPMI_THRESHOLD=0.3`、`_DEFAULT_MIN_CANDIDATE_DF=2`（DF=1 的逃生口候选不进 Gate1，去噪）、`_QUOTE_MAX=300`。

**测试**：`knowledge_mining/tests/test_graph_write.py`（7 passed）。用 `SimpleNamespace` 造 doc 视图、`_FakeGraph`/`_FakeOntology` 假 Store，覆盖：
- auto 实体 + 恒共现边（npmi=1.0）；
- pending 只记 mention 不升实体；
- 弱共现边被 NPMI 闸丢（5 段 A/B 构造，npmi<0.3）；
- 跨文档 out_of_schema 候选 DF=2；关系候选按类型对聚合；
- persist 幂等形态（先删快照、计数 SET、边出处非空）；
- persist 刷候选（node_type "5GC" + relation_type "alarm→feature"）。

**幂等三策**（按数据性质分别处理）：
- mention/evidence 是**快照粒度** → "先删快照旧物再重写"；
- 实体计数 → `set_entity_counts` SET（不累加，重跑不翻倍）；
- 关系/候选 → ON CONFLICT / 自然键去重。

**保护性设计**：`_run_graph_write` 仅当该领域**已引种本体（有 active 版本）**才跑，未引种则跳过（本体能力默认不影响存量流水线）；且整段 try/except，落图失败只记日志、不阻断整轮 build。

---

## 9. B6 — 人审 Gate1/Gate2 + 暂停恢复

**意图**：全局落图后若攒出本体候选或挂起实体，**暂停**整轮 build（不发布），等人在前端拍板；人审提交后**从断点续跑**建库发布。两道 Gate 异常触发、非必经——没异常就自动放行。

**文件清单**
- `databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql` + `001_*.sqlite.sql` — `mining_runs.status` 的 CHECK 增加 `'awaiting_review'`。
- `databases/ontology/schemas/001_ontology_concept_postgresql.sql` — mention 的 `resolve_status` CHECK 增加 `'rejected'`（Gate2 丢弃用）。`subloop_stage`/`ontology_version_id` 两列在 B1 已随本体 DDL §6 写好，本批直接复用。
- `knowledge_mining/mining/infra/db.py` — `update_run_status` 支持写 `subloop_stage`/`ontology_version_id`；新增 `get_awaiting_review_runs(domain=None)`。
- `knowledge_mining/mining/runtime/__init__.py` — RuntimeTracker 加 `pause_for_review(run_id, *, subloop_stage, ontology_version_id, **counters)`（不写 finished_at）+ `resume_running(run_id, *, subloop_stage)`。
- `knowledge_mining/mining/infra/ontology_store.py` — Gate1（OntologyStore）+ Gate2（GraphStore）回写能力（见下）。
- `knowledge_mining/mining/jobs/run.py` — 暂停/恢复编排：`_check_review_gate` / `_finalize_run`（抽出原 Phase 2）/ `resume()`（公开入口）/ `_rebuild_from_run_documents`。

**关键实现 — Gate1（OntologyStore）**
- `count_proposed_candidates(domain_id) -> int`：暂停触发判定。
- `review_candidate(candidate_id, *, action, new_name=None, note=None)`：单条裁决，`accept`→`status='accepted'`（可顺带改名，写回 `proposed_name`）/ `reject`→`status='rejected'`。**只标状态、不升版**。
- `promote_accepted_candidates(domain_id, *, created_by=None) -> str | None`：把全部 accepted 候选**并入一个新 active 版本**——克隆旧 active 版的点/边类型 → 追加候选转出的新类型（`node_type` 候选→新节点类型；`relation_type` 候选→新边类型，`allowed_pairs` 取 payload 的 `head_type`/`tail_type`）→ 激活新版（旧版自动降 superseded）。无 accepted → 返回 None（自动放行不升版）。

**关键实现 — Gate2（GraphStore）**
- mention 表无 `run_id`，按 run 维度查要经 `mining_run_documents.document_snapshot_id` 关联：`pending_mentions_for_run(run_id)` / `count_pending_mentions_for_run(run_id)`。
- `resolve_mention(mention_id, *, action, entity_id=None, domain_id=None, ...)`：`merge`→指向已有 canonical 对象（`resolve_status='human'`）/ `new`→按 mention 自身 `upsert_entity` 再指过去 / `reject`→`resolve_status='rejected'`（非实体）。

**关键实现 — run.py 暂停/恢复编排**
- `_check_review_gate(asset_db, run_id, domain_id) -> str | None`：gate1 优先于 gate2，都无→None。
- 首跑 Phase 1e（graph_write 之后）：命中 Gate → `pause_for_review` 置 `awaiting_review` + `subloop_stage` + 记下本次 `ontology_version_id`，**直接 return 退出**（不 build/publish）；无 Gate → 走 `_finalize_run`。
- `_finalize_run(...)`：把原 Phase 2（assemble_build / validate / publish_release + 收尾 complete/fail）抽成独立函数，**首跑与 resume 共用**。
- `resume(run_id, *, domain, ...)`：公开入口（仿 `publish` 的 DB 生命周期）。幂等地**重新评估 Gate**——还剩待审则刷新 `subloop_stage` 保持 `awaiting_review`；两道都清空则置 `running` → 续跑 `_finalize_run`。首跑的内存态（snapshot_decisions/计数）随进程退出已丢，由 `_rebuild_from_run_documents` 从 `mining_run_documents`（`status='committed'` 行）重建。

**测试**：`knowledge_mining/tests/test_review_gate.py`（11 passed）。假 store 子类只覆盖 `_execute`/`_fetchone`/`_fetchall`，让被测方法真实的 SQL 拼装与编排照常跑。覆盖：Gate 优先级（gate1>gate2>None）、resume 重建计数/decisions、promote 克隆旧版+追加候选+激活、候选 accept 改名 / reject、mention merge/new/reject 三路。

**取舍**：
- **升版时机**：`review_candidate`（逐条标态）与 `promote_accepted_candidates`（一把升版）分开，对应前端"逐条审完再提交"。promote 由 B7 的提交 API 触发，不在 resume 里。
- **MVP 不做增量回灌**：Gate1 升版影响的是**后续** run；本 run 已抽好的图审完直接发布，不重抽（L3 §6 标注的"全量重跑代表性子集"兜底后置）。
- **HTTP API 归 B7**：B6 只交付后端能力（Store + run.py 编排），Gate1/Gate2 的 REST 端点（L2 §9）在 B7 与前端一起接。

---

## 10. B7 — kb-ui 透明前端 + 后端 API

**意图**：把 B1–B6 攒下的后端能力**全程在 UI 走通**——上传引种 → 看挖掘过程 → Gate1 评审本体候选 → Gate2 确认实体 → 浏览知识图谱与出处回链。让"概念层"对用户透明可见、可干预。

**文件清单 — 后端（`knowledge_mining/mining/api/`）**
- `routes/ontology.py`（新）— 本体/图谱/Gate 全部 REST 端点（见下），前缀 `/api`，tag `ontology`。
- `routes/runs.py` — 新增 `GET /api/runs/{id}/trace`（透视叠加视图）+ `POST /api/runs/{id}/resume`（人审后续跑，包 B6 的 `jobs/run.resume`）。
- `app.py` — lifespan 里**额外建一个同步连接池** `app.state.sync_pool`；注册 `ontology_router`。
- `infra/ontology_store.py` — GraphStore 补查询：`list_entities` / `count_entities` / `get_entities_by_ids`（邻域图按 id 批量取名）/ `evidence_for_target`（按 target_id 取出处并 JOIN 片段拿原文）。

**关键取舍 — 同步 Store 跑在异步 API 里**
- OntologyStore/GraphStore 是**同步**适配器（与挖掘流水线复用同一套编排，B6 已写好），而 Mining API 用的是 `AsyncConnectionPool`。
- 为**零重写**复用 B6 逻辑（promote 升版、resolve_mention 等多步编排），路由用 `asyncio.to_thread(...)` 把同步 Store 调用丢到线程池，跑在 `app.state.sync_pool` 上——而不是把这些编排重新用异步裸 SQL 实现一遍。这也沿用了既有 `publish_run`/`create_run`"异步处理器里调同步 job"的先例。

**API 端点（对应 L2 §9，实际加 `/api` 前缀）**
- 本体：`POST /api/ontology/bootstrap`（吃上传的 YAML 文本→`bootstrap_ontology_from_text`）、`GET /api/ontology/versions`、`GET /api/ontology/active`。
- Gate1：`GET /api/ontology/candidates?status=`、`POST /api/ontology/candidates/{id}/review`（accept/改名/reject）、`POST /api/ontology/promote`（一把升版）。
- Gate2：`GET /api/mentions/pending?run_id=`、`POST /api/mentions/{id}/resolve`（merge/new/reject）。
- 图谱：`GET /api/graph/entities`（检索）、`GET /api/graph/entities/{id}/neighbors?hops=`（边+涉及的点一起返回，前端直接渲染）、`GET /api/graph/evidence/{target_id}`（出处回链）。
- run：`GET /api/runs/{id}/trace`、`POST /api/runs/{id}/resume`。

**文件清单 — 前端（`kb-ui/src/`）**
- `api/mining.ts` + `types/index.ts` — 新增上述全部端点的调用方法与 TS 类型（`RunTrace`/`OntologyVersion`/`ActiveOntology`/`OntologyCandidate`/`PendingMention`/`GraphEntity`/`EntityNeighbors`/`GraphEvidence`）；`MiningRun.status` 扩 `awaiting_review`/`interrupted`。
- `views/knowledge/OntologyView.vue`（新）— 本体版本页：版本历史 + active 点/边类型 + **上传 YAML 引种按钮**。
- `views/knowledge/OntologyReviewView.vue`（新）— Gate1：候选列表逐条 通过/改名/拒绝 + 右上角"提交升版"。
- `views/knowledge/MentionReviewView.vue`（新）— Gate2：pending mention 列表 新建/合并(搜索已有对象)/丢弃；支持 `?run_id=`。
- `views/knowledge/EntityGraphView.vue`（新）— 实体图谱：左侧对象检索，右侧邻域力导向图（复用 `ForceGraph`）+ 出处回链。
- `views/mining/RunDetailView.vue` — 识别 `awaiting_review`：拉 `trace`、显示**人审暂停横幅**（按 active_gate 跳 Gate1/Gate2 页）+ "继续挖掘"按钮（调 resume）。
- `components/common/StatusBadge.vue`（加 `awaiting_review`/`interrupted` 样式）、`components/layout/Sidebar.vue`（加"实体图谱"/"本体版本"入口）、`router/index.ts`（加 `/entities` `/ontology` `/ontology/review` `/mentions/review` 路由）。

**测试/验证**
- 前端 `vue-tsc -b` 全量类型检查通过（修了 3 处：StatusBadge 状态联合、两处表格回调隐式 any）。
- 后端 `import app` 校验：11 个 ontology 端点 + trace/resume 全部挂载成功。
- `test_review_gate.py` 补 6 条新 GraphStore 查询的 SQL 拼装单测（实体检索过滤/分页、count、批量 IN、空列表短路、evidence JOIN），共 **17 passed**。

**取舍**：
- **图谱浏览另起 `EntityGraphView`**：既有 `GraphView.vue` 画的是片段级 RST 关系（`asset_raw_segment_relations`），与实体图（`ontology_entities`/`ontology_entity_relations`）不是一回事，故新建一页，不动旧页。
- **trace 是叠加视图**：失败不阻塞 RunDetail 主流程（catch 吞掉）；对象/边规模 MVP 按 domain 计数，不按 snapshot 精确切分。
- **恪守约束**：全程未对远程库执行任何 DDL/写入；引种仍走"用户上传一个 YAML 文件"模拟冷启动。

---

## 11. 累计测试与回归

| 套件 | 结果 |
|---|---|
| test_enrich_ontology_constraint.py（B2） | 4 passed |
| test_resolve_entity.py（B3） | 7 passed |
| test_entity_relations.py（B4） | 7 passed |
| test_graph_write.py（B5） | 7 passed |
| test_review_gate.py（B6+B7） | 17 passed |
| 前端 `vue-tsc -b`（B7） | 类型检查通过 |

全程未对远程库执行任何 DDL/写入（恪守用户约束）。

---

## 12. 待续（B8 实现后补记本文）

- **B8**：agent_serving 检索侧消费（实体链接 + GraphStore.neighbors + get_evidence）。

> 本文每批续写"文件清单 / 关键实现 / 测试 / 取舍"。

---

## 13. B7 联调踩坑与修复（2026-06-10）

> 前端 + 后端跑起来后实测，连撞三类 bug，逐一定位修复。记录于此，方便回溯"这次到底改了哪些东西"。

### 13.1 域包文件名错配 → 挖掘 FileNotFoundError + 系统设置页 404

**现象**：
- 挖掘任务报 `FileNotFoundError: Domain pack not found: .../scenario_packs/cloud_core_network/domain_cloud_core.yaml`。
- 前端系统设置页报 404，URL `http://localhost:8910/api/v1/domains/cloud_core_network/scenario/raw`。

**根因**：工作区里有一批**未提交**的全局改名——把代码中读写的文件名 `domain.yaml` 改成了 `domain_cloud_core.yaml`，但**实际磁盘上的 yaml 文件没跟着改名**（仍叫 `domain.yaml`）。凡是被改到的代码都去找一个不存在的文件就报错。`git grep domain_cloud_core.yaml HEAD` 为空，证明**已提交状态全是 `domain.yaml`**——`domain.yaml` 才是一致、能跑的命名。用户决定全仓保留 `domain.yaml`。

**修复**（全部还原回 `domain.yaml`，只替换该字符串、不动其它改动）：
- `knowledge_mining/mining/infra/domain_pack.py` — `git checkout` 整文件还原（路径 2 处 + 注释/文档串）。
- `main_control_service/service.py` — 5 处 `domain_cloud_core.yaml` → `domain.yaml`（修 404）。
- `agent_serving/serving/domain_pack_reader.py`（3）、`agent_serving/serving/eval/runner.py`（1 文档串）、`agent_serving/tests/test_eval_cloud_core.py`（2）。
- `domain_registry.yaml`（注释 1）、`knowledge_mining/mining/contracts/rst_relations.py`（文档串 1）。
- **未动**：`knowledge_mining_zym/`、`knowledge_mining_fzl/` 两个变体沙盒（夹带大量其它未提交改动，按用户要求暂不统一）。

**注**：控制面 `service.py` 改后需**重启 main_control_service** 才生效。

### 13.2 llm_service 缺表：`relation "agent_llm_tasks" does not exist`

**现象**：llm_service worker 每隔几秒报 `UndefinedTable: relation "agent_llm_tasks" does not exist`。

**根因**：`coremasterkb` 库里唯独缺了 `agent_llm_*` 这 7 张表（mining_*/asset_* 都在）。llm_service 只在**启动那一刻**通过 `ensure_schema()`（`llm_service/main.py:65`）建表一次；这库后来被清/重建过而服务进程没重启，运行中的服务不会自动补建被删的表。

**处理**（运维操作，非代码改动）：手动将 `databases/agent_llm_runtime/schemas/002_agent_llm_runtime_postgresql.sql` 逐句应用到 `coremasterkb`，建回 7 张表（agent_llm_tasks/requests/attempts/results/events/prompt_templates/model_calls）。worker 下次轮询即恢复，无需重启。

**已知风险（未改）**：`reset_db.py` 的 `SCHEMA_FILES` 漏了 `databases/ontology/schemas/001_ontology_concept_postgresql.sql`，`ALL_TABLES` 也没列 9 张本体表 → 走 reset_db.py 一键重建会缺本体层。走"重启各服务"路径则完整（knowledge_mining API 的 `ensure_schema` 已含 ontology DDL）。

### 13.3 RST 关系类型 CHECK 约束与代码不一致 → 入库被拦

**现象**：最后一个文件 pipeline 失败，`new row for relation "asset_raw_segment_relations" violates check constraint "..._relation_type_check"`，失败值 `relation_type='exemplifies'`。

**根因**：话语关系环节 LLM 可产出 15 种 RST 关系（`rst_relations.py` 的 `RST_DB_VALUES`），但 DB 的 CHECK 白名单只列了 12 种，**漏了 `exemplifies` / `concedes` / `purposes`**。一旦标到漏掉的那 3 种，入库即被约束拦下。

**修复**（两头都补，新建库 + 现有表都覆盖）：
- **DDL 文件** `databases/asset_core/schemas/002_asset_core_postgresql.sql` — 在 `relation_type` 的 `CHECK ... IN (...)` 里加上 `'exemplifies', 'concedes', 'purposes'`（管以后新建/reset 的库）。
- **线上 coremasterkb 现有表** — `ALTER TABLE asset_raw_segment_relations DROP/ADD CONSTRAINT`，把约束换成"旧全集 + 这 3 个"的超集（已有数据兼容，无冲突）。
- 校验：`RST_DB_VALUES`（15）⊆ 更新后约束，无遗漏。

**复跑**：重传失败的 `规则规划_92407891.md` 即可通过；已"完成"的文件不受影响。

### 13.4 落一份可上传的本体种子到 `data/ontology/`（实跑冷启动用）

**背景**：用户删库重入后，「本体版本 →引种本体」需要一个**可人工上传的种子文件**走冷启动。B1 原种子在 `scenario_packs/cloud_core_network/ontology_seed/concept.yaml`，本次按用户要求在 `data/ontology/` 另落一份**扩充版**，作为实跑的标准上传件。

**产出**：`data/ontology/cloud_core_network_concept.yaml`（与 `ontology_bootstrap.py` 同 schema，可直接被「引种本体」解析）。
- **schema**：`layer`（默认层）/ `node_types`（name·is_strong·definition·examples）/ `relation_types`（name·is_directed·inverse_name·patterns·definition，patterns=允许的 head/tail 类型对，`*`=任意）/ `aliases`（{规范名: [别名…]}，source='seed'）。
- **内容**：8 类概念对象（network_element/command/parameter/protocol/interface/alarm/feature/concept）+ 4 种关系（connects_to/uses_protocol/part_of/is_a）+ **13 组别名 / 共 33 个别名**。
- **相比 B1 原种子的扩充**：网元 examples 补 SMSF/UDR；接口补全 N1/N2/N8/N10；协议加 SCTP/TCP；**别名词典从 6 组扩到 13 组**（新增 AUSF/NSSF/UDR/PFCP/GTP-U/5GC/PDU会话），提高实跑归一化命中率。

**校验**：`yaml.safe_load` 解析通过，node/relation 必填字段（name、patterns 的 head/tail）齐全；未写库（恪守"只写文件、不直连远程库"约束）。

**注**：引种是**幂等**的（`bootstrap_*` 见 active 版本即 skip），故删库重入后第一次上传才真正建 v1；重复上传不会重复建版。

---

## 14. Gate2 评审增强 + 两则设计澄清（2026-06-11，计划）

> B7 实测后用户提了 5 点诉求。本节记前 4 点（①②③ 改动方案 + ④⑤ 设计澄清）+ 4 个拍板项。第 5 点（逃生口可视化）与"本体抽取 / RST 篇章关系抽取拆分"另起讨论，定案后再补记。
>
> **进度（2026-06-11）**：①②③ 全部实现。③ 多选批量见 **§18**；① Gate2 证据原文（14.2）+ ② 相似实体推荐（14.3）见 **§19**。④⑤ 为设计澄清，无代码。

### 14.1 四个拍板项（用户 2026-06-11 确认）

| # | 选择点 | 定案 |
|---|--------|------|
| 1 | ②推荐是否限同类型 | **限同 node_type**（更准） |
| 2 | ②推荐触发时机 | **列表加载即并发预取**（最快看到） |
| 3 | ③批量合并语义 | **只做"并到同一已有对象"**；"彼此聚成新对象"用批量新建顶替 |
| 4 | ①原文展示方式 | **可展开行**（不挤列表） |

### 14.2 ① Gate2 提及列表带出证据原文

**意图**：每条待确认提及旁能看原文上下文，便于判断该提及在特定语境下指什么。

**方案**：提及（`asset_segment_entity_mentions`）本就有 `segment_id`，给 `pending_mentions_for_run` / `pending_mentions` 的 SQL 加 `LEFT JOIN asset_raw_segments s ON s.id = m.segment_id`，带出 `s.raw_text AS segment_text`、`s.section_title AS segment_section`。前端用可展开行显示。改动小、风险低。

### 14.3 ② 实时推荐相似的已有实体

**意图**：列表里每条提及旁直接列出"库里可能是同一个的实体"，点一下即合并，省掉先开弹框再搜索的绕路。

**方案**：新增 `GraphStore.suggest_entities_for_mention(domain_id, *, mention_text, node_type, limit=5)`——`WHERE domain_id=%s AND node_type=%s AND (canonical_name ILIKE %s OR %s ILIKE '%'||canonical_name||'%')`（**双向包含**：实体名含提及 OR 提及含实体名，后者让"SMF会话"能命中已有"SMF"），按 `mention_count DESC` 取 top-N。限同 node_type。新增 `GET /api/mentions/suggest`。前端列表加载后并发为每条预取 top-3，渲染成可点小标签（点=直接合并）。

**局限（已知，标 TODO）**：纯字面匹配，"会话管理功能↔SMF"这类语义异形抓不到；后续接别名词典（`ontology_alias_dictionary`）或向量相似度增强。

### 14.4 ③ 多选批量：丢弃 / 新建 / 合并

**意图**：成批处理提及，减少逐条点击。

**方案**：
- 后端新增 `GraphStore.resolve_mentions_batch(mention_ids, *, action, entity_id=None, domain_id=None, node_type=None)`——循环复用单条 `resolve_mention`，逐条返回结果，零重写。新增 `POST /api/mentions/resolve-batch`（+ `ResolveBatchRequest`）。
- 前端 `MentionReviewView.vue` 表格加 `type="selection"` 多选 + 顶部批量操作栏：批量丢弃 / 批量新建 / 批量合并到已有（弹一次框选一个目标实体，选中的全并过去）。
- **合并语义评估结论**：只做"并到同一已有对象"（语义清晰、循环指过去即可）；"选中的彼此聚成一个新对象"语义可用"批量新建"覆盖（同名自动聚合），不单独实现。

### 14.5 ④ 实体如何关联 segment（设计澄清，无代码改动）

一个确认/合并后的 canonical 实体，与原文片段经**两条路**关联：
- **提及桥（间接）**：每条提及天生记 `segment_id`（出自哪句）+ 确认后填 `resolved_entity_id`（归属哪个实体）。拿实体 id 反查 `resolved_entity_id` 即得它在所有文档里被提到的每一句。提及表是"抽取过程痕迹"，文章级、随重跑清写。
- **出处表（直接、稳定）**：落图阶段给实体显式写 `ontology_evidence_nodes`（`target_kind='entity'`、`target_id=实体id`、`segment_id`、`quote` 摘录原话）。这是给检索/展示用的稳定出处链，「实体图谱」页"出处回链"即走此路。
- **权威出处 = `ontology_evidence_nodes`**；提及表是更细粒度的过程记录。

### 14.6 ⑤ 确认的实体是否需人工提取成本体节点（设计澄清，无代码改动）

**结论：不需要。** 实体=实例（ABox，`ontology_entities`），本体节点=类型（TBox，`ontology_node_types`），两层分开：
- 每条提及抽取时**就带 node_type**，Gate2 确认后实体直接继承——实体天生挂在某个本体节点类型下，不存在"确认完还要人工塞进本体"。
- **Gate2 管实例消歧**（这个"SMF会话"是不是已有的"SMF"：合并/新建/丢弃），不碰类型。
- **改本体类型是 Gate1 的活**：现有类型装不下的新概念经逃生口 → `ontology_candidates` → 人工审 → 升版新增 node_type。
- 边缘情况"某实例本该是类型"（如"PDU会话"反复出现想升格为独立类别）也走 Gate1 候选，非 Gate2。

### 14.7 影响文件清单（待实现）

- 后端：`ontology_store.py`（GraphStore：改 2 个 pending 方法 SQL、加 suggest/批量 2 方法）、`api/routes/ontology.py`（加 2 端点）。
- 前端：`types/index.ts`（PendingMention 加 segment 字段）、`api/mining.ts`（加 suggest/批量 2 方法）、`MentionReviewView.vue`（展开行 + 推荐标签 + 多选批量栏）。
- 测试：`tests/test_review_gate.py`（加 3 组 SQL 拼装单测）。

---

## 15. 实体抽取从 enrich 拆出独立阶段（双通道）+ 逃生口可视化（2026-06-11，方案）

> 讨论"把本体抽取和篇章抽取分开管理"时,读 `enrich/__init__.py` 实测纠正了一个认知错误,进而定下"实体抽取独立成阶段"的架构决策。本节为**方案稿,尚未实现**,含 6 块:认知修正 / 拆分决策 / 双通道 prompt / pipeline 重设计 / 前端重设计 / 逃生口可视化。

### 15.0 认知修正：推翻前面"三层已解耦"的口头结论

讨论中我一度说"实体链与 RST 链在代码/存储/显示三层已解耦,只剩执行编排耦合"。**读 enrich 源码后此结论作废**——

- `LlmEnricher`（`stages/enrich/__init__.py`,stage_version=2）**是原篇章 graphrag 管道的"段落理解"阶段**,用**一次** LLM 调用（模板 `mining-segment-understanding`）同时产出三样:**语义角色** + **内容质量评估**（is_substantive/is_navigation,篇章/检索本职）+ **实体抽取**。
- B2 的本体改造**没有新建实体抽取阶段**,而是**嫁接**在 enrich 上（`:50` `_resolve_allowed_types` 用 active 本体点类型约束、`:177` 越界类型进逃生口）。即:**本体实体抽取寄生在篇章理解的同一次 LLM 调用、同一个 prompt 里**。
- **真实耦合点** = 实体抽取与段落理解共用一次 LLM 调用。这才是"想分开"的真正落点,前面那几层早已分开,唯独这里没分。

### 15.1 决策：实体抽取拆成独立阶段（独立 LLM + 本体感知 prompt）

**决策**：enrich 卸掉实体抽取、回归篇章本职（只留语义角色 + 内容质量）；新建独立 `entity_extract` 阶段,自己一次 LLM 调用,**把 active 本体类型表喂进 prompt**。

**前提（价值兑现的关键）**：拆 = **重写一个"本体感知"的实体抽取 prompt**,不是把旧 prompt 里抽实体那段原样搬出来。若只搬代码、prompt 不变,则只多一次 LLM 调用、质量零提升——纯亏。收益全部来自"新 prompt 把类型表喂进去 → 抽得更准、逃生口判得更干净"。

**代价**：每个实质段落由过一次模型变两次,成本/延迟约翻倍。本场景可接受——挖掘**离线**(延迟不敏感)、runtime 已切 **claude_cli 本地 provider 不走 API key**(成本非硬约束,见 [[project_claude_pro_no_headless]])。

**爆炸半径小**：新阶段照旧把实体写 `entity_refs_json`、逃生口写 `meta["out_of_schema"]`;下游 resolve / entity_relations / graph_write 读这俩字段,**谁写不关心,接口不变,不用改**。

### 15.2 双通道 prompt 设计（既照本体抽,又能发现新类型）

**矛盾**：把类型清单喂进去,模型易"偷懒对齐"——把清单外的新概念硬塞进最接近的旧类型,导致**永远发现不了新类型、本体不进化**(逃生口→Gate1→升版整套空转)。

**解法:双通道输出**。每个概念二选一:
- **通道 A（对齐已知）**：确属清单某类 → 打该 type;
- **通道 B（提议新类型）**：重要但清单哪个都不贴切 → **不许硬塞、不许丢弃**,放 `out_of_schema`,并让模型**自己提议新类型名 + 理由**。通道 B 即逃生口入口。

**灵魂措辞（防框死,prompt 必含)**："下列类型清单仅作**参考**,**不一定完整**;若某概念重要却不属于任何一类,**宁可标成『新类型待定』,也不要勉强归到最接近的旧类型**。"——把模型默认偏好从"对齐"扳到"诚实"。

**输出 schema（示意）**：
```json
{
  "entities": [{"name": "SMF", "type": "network_element", "confidence": 0.95}],
  "out_of_schema": [{"name": "网络切片", "proposed_type": "concept",
                     "reason": "反复出现的核心架构概念,清单无对应类型",
                     "evidence": "原文摘录…"}]
}
```
额外:给 in-schema 实体一个 `confidence`,**低置信(模型其实不确定)的也转入逃生口复核**,进一步防硬塞。

**新类型三道闸（模型无权直接改本体,呼应 §14.6 实例/类型两层）**：
1. **prompt**：只负责"诚实标 + 提议",广撒网;
2. **B5 跨文档统计**：同一 proposed_type ≥2 篇文档复现才够格(df≥2 去噪,只冒一次当噪音弃);
3. **Gate1 人审**：评审页拍板,通过才升版进正式本体。

prompt 模板落点:enrich 模板 `mining-segment-understanding` **瘦身**(删 entities,只留 semantic_role + content_assessment);**新建** `mining-entity-extraction` 模板(喂类型表 + 双通道)。

### 15.3 Pipeline 阶段重新设计

**现状 → 拆分后**（per-document,`MiningPipeline.process_document`）：

| 现状阶段 | 拆分后 |
|---------|--------|
| enrich（角色+质量+**实体**） | enrich（角色+质量) |
| — | **entity_extract（新,双通道实体抽取,喂本体类型表)** |
| resolve | resolve |
| entity_relations | entity_relations |
| (seg_ids) | (seg_ids) |
| discourse/RST | discourse/RST |
| build_retrieval_units | build_retrieval_units |

**两条线视角**(分组管理,服务"好演进"):
- **篇章线**：enrich → discourse/RST → build_retrieval_units（吃原文,不依赖实体）;
- **本体线**：entity_extract → resolve → entity_relations →（全局)graph_write（实体抽取打头,环环相扣）。

**落点**：
- 新建 `stages/entity_extract/__init__.py`（`EntityExtractor` 类,构造同 LlmEnricher 传 `ontology_store`+`domain_id` 以读 active 类型表)。
- `PipelineConfig` 加字段 `entity_extractor`（`pipeline.py:99` 附近)。
- `process_document` 在 enrich 之后、resolve 之前插 `entity_extract` stage（`pipeline.py:180` 之后)；streaming 路径 `stages` 列表(`pipeline.py:300` 附近)同步加。
- `jobs/run.py` 造 `EntityExtractor` 放进 llm dict（`:435` 附近)、`PipelineConfig(...)` 注入（`:615` 附近)。
- enrich `_apply_llm_result` 删实体/逃生口分支(搬去新阶段);新阶段承接 entity_refs_json + out_of_schema 写入。

### 15.4 前端显示重新设计

- **Run 详情页阶段进度**(`RunDetailView.vue`)：阶段列表插入 `entity_extract`,并按 **篇章线 / 本体线** 两组展示,让用户看清两条线各自进度与产出。
- **trace 接口**(`api/routes/runs.py` 的 `/{run_id}/trace`)：补 `entity_extract` 的产出计数(抽出实体数 / 逃生口候选数),前端据此显示。
- **阶段名映射**：前端阶段中文名表 + 若有 StageBadge 类组件,加 `entity_extract`(本体线)、明确 `enrich` 归篇章线。

### 15.5 逃生口可视化（原"第 5 点",增强 Gate1 评审页）

**现状**：Gate1 评审页(`OntologyReviewView.vue`)只显示候选名 + "X 条证据" + 打分,**看不到**热度、判为本体外的理由、原文——光凭名字难决定该不该收进本体。逃生口产物其实就是 `ontology_candidates` 表 `source='escape_hatch'` 的行(payload 含 tf/df/reasons、evidence 含 quote)。

**增强(纯前端,后端零改)**：
- **来源标签列**：`escape_hatch`(逃生口)候选醒目标注,与其它来源区分。
- **热度列**：`出现 N 次 / M 篇`(读 payload 的 tf/df),高频优先看。
- **可展开行**：展开显示 判为本体外的理由(reasons)、建议归到的类型(proposed_type/node_type)、原文摘录列表(evidence 的 quote + 片段)。

**落点**：
- 后端：`list_candidates` 已 `SELECT *` 返回 `source`/`payload_json`/`evidence_json`,**零改动**。
- 前端：`types/index.ts` 的 `OntologyCandidate` 补 `source` 字段；`OntologyReviewView.vue` 加来源/热度列 + 展开行。

### 15.6 影响文件清单（待实现,汇总）

| 模块 | 文件 | 动作 |
|------|------|------|
| 实体抽取阶段 | `stages/entity_extract/__init__.py` | 新建 EntityExtractor(双通道,喂类型表) |
| enrich 瘦身 | `stages/enrich/__init__.py` | 删实体/逃生口分支,只留角色+质量 |
| prompt 模板 | `mining-segment-understanding` / 新 `mining-entity-extraction` | 瘦身 / 新建(双通道) |
| 管线编排 | `pipeline.py` | PipelineConfig 加字段 + process_document/streaming 插阶段 |
| 组装 | `jobs/run.py` | 造 EntityExtractor + 注入 cfg |
| Run 详情前端 | `RunDetailView.vue` | 阶段进度加 entity_extract,篇章线/本体线分组 |
| trace 接口 | `api/routes/runs.py` | /trace 补 entity_extract 计数 |
| 逃生口可视化 | `OntologyReviewView.vue` + `types/index.ts` | 来源/热度列 + 展开行 + 补 source 字段 |
| 测试 | `test_enrich_ontology_constraint.py` 等 | 实体部分拆出 + entity_extract 新测 |

### 15.7 建议实施顺序（分批,降低风险）

1. **批一:逃生口可视化**(15.5)——纯前端、后端零改、独立,先落地见效。
2. **批二:实体抽取拆阶段**(15.1–15.3)——后端核心,含新 prompt 设计与编排;先保证下游接口不变、回归测试过。
3. **批三:前端阶段重设计**(15.4)——依赖批二的新阶段与 trace 字段。

> 待 §14（Gate2 增强）与本节方案经用户确认,即按批次开工;每批完工回填"实现实录"。
>
> **进度（2026-06-11）**：§15 三批已全部实现并通过测试,实现实录见 **§16**。§14（Gate2 增强）仍为计划稿,未开工。

---

## 16. §15 三批实现实录（2026-06-11，已落地）

> 对应 §15 方案。三批均已实现 + 测试通过。下游 resolve / entity_relations / graph_write 接口未动（仍读 `entity_refs_json` + `meta["out_of_schema"]`），爆炸半径如预期被控制在"谁写这俩字段"。

### 16.1 批一：逃生口可视化（对应 §15.5）

**意图**：Gate1 评审页从"只有名字 + 打分"升级为能看见**来源 / 热度 / 判为本体外的理由 / 原文摘录**,让人审有据可依。纯前端,后端零改。

**文件清单**
- `kb-ui/src/views/knowledge/OntologyReviewView.vue` — 加列 + 可展开行。
- `kb-ui/src/types/index.ts` — `OntologyCandidate` 已有 `source` 字段,**无需改**（核对后确认）。

**关键实现**
- 新增 **来源列**（chip：`escape_hatch`→"逃生口",橙色 `.chip--escape`）、**热度列**（节点候选 `出现 N 次 / M 篇`,读 payload 的 tf/df；关系候选 `共现 N 次`,读 cooccur）。
- `el-table-column type="expand"`（配 `row-key="id"`）展开显示：判为本体外的理由（`reasonLabel` 中文化）、建议归到的类型（node_type / proposed_type）、关系示例、证据原文 quote 列表。
- 脚本辅助：`SOURCE_LABELS`/`sourceLabel`、`REASON_LABELS`/`reasonLabel`、`payloadOf`/`payloadStr`/`payloadNum`/`payloadArr`、`reasonsOf`、`quotesOf`；类型 `Payload`、`Evidence`。

**测试/验证**：前端 `npx vue-tsc --noEmit` 通过。

**取舍**：后端 `list_candidates` 本就 `SELECT *` 返回 `source`/`payload_json`/`evidence_json`,确认零改动——批一能独立先发就是因为这点。

### 16.2 批二：实体抽取拆成独立阶段 + 双通道（对应 §15.1–§15.3）

**意图**：把寄生在 enrich 的实体抽取拆成独立 `entity_extract` 阶段,自己一次 LLM 调用、把 active 本体类型表喂进 prompt、双通道输出（对齐已知 / 提议新类型）。enrich 回归篇章本职。

**文件清单**
- `knowledge_mining/mining/stages/entity_extract/__init__.py`（**新建**）— `EntityExtractor`（stage_name=`entity_extract`,version `1`）。
- `knowledge_mining/mining/stages/enrich/__init__.py`（**瘦身**）— stage_version `2`→`3`,卸掉实体/逃生口/`ontology_store`/`domain_id`/`_resolve_allowed_types`。
- `knowledge_mining/mining/pipeline.py` — `PipelineConfig` 加 `entity_extractor` 字段；`process_document` 在 enrich 后、resolve 前插 entity_extract；新增 `entity_extract_stage(ctx, cfg)`。
- `knowledge_mining/mining/jobs/run.py` — 造 `EntityExtractor`（带 ontology_store + domain_id）注入 cfg；LlmEnricher 构造去掉 ontology_store/domain_id；streaming `stages` 列表在 enrich 与 resolve 间插 `entity_extract`。
- `knowledge_mining/mining/infra/llm_templates.py` — 去掉对 segment-understanding 的实体枚举注入,只保留 semantic_role 枚举；新实体抽取模板**刻意不注入枚举**（避免静态/运行期漂移、保住通道 B）。
- `scenario_packs/cloud_core_network/domain.yaml` — `mining-segment-understanding` 瘦身（version `4`→`5`,删 entities,只留 semantic_role/document_type/content_assessment）；**新建** `mining-entity-extraction`（version `1`,双通道,喂 `$allowed_types`）。
- `databases/mining_runtime/schemas/002_mining_runtime_postgresql.sql` + `001_mining_runtime.sqlite.sql` — `mining_run_stage_events` 的 stage CHECK 增加 `'entity_extract'`。

**关键实现**
- `EntityExtractor._resolve_allowed_types()` 读 active 本体点类型,读不到回退 `profile.entity_types`；`extract_batch` 跳过 tiny 段（< `min_enrich_tokens`）,提交 `mining-entity-extraction` 模板,input `{text, section_title, block_type, allowed_types}`,`pipeline_stage="entity_extract"`。
- `_apply_entity_result`（`_MIN_ENTITY_CONFIDENCE=0.5`）：
  - **通道 A** in-schema 且置信达标 → `entity_refs_json`；off-schema → 逃生口 `reason="off_schema_type"`；低置信 → 逃生口 `reason="low_confidence"`。
  - **通道 B** `out_of_schema` 映射 `{"type": proposed_type or type or "concept", "name", "reason": "llm_out_of_schema"}` + 附 `proposed_reason`/`evidence`。
  - **关键兼容点**：graph_write 消费 `meta["out_of_schema"]` 只认 `name`/`type`/`reason`,故必须把 `proposed_type` 映射成 `type`（不能只写 proposed_type）——否则下游落图把新类型当默认 `concept` 吞掉。
- enrich `_apply_llm_result(seg, result, valid_roles)` 现在只管 semantic_role + `meta["llm_document_type"]` + `meta["content_assessment"]`。

**测试**（均 passed）
- `tests/test_entity_extract.py`（**新建**,6 测）：in/off-schema 分流、低置信分流、缺 confidence 放行、通道 B `proposed_type→type` 映射、空 allowed 不过滤、无改动同实例。
- `tests/test_enrich_ontology_constraint.py`（**重写**）：瘦身后 enrich 只产角色/质量、不再产实体（`assert list(out.entity_refs_json) == []`）。
- `tests/test_v14_domain_pack.py`、`tests/test_pipeline_operators.py`（更新）：segment-understanding 无 entities、entity-extraction 双通道存在。
- 全量回归 135 passed。

**取舍/偏差**
- 离线挖掘 + 本地 claude_cli provider（无 API key 成本）,故"多一次 LLM 调用"可接受,收益来自双通道把类型表喂进 prompt 抽得更准、逃生口判得更干净。
- 新模板**不加 entities[].type 枚举约束**：靠 prompt + 抽后过滤,避免静态枚举与运行期 active 本体漂移、且保住通道 B 发现新类型。
- DDL 只写文件,**未动远程库**（按用户约束,等其重建库重新入库时生效）。

### 16.3 批三：前端阶段重设计（对应 §15.4）

**意图**：Run 详情页阶段进度插入 `entity_extract`,并按**篇章线 / 本体线**两组展示,trace 接口补逃生口候选计数。

**文件清单**
- `kb-ui/src/components/mining/PipelineFlow.vue` — `PipelineStage` 加 `line?: 'discourse'|'ontology'`;stages 数组加 entity_extract/resolve/entity_relations/graph_write 并打线标;渲染"本体线/篇章线"徽标;补图标与 `.pipeline-stage__line*` 样式。
- `kb-ui/src/views/mining/RunDetailView.vue` — `stageLabel` 加 entity_extract/resolve/entity_relations/graph_write 中文名,`enrich`"增强"→"段落理解";PipelineFlow 下加"本体线产出"汇总块（实体数 / 关系数 / 逃生口候选,带 router-link 到 `/ontology/review`）+ `.ontology-line-stats*` 样式。
- `knowledge_mining/mining/api/routes/runs.py` — `/{run_id}/trace` 加 `escape_hatch_candidates`（COUNT `ontology_candidates` WHERE domain_id=… AND source='escape_hatch'）。
- `kb-ui/src/types/index.ts` — `RunTrace` 加 `escape_hatch_candidates?: number`。

**测试/验证**：前端 `npx vue-tsc --noEmit` 通过（exit 0）。

**取舍**："本体线产出"汇总块用 `v-if="trace"` 包裹——trace 是叠加视图,取失败不影响主流程（沿用 RunDetailView 既有容错）。

---

## 17. Run 进度/阶段状态两处修正（2026-06-11，实测回归）

> §15 三批上线后实测发现两处显示失真：① 进度条 100% 时底下 Pipeline 还在跑；② 语篇分析阶段永久卡"运行中"。根因同一处——**前端把"文档维度"和"阶段维度"两套口径混用**,且阶段状态在并行流式下被"孤儿事件"带偏。两处均已修。

### 17.1 进度条纳入全局尾段（不再"文档满格=100%"）

**问题**：进度条 `progress_percent` 原口径 = `(committed+failed+skipped)/total_documents`,纯文档维度。但落图（graph_write）+ 建库发布（assemble_build/validate_build/publish_release）是**全局尾段**——所有文档提交后才整体跑一次（见 §2.1，跨文档统计只能等全量）。这些尾段的 stage event 是 `run_document_id IS NULL`,而进度查询 `WHERE run_document_id IS NOT NULL` 把它们整个排除,导致"18 篇全提交→100%,可落图/建库其实还在跑"。

**修法**（`knowledge_mining/mining/api/routes/runs.py` 的 `/{run_id}/progress`）：
- 新增查询：全局尾段（`run_document_id IS NULL` 且 `status='completed'`）已完成的 stage 集合。
- 改口径为**单元制**：`total_units = 文档数 + 4 个全局尾段`;`done_units = (committed+failed+skipped) + 已完成尾段数`;`progress_percent = done_units/total_units`。于是"18 篇全提交"只到 `18/22 ≈ 81.8%`,落图/建库/发布各完成才继续爬。
- **封顶规则**：只有 `run.status == 'completed'` 才强制 100.0;尾段在跑、或尾段被跳过（无 active 本体跳过落图、分子凑不满）时封顶 99.9%,杜绝误导性满格。
- 返回体加 `global_done_stages` 字段（已完成的全局尾段列表,便于前端/排查）。

**前端配套**（`kb-ui/src/views/mining/RunDetailView.vue`）：彩条原 `pctCompleted = completed/total`（文档维度,会满格）改为 `Math.round(progress_percent) - pctFailed`,让**彩条宽度与右侧百分比数字同口径**。

**取舍**：尾段固定列 4 个（`GLOBAL_TAIL_STAGES`）。Gate 暂停（awaiting_review）时建库阶段不跑,尾段只完成落图 1 个 → 进度自然停在 ~86%,既非 100% 也直观反映"卡在评审、尚未建库",符合预期。

### 17.2 修阶段永久卡"运行中"（并行流式的孤儿 started 事件）

**问题**：`PipelineFlow.vue` 的 `getStageStatus` 用 `started.length > completed.length` 判"运行中"。每篇文档每阶段发一对 started/completed,18 篇并行跑,只要**有一篇的 discourse 只发了 started、没发 completed**（那篇在该步失败/事件丢失）,全局聚合就 `started(18)>completed(17)` → 该阶段永久"运行中",哪怕其它 17 篇早已流到下游、下游阶段都显示完成了。

**修法**（`kb-ui/src/components/mining/PipelineFlow.vue`）：
- 给 `PipelineStage` 加 `scope?: 'document'|'global'`,把 graph_write、build 标 `global`（它们本就在文档落定后才跑,不能套用下面的豁免）。
- 组件加 `allDocsSettled` prop（由 RunDetailView 算：`processing===0 && committed+failed+skipped>=total` 时为真）。
- `getStageStatus` 新规则：**逐文档阶段**一旦 `allDocsSettled`,孤儿 started 不再钉死——只要本阶段有 ≥1 条 completed 即判 `completed`;**全局尾段**不豁免,仍按真实 started/completed 判（落图正在跑就该显示运行中）。

**取舍**：没有引入"下游阶段已完成就反推上游完成"这类启发式——并行流式下多个阶段可合法地同时在跑,下游有 completed 不代表上游必然结束。改用"全部文档落定"这个更硬的信号做豁免边界,避免误判。

### 17.3 验证

- 后端 `runs.py` 通过 `ast.parse` 语法校验;`/progress` 口径改动不影响 `test_full_integration.py`（该用例 run 为 completed 态,走强制 100.0 分支）。
- 前端 `npx vue-tsc --noEmit` 通过（exit 0）。

> DDL 未涉及;两处均为读侧聚合逻辑修正,不动远程库。

---

## 18. Gate2 多选批量裁决（对应 §14.4 ③，2026-06-11，已落地）

> 实现 §14.4 的第③点：Gate2 评审页一次勾选多条提及，批量 丢弃 / 新建 / 合并到同一已有对象。①证据原文（14.2）、②相似实体推荐（14.3）仍待做。

**意图**：提及多时逐条点太累。勾一批，套同一裁决。按 §14.1 拍板项③——"批量合并"只做"并到同一已有对象"；"彼此聚成新对象"由"批量新建"覆盖（`upsert_entity` 按 canonical_name 聚合，同名自动并）。

**文件清单**
- `knowledge_mining/mining/infra/ontology_store.py` — `GraphStore.resolve_mentions_batch(mention_ids, *, action, entity_id, domain_id, node_type)`：循环复用单条 `resolve_mention`，逐条收集结果，**单条失败不阻断其余**（每条 `_execute` 各自 auto-commit，与单条一致）。
- `knowledge_mining/mining/api/routes/ontology.py` — `ResolveBatchRequest` 模型 + `POST /api/mentions/resolve-batch`；校验 action ∈ {merge,new,reject}、merge 必带 entity_id、mention_ids 非空；返回 `{action,total,ok,failed,results}`。
- `kb-ui/src/api/mining.ts` — `resolveMentionsBatch(body)`。
- `kb-ui/src/views/knowledge/MentionReviewView.vue` — 表格加 `type="selection"` 勾选列（`reserve-selection` + `row-key="id"`）+ `@selection-change`；勾选 ≥1 条时浮出批量操作栏（批量新建 / 批量合并到已有 / 批量丢弃 / 取消选择）；批量合并复用既有"搜索已有对象"弹框（`batchMerging` 标志区分单条/批量）；批量丢弃前 `ElMessageBox.confirm` 二次确认；结果按 `ok/failed` 提示。

**关键实现/取舍**
- **零重写后端逻辑**：批量就是单条的 for 循环，裁决语义（merge/new/reject）完全沿用 `resolve_mention`，避免两套实现漂移。
- **容错**：循环内 try/except 收集每条 `{ok, resolved_entity_id|error}`，部分失败照样把成功的提交掉，前端提示"成功 N 条、失败 M 条"。
- **合并语义**：批量 merge 时所有选中提及共用同一个 `entity_id`（弹框选一个目标对象），符合拍板③。

**测试**（`knowledge_mining/tests/test_review_gate.py`，新增 3 测，全文件 20 passed）
- `test_resolve_batch_reject_all`：3 条全 reject、各发 rejected 语句、resolved_entity_id 均 None。
- `test_resolve_batch_merge_to_same_entity`：2 条并到同一 `e-smf`。
- `test_resolve_batch_continues_on_error`：`[m1, bad, m2]` 中间条失败（mention 不存在）→ 结果 `[ok, fail, ok]`、失败条带 `error`，不抛断。

**验证**：后端 20 passed；前端 `npx vue-tsc --noEmit` 通过（exit 0）。DDL 未涉及，不动远程库。

---

## 19. Gate2 证据原文 + 相似实体推荐（对应 §14.2 ① / §14.3 ②，2026-06-11，已落地）

> 补齐 Gate2 评审增强的另外两点，至此 §14 ①②③ 全部落地。

### 19.1 ① 提及带出证据原文（§14.2）

**意图**：每条待确认提及旁能看原文上下文，便于判断它在特定语境下指什么。

**文件清单**
- `knowledge_mining/mining/infra/ontology_store.py` — `pending_mentions` / `pending_mentions_for_run` 两个查询都加 `LEFT JOIN asset_raw_segments s ON s.id = m.segment_id`，带出 `s.raw_text AS segment_text`、`s.section_title AS segment_section`。`pending_mentions` 抽出公共 `_PENDING_SELECT` 前缀避免重复。
- `kb-ui/src/types/index.ts` — `PendingMention` 加 `segment_text?` / `segment_section?`。
- `kb-ui/src/views/knowledge/MentionReviewView.vue` — 表格加 `type="expand"` 展开行，显示段标题 + 原文（`white-space: pre-wrap`）；无原文时提示"段落已重建"。

**取舍**：用 `LEFT JOIN`（非 INNER）——段落被重建/清掉时 mention 仍要能评审，只是没原文。改动小、风险低。

### 19.2 ② 实时推荐相似的已有实体（§14.3）

**意图**：列表每条提及旁直接列"库里可能是同一个的对象"，点一下即合并，省掉先开弹框再搜索的绕路。

**文件清单**
- `knowledge_mining/mining/infra/ontology_store.py` — `GraphStore.suggest_entities_for_mention(domain_id, *, mention_text, node_type, limit=5)`：`WHERE domain_id=%s AND node_type=%s AND (canonical_name ILIKE %s OR %s ILIKE '%%'||canonical_name||'%%')`，**双向包含**（实体名含提及 OR 提及含实体名，后者让"SMF会话"命中已有"SMF"），按 `mention_count DESC` 取 top-N；按拍板①**限同 node_type**。空名字/类型短路返回 `[]`。
- `knowledge_mining/mining/api/routes/ontology.py` — `GET /api/mentions/suggest`（mention_text / node_type / domain / limit）。
- `kb-ui/src/api/mining.ts` — `suggestEntities(params)`。
- `kb-ui/src/views/knowledge/MentionReviewView.vue` — 列表加载后 `prefetchSuggestions()` 按拍板②**并发预取**每条 top-3（`Promise.all`，不阻塞渲染、失败静默）；"推荐对象"列渲染成可点 `el-tag`（`名字 · 提及数`），点击 = 直接 merge 到该对象。

**局限（已知 TODO，沿用 §14.3）**：纯字面匹配，"会话管理功能↔SMF"这类语义异形抓不到；后续接别名词典（`ontology_alias_dictionary`）或向量相似度增强。

### 19.3 测试 / 验证

- `knowledge_mining/tests/test_review_gate.py` 新增 2 测（全文件 **22 passed**）：
  - `test_suggest_entities_bidirectional_match`：校验 SQL 含双向包含 + `ORDER BY mention_count DESC`，params = `("dom","network_element","%SMF会话%","SMF会话",3)`。
  - `test_suggest_entities_empty_inputs_short_circuit`：缺名字/类型不查库。
- 前端 `npx vue-tsc --noEmit` 通过（exit 0）。DDL 未涉及，不动远程库。

---

## 20. 前端体验两处修正（2026-06-11，纯前端）

> 实测反馈：① 批量操作等后端时没有反馈；② Gate1 评审页会点亮左侧"本体版本"导航、且无返回入口，不像 Gate2 那样是挖掘流程的临时子页。

### 20.1 批量操作转圈反馈（`MentionReviewView.vue`）

- 加 `busy` 状态，包裹 `batchNew`/`batchReject`/`confirmMerge`（含批量合并）/`applySuggestion`（点推荐标签即合并）的后端调用（try/finally）。
- 绑定：表格 `v-loading="loading || busy"`（`element-loading-text="处理中…"`）、批量栏三个按钮 `:loading="busy"`、合并弹框"确认合并"按钮 `:loading="busy"`、取消/取消选择 `:disabled="busy"`。等后端期间整表转圈 + 按钮转圈，杜绝重复点击。

### 20.2 Gate1 改造成挖掘流程的临时子页（仿 Gate2）

**问题根因**：Gate1 路由原为 `ontology/review`，落在 `/ontology/` 前缀下；侧栏 `isActive('/ontology')` 用 `route.path.startsWith('/ontology/')` 判定，于是点进 Gate1 时"本体版本"菜单被点亮，像是离开了挖掘流程进了本体页；且页面无返回入口。Gate2（`mentions/review`）不在任何导航前缀下，故天然像临时子页。

**修法**：
- `router/index.ts`：Gate1 路由 `ontology/review` → **`candidates/review`**（路由名 `ontology-review` 不变，无其它引用）。脱离 `/ontology/` 前缀，侧栏不再点亮"本体版本"。
- `OntologyReviewView.vue`：仿 Gate2——从 `route.query.run_id` 读 `runId`，标题副文案带 `· run xxxxxxxx`，右上角加 `返回 Run 详情`（`router-link` 到 `/mining/{runId}`，`v-if="runId"`）；加 `watch(route.query.run_id)`。
- `RunDetailView.vue`：两处指向 Gate1 的链接（评审横幅"去评审本体候选" + 本体线产出"逃生口候选"）都改成 `/candidates/review?run_id=${runId}`，带上 run_id 以支持返回。

**验证**：前端 `npx vue-tsc --noEmit` 通过（exit 0）。纯前端 + 路由，无后端/DDL 改动。

---

## 22. 修复：Gate2 人工确认实体不写出处（2026-06-11）

> 实测发现"确认过的实体在出处回链里连不到原文段落"。排查确认是真漏洞，修复并补测试。

### 22.1 根因
- 实体↔段落的链接，表结构层面本就有两座桥：`asset_segment_entity_mentions`（`segment_id` + `resolved_entity_id`）与 `ontology_evidence_nodes`（`target_kind='entity'` + `segment_id`，权威出处）。`ontology_entities` 本身无 segment 列——按设计（实体是跨文档聚合，一对多）。
- 但 `ontology_evidence_nodes` 的**实体出处行只在 `graph_write` 里、且只给"自动归一(auto)"的实体写**（`graph_write/__init__.py:166/274`）。`add_evidence` 全仓库仅 `graph_write` 调用。
- **Gate2 人工确认**（`resolve_mention` 的 new/merge）只建 `domain_entity` + 回填 mention 的 `resolved_entity_id`，**从不写 `ontology_evidence_nodes`**；且落图不在 Gate2 后重跑。
- 早期别名词典稀疏 → 绝大多数实体走人工确认 → 实体普遍**没有出处行** → `evidence_for_target(实体id)` 返回空。违反 L1 §7"出处强制"与 L4 §14.5"权威出处=ontology_evidence_nodes"。

### 22.2 修法（A 方案，最小改动）
- `knowledge_mining/mining/infra/ontology_store.py` — `resolve_mention` 在 new/merge 成功（拿到 `entity_id` + 该提及的 `segment_id`/`document_snapshot_id`）后，补写一条 `ontology_evidence_nodes(target_kind='entity', target_id=entity_id, segment_id=…, quote=mention_text)`。
- 域来源 `ev_domain`：merge 取 `domain_id` 或实体的 `domain_id`；new 取已算出的 `did`。缺 segment/snapshot/domain 时静默跳过（兜底不报错）。
- **多段自然落多行**：同一实体在不同段被确认 → `target_id` 重复、`segment_id` 不同的多行，正是"实体一直携带它出现过的段落集"的落地。批量裁决（`resolve_mentions_batch`）循环复用同一函数，故也自动补出处。

### 22.3 测试 / 验证
- `knowledge_mining/tests/test_review_gate.py` 新增 4 测（全文件 **26 passed**）：
  - `test_resolve_new_writes_entity_evidence` / `test_resolve_merge_writes_entity_evidence`：new/merge 各写一条 entity 出处，含 segment_id/snapshot_id/entity_id。
  - `test_resolve_reject_writes_no_evidence`：reject 不写出处。
  - `test_resolve_without_segment_writes_no_evidence`：提及缺 segment 时静默跳过、不报错。
- 无 DDL 改动（`ontology_evidence_nodes` 表早已存在），不动远程库。

---

## 23. 本体层 4 张表加 ontology_ 前缀（2026-06-11）

> 用户要求把本体概念层里没有 `ontology_` 前缀的表统一加前缀。第 5 张 `asset_segment_entity_mentions` 因是段落级（挂在 asset 段上）保留原名，仅其外键指向更新。

### 23.1 改名映射（4 张）

| 旧名 | 新名 |
|---|---|
| `domain_entities` | `ontology_entities` |
| `domain_entity_relations` | `ontology_entity_relations` |
| `entity_alias_dictionary` | `ontology_alias_dictionary` |
| `evidence_nodes` | `ontology_evidence_nodes` |

`asset_segment_entity_mentions` **不改名**（保留 `asset_` 前缀）；其 `resolved_entity_id` 外键改为 `REFERENCES ontology_entities(id)`。

### 23.2 改动范围
- **DDL**：`databases/ontology/schemas/001_ontology_concept_postgresql.sql` —— CREATE TABLE、外键 REFERENCES、内嵌表名的索引（`idx_domain_entities_*`→`idx_ontology_entities_*`）。`idx_der_*` / `idx_ev_*` / `chk_der_*` 等缩写名不含表名字面量，保持不变（仍有效）。
- **代码**（SQL 字符串 + 注释）：`mining/infra/ontology_store.py`、`api/routes/runs.py`、`stages/entity_relations`、`stages/resolve`、`infra/ontology_bootstrap.py`、种子 `scenario_packs/cloud_core_network/ontology_seed/concept.yaml`。
- **测试**：`tests/test_review_gate.py`（断言里的表名同步）。
- **文档**：L1/L2/L3/L4 中的表名引用一并同步，保持设计与实现一致。
- **未涉及**：`graph_write` 用 Store 方法不写裸 SQL，无需改；agent_serving（Java/Python）与前端 TS 不引用这 4 张表，不受影响；本体概念层无 sqlite 版。

### 23.3 验证
- 全仓库 grep：3 个"干净"旧名（`domain_entities`/`domain_entity_relations`/`entity_alias_dictionary`，新名不含旧字面量）残留为 **0**；`evidence_nodes` 仅作为 `ontology_evidence_nodes` 的子串出现。
- `test_review_gate` + `test_graph_write` 共 **33 passed**。
- 远程库未动——按约定，用户用更新后的 DDL 重建库重新入库后生效。

---

## 24. 本体归纳重排：先确认实体、再归纳本体（N1–N5，2026-06-12）

> 落地 L1 §12 / L2 §15 / L3 §10 的 N 系列：把"抽取时直接产类型候选 + Gate1 优先"改成
> "先 Gate2 确认实体 → 用确认过的干净实体归纳类型 → Gate1 确认本体 → 回贴类型 + 建边"。
> 决策①：用哨兵 `node_type='__untyped__'` 表示"已确认是实体、暂无类型"，**零 DDL**。

### 24.1 N1 本体外概念改走 `__untyped__` pending mention
- `stages/entity_extract/__init__.py`：通道 B（off-schema 概念）不再直接产 `ontology_candidates`；
  改写成 `resolve_status='pending'`、`node_type='__untyped__'` 的 mention，`metadata` 记 `proposed_type`/`off_schema_reason`，进 Gate2 待确认列表。

### 24.2 N2 实体聚合前移 + Gate2 收"暂无类型"
- `stages/graph_write/__init__.py`：把落图拆成两步——
  `persist_entities_and_mentions`（全局A：实体 + 计数 + 出处 + mention + 关系候选，**不建边**）与
  `persist_edges`（事实边，后移到 Gate1 之后）。`MentionRec` 增 `metadata`，`aggregate_build` 透传 untyped 元信息。
- `MentionReviewView.vue`：类型列对 `__untyped__` 渲染"暂无类型"灰标 + "建议：xxx"提示（读 `metadata_json.proposed_type`）。

### 24.3 N3 `ontology_induction` 阶段（LLM 调用 2）
- 新 `stages/ontology_induction/__init__.py`：`OntologyInductor.induce()` 读已确认的 `__untyped__` 实体
  （`GraphStore.confirmed_untyped_entities`），<2 条则跳过；调 `mining-ontology-induction` 模板归纳类型，
  `_build_type_candidates`（纯函数，已测）按 canonical_name 大小写/空白不敏感对回实体、算 DF/support、DF<2 去噪，
  upsert 成 `ontology_candidates(source='global_induction')`，payload 存 definition/examples/layer/member_entity_ids/df/support。
- `scenario_packs/cloud_core_network/domain.yaml`：加 `mining-ontology-induction` 模板（输入 `$untyped_entities`/`$existing_types`）。

### 24.4 N4 两检查点编排 + 反转闸序 + 建边后移
- `jobs/run.py`：
  - `_check_review_gate` **反转**：Gate2（pending mention）在前、Gate1（proposed 候选）在后；都无→None（快速通道）。拆出 `_has_pending_mentions`/`_has_proposed_candidates` 两个判定。
  - 首跑收尾改成分档：有 pending→停 `gate2_entity`；否则跑 `_run_induction`→有候选→停 `gate1_ontology`；都无→`_finalize_graph` 后再建库。
  - `resume` 分档：仍有 pending→留 Gate2；上一步停在 `gate2_entity` 且 pending 已清→跑 `_run_induction` 产候选；有候选→留 Gate1；都清→`_finalize_graph` + `_finalize_run`。`llm_base_url` 从 `MiningConfig().llm_service_url` 取。
  - 流水线 stages 去掉 `entity_relations`（关系改到收尾从 DB 重聚合）。
- `_run_graph_write` 改为只跑全局A（不建边）。

### 24.5 N5 Gate1 通过后回贴类型 + 收尾建图
- `GraphStore.resolved_mentions_for_run(run_id)`：取某 run 全部已确认 mention → 连其归一实体的**当前** node_type/canonical_name + 段原文，作为终态建边输入。
- `GraphStore.rebind_untyped_entities(domain, members)`：把成员实体 `__untyped__`→正式类型名，带 `node_type='__untyped__'` 守卫保证幂等、撞名冲突跳过记日志。
- `OntologyStore.accepted_node_type_members(domain)`：从 accepted 的归纳候选 payload 摊平出 `(entity_id, 批准类型名)` 名单。
- `OntologyStore.promote_accepted_candidates`：升版追加 node_type 时一并保留候选 payload 的 definition/layer/examples。
- `jobs/run.py:_finalize_graph`：①回贴（必须在读 mention 当前类型之前）→ ②`reaggregate_edges`（从 DB 已确认 mention 重聚合候选边、按 active 本体 allowed_pairs + NPMI）→ `persist_edges`。边只连"已确认且类型已定"的对象。

### 24.6 测试 / 验证
- `tests/test_graph_write.py`（N2，重写）、`tests/test_ontology_induction.py`（N3，新建 6 测）、`tests/test_review_gate.py`（N4 闸序反转改测 + N5 回贴/成员名单新增 4 测）。
- 反转闸序测试：`test_gate2_takes_priority_over_gate1` / `test_gate1_when_no_pending_mentions`。
- 前端 `vue-tsc -b` 通过（exit 0）。
