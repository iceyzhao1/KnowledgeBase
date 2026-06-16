# 实体图谱页（功能①）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已有的"实体图谱"页上加 **合并 / 删除 / 改本体节点（改类型）** 三个动作，改完按 scoped 策略立即重算受影响的边。

**Architecture:** 边是从提及表算出来的（NPMI），不能接线。所以每个 mutation 都走"改提及 → 重算受影响子图的边"。后端在 `GraphStore` 加 mutation + 一个 scoped 重算编排函数，复用现成的 `reaggregate_edges`/`persist_edges`；前端在现有 `EntityGraphView.vue` 上加选择 + 动作面板 + 确认高亮 + 重算状态。

**Tech Stack:** Python（psycopg + FastAPI + pytest，测试连真实远程 PG）；Vue 3 + Element Plus + TypeScript + ECharts（已有 `ForceGraph.vue`）。

**设计依据：** [docs/superpowers/specs/2026-06-15-entity-graph-page-design.md](../specs/2026-06-15-entity-graph-page-design.md)

---

## 关键现状（已核实，写代码前必读）

- 已存在：`EntityGraphView.vue`（路由 `/entities`）、`GET /api/graph/entities`、`GET /api/graph/entities/{id}/neighbors`、`ForceGraph.vue`、类型 `GraphEntity`/`EntityNeighbors`。**可视化已经有了，本计划只加"改"。**
- `reaggregate_edges(mention_rows, *, domain_id, relation_builder, npmi_threshold) -> (BuildGraph, entity_ids_map)`，`mention_rows` 每项需含：`document_snapshot_id / segment_id / entity_id / node_type / canonical_name / quote`（见 `resolved_mentions_for_run` 的 SELECT 形状）。
- `persist_edges(graph_store, bg, entity_ids, *, domain_id, ontology_version_id) -> int` 只**新增**过闸边，不删旧边。所以重算前必须先删旧边。
- `EntityRelationBuilder(ontology_store=OntologyStore(pool), domain_id=...)` 即可构造关系抽取器。
- 实体唯一键 `(domain_id, node_type, canonical_name)`；改名/改类型撞键 → 视为合并。
- 边表 `ontology_entity_relations(head_entity_id, tail_entity_id, relation_type, ...)`；提及表 `asset_segment_entity_mentions(resolved_entity_id, segment_id, document_snapshot_id, resolve_status, ...)`。
- 测试夹具 `asset_db`（conftest.py）提供 `AssetCoreDB`，`asset_db.pool` 可建 `GraphStore`/`OntologyStore`；每个测试前后 TRUNCATE 全表。
- 路由依赖注入：`pool = request.app.state.sync_pool`；`OntologyStore(pool), GraphStore(pool)`；同步 Store 调用用 `await _run(fn, ...)` 丢线程池。

## 文件结构

- 改：`knowledge_mining/mining/infra/ontology_store.py`（GraphStore 加 4 个 mutation + 2 个查询/删除辅助）
- 改：`knowledge_mining/mining/stages/graph_write/__init__.py`（加 `scoped_recompute` 编排函数）
- 改：`knowledge_mining/mining/api/routes/ontology.py`（加 3 个 mutation 端点）
- 新：`knowledge_mining/tests/test_entity_mutations.py`（store 层测试）
- 新：`knowledge_mining/tests/test_scoped_recompute.py`（重算编排测试）
- 改：`kb-ui/src/api/mining.ts`（加 3 个 mutation 方法）
- 改：`kb-ui/src/types/index.ts`（加 mutation 请求/响应类型）
- 改：`kb-ui/src/views/knowledge/EntityGraphView.vue`（选择 + 动作面板 + 确认高亮 + 重算状态）

---

## Task 1: scoped 重算的取数 —— `resolved_mentions_around_entities`

scoped 重算要拿"受影响实体所在段落里的全部提及"（邻居由此自然带出）。新增一个 GraphStore 查询，形状与 `resolved_mentions_for_run` 一致，但按 domain + 一组 entity_id 所在段落过滤。

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（GraphStore 内，紧跟 `resolved_mentions_for_run` 之后）
- Test: `knowledge_mining/tests/test_entity_mutations.py`

- [ ] **Step 1: 写失败测试**

```python
# knowledge_mining/tests/test_entity_mutations.py
"""实体 mutation（合并/改类型/删除）+ scoped 取数的 store 层测试。连真实 PG。"""
from __future__ import annotations

import uuid
from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore

DOMAIN = "cloud_core_network"


def _nid() -> str:
    return uuid.uuid4().hex


def _seed_entity(gs: GraphStore, name: str, node_type: str = "concept_x") -> str:
    eid = _nid()
    gs._execute(
        "INSERT INTO ontology_entities (id, domain_id, canonical_name, node_type, layer) "
        "VALUES (%s, %s, %s, %s, 'concept')",
        (eid, DOMAIN, name, node_type),
    )
    return eid


def _seed_snapshot_segment(gs: GraphStore) -> tuple[str, str]:
    """建一份最小的快照+段落，满足 mention 的外键。返回 (snapshot_id, segment_id)。"""
    batch, doc, snap, seg = _nid(), _nid(), _nid(), _nid()
    gs._execute("INSERT INTO asset_source_batches (id) VALUES (%s)", (batch,))
    gs._execute(
        "INSERT INTO asset_documents (id, source_batch_id) VALUES (%s, %s)", (doc, batch))
    gs._execute(
        "INSERT INTO asset_document_snapshots (id, document_id) VALUES (%s, %s)", (snap, doc))
    gs._execute(
        "INSERT INTO asset_raw_segments (id, document_snapshot_id, raw_text, ordinal) "
        "VALUES (%s, %s, %s, 0)", (seg, snap, "网络切片由 UPF 承载"))
    return snap, seg


def _seed_mention(gs: GraphStore, *, snap: str, seg: str, entity_id: str, name: str) -> None:
    gs._execute(
        "INSERT INTO asset_segment_entity_mentions "
        "(id, document_snapshot_id, segment_id, node_type, mention_text, canonical_name, "
        " resolved_entity_id, resolve_status) "
        "VALUES (%s, %s, %s, 'concept_x', %s, %s, %s, 'human')",
        (_nid(), snap, seg, name, name, entity_id),
    )


def test_mentions_around_entities_pulls_cooccurring(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    a = _seed_entity(gs, "网络切片")
    b = _seed_entity(gs, "UPF")
    c = _seed_entity(gs, "无关实体")
    snap, seg = _seed_snapshot_segment(gs)
    _seed_mention(gs, snap=snap, seg=seg, entity_id=a, name="网络切片")
    _seed_mention(gs, snap=snap, seg=seg, entity_id=b, name="UPF")
    # c 在另一段，不与 a 同段
    snap2, seg2 = _seed_snapshot_segment(gs)
    _seed_mention(gs, snap=snap2, seg=seg2, entity_id=c, name="无关实体")
    asset_db.commit()

    rows = gs.resolved_mentions_around_entities(DOMAIN, [a])
    ids = {r["entity_id"] for r in rows}
    assert a in ids and b in ids        # a 及其同段邻居 b 被带出
    assert c not in ids                  # 不同段的 c 不在范围内
    r0 = next(r for r in rows if r["entity_id"] == a)
    assert set(r0.keys()) >= {"document_snapshot_id", "segment_id", "entity_id",
                              "node_type", "canonical_name", "quote"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_mentions_around_entities_pulls_cooccurring -v`
Expected: FAIL（`AttributeError: 'GraphStore' object has no attribute 'resolved_mentions_around_entities'`）

- [ ] **Step 3: 实现查询方法**

在 `ontology_store.py` 的 GraphStore 里，`resolved_mentions_for_run` 方法之后加：

```python
    def resolved_mentions_around_entities(
        self, domain_id: str, entity_ids: list[str],
    ) -> list[dict[str, Any]]:
        """scoped 重算取数：给定一组实体，返回它们**所在段落**里的全部已确认 mention，
        连到各自归一实体的当前 node_type/canonical_name + 段落原文。

        形状与 resolved_mentions_for_run 一致（供 reaggregate_edges 直接消费）。
        邻居实体由"同段共现"自然带出——这正是 NPMI 需要重算的范围。
        """
        if not entity_ids:
            return []
        return self._fetchall(
            """SELECT m.document_snapshot_id, m.segment_id,
                      e.id AS entity_id, e.node_type AS node_type,
                      e.canonical_name AS canonical_name,
                      s.raw_text AS quote
               FROM asset_segment_entity_mentions m
               JOIN ontology_entities e ON e.id = m.resolved_entity_id
               LEFT JOIN asset_raw_segments s ON s.id = m.segment_id
               WHERE e.domain_id = %s
                 AND m.resolve_status IN ('auto', 'human')
                 AND m.segment_id IN (
                     SELECT DISTINCT m2.segment_id
                     FROM asset_segment_entity_mentions m2
                     WHERE m2.resolved_entity_id = ANY(%s)
                 )
               ORDER BY m.document_snapshot_id, m.segment_id""",
            (domain_id, list(entity_ids)),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_mentions_around_entities_pulls_cooccurring -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_entity_mutations.py
git commit -m "feat(graph): GraphStore.resolved_mentions_around_entities for scoped recompute"
```

---

## Task 2: 删旧边辅助 —— `delete_edges_among`

scoped 重算前要先删"重算集合内两端点之间"的旧边（只删两端都在集合内的，避免误删邻居指向集合外的边 → 无损）。

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（GraphStore）
- Test: `knowledge_mining/tests/test_entity_mutations.py`

- [ ] **Step 1: 写失败测试**

```python
def _seed_edge(gs: GraphStore, h: str, t: str) -> None:
    gs._execute(
        "INSERT INTO ontology_entity_relations "
        "(id, domain_id, head_entity_id, tail_entity_id, relation_type, source_refs_json) "
        "VALUES (%s, %s, %s, %s, 'rel', '[\"x\"]'::jsonb)",
        (_nid(), DOMAIN, h, t),
    )


def test_delete_edges_among_only_inside_set(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    a, b, c = _seed_entity(gs, "A"), _seed_entity(gs, "B"), _seed_entity(gs, "C")
    _seed_edge(gs, a, b)   # 两端都在 {a,b} → 应删
    _seed_edge(gs, b, c)   # 一端 c 在集合外 → 应保留
    asset_db.commit()

    n = gs.delete_edges_among(DOMAIN, [a, b])
    asset_db.commit()
    assert n == 1
    remain = gs._fetchall(
        "SELECT head_entity_id, tail_entity_id FROM ontology_entity_relations WHERE domain_id=%s",
        (DOMAIN,))
    assert len(remain) == 1
    assert {remain[0]["head_entity_id"], remain[0]["tail_entity_id"]} == {b, c}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_delete_edges_among_only_inside_set -v`
Expected: FAIL（无 `delete_edges_among`）

- [ ] **Step 3: 实现**

GraphStore 内加：

```python
    def delete_edges_among(self, domain_id: str, entity_ids: list[str]) -> int:
        """删除两端点都在 entity_ids 内的事实边（scoped 重算前清旧边，无损）。返回删除条数。"""
        if not entity_ids:
            return 0
        ids = list(entity_ids)
        row = self._fetchone(
            """WITH del AS (
                   DELETE FROM ontology_entity_relations
                   WHERE domain_id = %s
                     AND head_entity_id = ANY(%s) AND tail_entity_id = ANY(%s)
                   RETURNING 1
               ) SELECT count(*) AS n FROM del""",
            (domain_id, ids, ids),
        )
        return int(row["n"]) if row else 0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_delete_edges_among_only_inside_set -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_entity_mutations.py
git commit -m "feat(graph): GraphStore.delete_edges_among (scoped pre-clean)"
```

---

## Task 3: scoped 重算编排 —— `scoped_recompute`

把"取数 → 删旧边 → 重聚合 → 落新边"串起来。放在 graph_write，复用 `reaggregate_edges`/`persist_edges`。

**Files:**
- Modify: `knowledge_mining/mining/stages/graph_write/__init__.py`
- Test: `knowledge_mining/tests/test_scoped_recompute.py`

- [ ] **Step 1: 写失败测试**

```python
# knowledge_mining/tests/test_scoped_recompute.py
"""scoped 重算编排：取受影响子图 mention → 删旧边 → 重算 NPMI → 落边。连真实 PG。"""
from __future__ import annotations
import uuid
from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore
from knowledge_mining.mining.stages.graph_write import scoped_recompute

DOMAIN = "cloud_core_network"


def _nid() -> str:
    return uuid.uuid4().hex


def test_scoped_recompute_runs_without_active_ontology(asset_db) -> None:
    """无 active 本体时安静返回 0（不抛、不阻断）——与 graph_write_final 同样的容错。"""
    gs = GraphStore(asset_db.pool)
    eid = _nid()
    gs._execute(
        "INSERT INTO ontology_entities (id, domain_id, canonical_name, node_type, layer) "
        "VALUES (%s, %s, '网络切片', 'concept_x', 'concept')", (eid, DOMAIN))
    asset_db.commit()
    n = scoped_recompute(asset_db.pool, DOMAIN, [eid])
    assert n == 0
```

> 注：完整的"建边数 > 0"端到端断言依赖 active 本体 + allowed_pairs + LLM 关系抽取，成本高；
> store 层 mutation 的正确性由 Task 4/5/6 覆盖，本任务先锁"编排可跑且容错"。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_scoped_recompute.py -v`
Expected: FAIL（`ImportError: cannot import name 'scoped_recompute'`）

- [ ] **Step 3: 实现编排函数**

`graph_write/__init__.py` 末尾加：

```python
def scoped_recompute(pool: Any, domain_id: str, affected_entity_ids: list[str]) -> int:
    """局部重算：只重算受影响实体所在段落涉及的边（NPMI 是全局量但分母不变，
    范围外实体对的边数学上不变，故 scoped 是精确而非近似）。

    流程：取这些实体所在段落的全部已确认 mention → 算出重算集合（含同段邻居）→
    删该集合内的旧边 → reaggregate_edges 重算 → persist_edges 落新边。
    无 active 本体则跳过返回 0（与 graph_write_final 容错一致）。
    """
    from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore
    from knowledge_mining.mining.stages.entity_relations import EntityRelationBuilder

    if not affected_entity_ids:
        return 0
    ostore = OntologyStore(pool)
    active = ostore.active_version(domain_id)
    if active is None:
        return 0
    gstore = GraphStore(pool)

    rows = gstore.resolved_mentions_around_entities(domain_id, affected_entity_ids)
    if not rows:
        return 0
    recompute_ids = {r["entity_id"] for r in rows} | set(affected_entity_ids)
    gstore.delete_edges_among(domain_id, list(recompute_ids))

    rel_builder = EntityRelationBuilder(ontology_store=ostore, domain_id=domain_id)
    bg, entity_ids = reaggregate_edges(rows, domain_id=domain_id, relation_builder=rel_builder)
    return persist_edges(gstore, bg, entity_ids,
                         domain_id=domain_id, ontology_version_id=active["id"])
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_scoped_recompute.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/stages/graph_write/__init__.py knowledge_mining/tests/test_scoped_recompute.py
git commit -m "feat(graph): scoped_recompute orchestrator (gather/clean/reaggregate/persist)"
```

---

## Task 4: 合并实体 —— `merge_entities`

把被并实体的提及全改指主实体，重算计数，删被并实体。返回受影响实体集合（供调用方 scoped 重算）。

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（GraphStore）
- Test: `knowledge_mining/tests/test_entity_mutations.py`

- [ ] **Step 1: 写失败测试**

```python
def test_merge_entities_repoints_and_drops(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    prim = _seed_entity(gs, "UPF")
    dup = _seed_entity(gs, "用户面功能")
    snap, seg = _seed_snapshot_segment(gs)
    _seed_mention(gs, snap=snap, seg=seg, entity_id=dup, name="用户面功能")
    asset_db.commit()

    affected = gs.merge_entities(DOMAIN, prim, [dup])
    asset_db.commit()

    # 被并实体没了
    assert gs._fetchone("SELECT 1 FROM ontology_entities WHERE id=%s", (dup,)) is None
    # 它的提及改指主实体
    m = gs._fetchall(
        "SELECT resolved_entity_id FROM asset_segment_entity_mentions WHERE segment_id=%s", (seg,))
    assert all(r["resolved_entity_id"] == prim for r in m)
    # 返回的受影响集合含主实体
    assert prim in affected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_merge_entities_repoints_and_drops -v`
Expected: FAIL（无 `merge_entities`）

- [ ] **Step 3: 实现**

GraphStore 内加：

```python
    def merge_entities(
        self, domain_id: str, primary_id: str, drop_ids: list[str],
    ) -> list[str]:
        """把 drop_ids 的提及全部改指 primary_id，删除 drop 实体，重算 primary 计数。
        返回受影响实体 id 列表（含 primary）——供 scoped_recompute 使用。
        """
        drops = [d for d in drop_ids if d and d != primary_id]
        if not drops:
            return [primary_id]
        # 1) 提及改指主实体
        self._execute(
            "UPDATE asset_segment_entity_mentions SET resolved_entity_id = %s "
            "WHERE resolved_entity_id = ANY(%s)",
            (primary_id, drops),
        )
        # 2) 删被并实体的事实边与实体本身（提及已搬走）
        self._execute(
            "DELETE FROM ontology_entity_relations "
            "WHERE head_entity_id = ANY(%s) OR tail_entity_id = ANY(%s)",
            (drops, drops),
        )
        self._execute(
            "DELETE FROM ontology_entities WHERE id = ANY(%s) AND domain_id = %s",
            (drops, domain_id),
        )
        # 3) 重算主实体计数（提及行数 / 去重文档数）
        self._recount_one(domain_id, primary_id)
        return [primary_id]

    def _recount_one(self, domain_id: str, entity_id: str) -> None:
        """按已确认 mention 重算单个实体的 mention_count / document_count，set 置准。"""
        self._execute(
            """UPDATE ontology_entities e SET
                   mention_count = sub.mc, document_count = sub.dc
               FROM (
                   SELECT count(*) AS mc, count(DISTINCT document_snapshot_id) AS dc
                   FROM asset_segment_entity_mentions
                   WHERE resolved_entity_id = %s AND resolve_status IN ('auto','human')
               ) sub
               WHERE e.id = %s AND e.domain_id = %s""",
            (entity_id, entity_id, domain_id),
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_merge_entities_repoints_and_drops -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_entity_mutations.py
git commit -m "feat(graph): GraphStore.merge_entities (repoint mentions + recount + drop)"
```

---

## Task 5: 改类型 —— `retype_entity`（撞名自动合并）

改实体 node_type；若新 (类型,名字) 已有实体 → 并入那个实体（返回它当主）。

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（GraphStore）
- Test: `knowledge_mining/tests/test_entity_mutations.py`

- [ ] **Step 1: 写失败测试**

```python
def test_retype_entity_simple(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    eid = _seed_entity(gs, "S-NSSAI", node_type="__untyped__")
    asset_db.commit()
    affected = gs.retype_entity(DOMAIN, eid, "identifier")
    asset_db.commit()
    row = gs._fetchone("SELECT node_type FROM ontology_entities WHERE id=%s", (eid,))
    assert row["node_type"] == "identifier"
    assert eid in affected


def test_retype_entity_name_conflict_merges(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    target = _seed_entity(gs, "网元", node_type="network_function")
    moving = _seed_entity(gs, "网元", node_type="__untyped__")  # 同名不同类型
    snap, seg = _seed_snapshot_segment(gs)
    _seed_mention(gs, snap=snap, seg=seg, entity_id=moving, name="网元")
    asset_db.commit()

    affected = gs.retype_entity(DOMAIN, moving, "network_function")  # 撞 (network_function,网元)
    asset_db.commit()
    # moving 被并入 target
    assert gs._fetchone("SELECT 1 FROM ontology_entities WHERE id=%s", (moving,)) is None
    m = gs._fetchall(
        "SELECT resolved_entity_id FROM asset_segment_entity_mentions WHERE segment_id=%s", (seg,))
    assert all(r["resolved_entity_id"] == target for r in m)
    assert target in affected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py -k retype -v`
Expected: FAIL（无 `retype_entity`）

- [ ] **Step 3: 实现**

GraphStore 内加：

```python
    def retype_entity(self, domain_id: str, entity_id: str, new_type: str) -> list[str]:
        """改实体 node_type。若新 (node_type, canonical_name) 已有实体 → 并入它（撞唯一键即合并）。
        返回受影响实体 id 列表（普通改类型为 [entity_id]；撞名合并为 [target_id]）。
        """
        ent = self._fetchone(
            "SELECT canonical_name, node_type FROM ontology_entities WHERE id=%s AND domain_id=%s",
            (entity_id, domain_id))
        if ent is None or ent["node_type"] == new_type:
            return [entity_id]
        existing = self._fetchone(
            "SELECT id FROM ontology_entities "
            "WHERE domain_id=%s AND node_type=%s AND canonical_name=%s",
            (domain_id, new_type, ent["canonical_name"]))
        if existing and existing["id"] != entity_id:
            return self.merge_entities(domain_id, existing["id"], [entity_id])
        self._execute(
            "UPDATE ontology_entities SET node_type=%s WHERE id=%s AND domain_id=%s",
            (new_type, entity_id, domain_id))
        return [entity_id]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py -k retype -v`
Expected: PASS（两个测试都过）

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_entity_mutations.py
git commit -m "feat(graph): GraphStore.retype_entity (name-conflict folds into merge)"
```

---

## Task 6: 删除实体 —— `delete_entity`

删实体 + 它的提及 + 它相连的边。删除是纯减法：不改变邻居—邻居的共现，故**无需重算**。

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（GraphStore）
- Test: `knowledge_mining/tests/test_entity_mutations.py`

- [ ] **Step 1: 写失败测试**

```python
def test_delete_entity_removes_entity_mentions_edges(asset_db) -> None:
    gs = GraphStore(asset_db.pool)
    a, b = _seed_entity(gs, "垃圾实体"), _seed_entity(gs, "邻居")
    snap, seg = _seed_snapshot_segment(gs)
    _seed_mention(gs, snap=snap, seg=seg, entity_id=a, name="垃圾实体")
    _seed_edge(gs, a, b)
    asset_db.commit()

    gs.delete_entity(DOMAIN, a)
    asset_db.commit()
    assert gs._fetchone("SELECT 1 FROM ontology_entities WHERE id=%s", (a,)) is None
    assert gs._fetchall(
        "SELECT 1 FROM asset_segment_entity_mentions WHERE resolved_entity_id=%s", (a,)) == []
    assert gs._fetchall(
        "SELECT 1 FROM ontology_entity_relations WHERE head_entity_id=%s OR tail_entity_id=%s",
        (a, a)) == []
    # 邻居还在
    assert gs._fetchone("SELECT 1 FROM ontology_entities WHERE id=%s", (b,)) is not None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_delete_entity_removes_entity_mentions_edges -v`
Expected: FAIL（无 `delete_entity`）

- [ ] **Step 3: 实现**

GraphStore 内加：

```python
    def delete_entity(self, domain_id: str, entity_id: str) -> None:
        """删除实体及其提及、相连事实边。纯减法——不改变其它实体对的共现，无需重算。"""
        self._execute(
            "DELETE FROM ontology_entity_relations "
            "WHERE head_entity_id=%s OR tail_entity_id=%s", (entity_id, entity_id))
        self._execute(
            "DELETE FROM asset_segment_entity_mentions WHERE resolved_entity_id=%s", (entity_id,))
        self._execute(
            "DELETE FROM ontology_entities WHERE id=%s AND domain_id=%s", (entity_id, domain_id))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py::test_delete_entity_removes_entity_mentions_edges -v`
Expected: PASS

- [ ] **Step 5: 跑全套 store 测试 + 提交**

Run: `python -m pytest knowledge_mining/tests/test_entity_mutations.py knowledge_mining/tests/test_scoped_recompute.py -v`
Expected: 全 PASS

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_entity_mutations.py
git commit -m "feat(graph): GraphStore.delete_entity (subtractive, no recompute)"
```

---

## Task 7: 三个 mutation 端点

合并/改类型/删除各一个端点：调 mutation → scoped 重算（删除不需重算）→ 返回更新后的邻域子图。

**Files:**
- Modify: `knowledge_mining/mining/api/routes/ontology.py`

- [ ] **Step 1: 加 pydantic 请求模型**

在 `ontology.py` 现有模型区（`ResolveBatchRequest` 之后）加：

```python
class MergeEntitiesRequest(BaseModel):
    primary_id: str
    drop_ids: list[str]
    domain: str | None = None


class RetypeEntityRequest(BaseModel):
    new_type: str
    domain: str | None = None
```

- [ ] **Step 2: 加三个端点**

在 `ontology.py` 现有 `/graph/entities/{entity_id}/neighbors` 端点附近加：

```python
@router.post("/graph/entities/merge")
async def merge_entities_route(body: MergeEntitiesRequest, request: Request) -> dict:
    """合并实体：被并实体提及改指主实体 → scoped 重算受影响边。返回主实体新邻域。"""
    _, graph = _stores(request)
    domain = body.domain or _DEFAULT_DOMAIN
    affected = await _run(graph.merge_entities, domain, body.primary_id, body.drop_ids)
    edges = await _run(scoped_recompute, request.app.state.sync_pool, domain, affected)
    neighbors = await _run(graph.neighbors, body.primary_id, hops=1)
    return {"primary_id": body.primary_id, "recomputed_edges": edges,
            "affected": affected, "neighbors": neighbors}


@router.post("/graph/entities/{entity_id}/retype")
async def retype_entity_route(entity_id: str, body: RetypeEntityRequest, request: Request) -> dict:
    """改类型：撞名自动并入 → scoped 重算。返回受影响实体新邻域。"""
    _, graph = _stores(request)
    domain = body.domain or _DEFAULT_DOMAIN
    affected = await _run(graph.retype_entity, domain, entity_id, body.new_type)
    edges = await _run(scoped_recompute, request.app.state.sync_pool, domain, affected)
    primary = affected[0] if affected else entity_id
    neighbors = await _run(graph.neighbors, primary, hops=1)
    return {"entity_id": entity_id, "primary_id": primary,
            "recomputed_edges": edges, "neighbors": neighbors}


@router.delete("/graph/entities/{entity_id}")
async def delete_entity_route(entity_id: str, request: Request,
                              domain: str = _DEFAULT_DOMAIN) -> dict:
    """删除实体：删实体+提及+相连边（纯减法，无需重算）。"""
    _, graph = _stores(request)
    await _run(graph.delete_entity, domain, entity_id)
    return {"entity_id": entity_id, "deleted": True}
```

- [ ] **Step 3: import scoped_recompute**

`ontology.py` 顶部 import 区加：

```python
from knowledge_mining.mining.stages.graph_write import scoped_recompute
```

- [ ] **Step 4: 冒烟验证（手动起服务，可选）**

Run: `python -m pytest knowledge_mining/tests/ -k "entity_mutations or scoped" -v`（store 层已覆盖逻辑）
端点层若有现成 FastAPI TestClient 夹具，加一个 200 冒烟测试；无则靠前端联调验证。

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/api/routes/ontology.py
git commit -m "feat(api): entity merge/retype/delete endpoints with scoped recompute"
```

---

## Task 8: 前端 API 方法 + 类型

**Files:**
- Modify: `kb-ui/src/types/index.ts`
- Modify: `kb-ui/src/api/mining.ts`

- [ ] **Step 1: 加类型**

`types/index.ts` 末尾加：

```typescript
export interface EntityMutationResult {
  recomputed_edges?: number
  affected?: string[]
  neighbors?: EntityNeighbors
  primary_id?: string
}
```

- [ ] **Step 2: 加 API 方法**

`mining.ts` 的 `useMiningApi()` 返回对象里，`getEntityNeighbors` 之后加：

```typescript
    async mergeEntities(primaryId: string, dropIds: string[], domain?: string): Promise<EntityMutationResult> {
      const { data } = await client.post('/api/graph/entities/merge', {
        primary_id: primaryId, drop_ids: dropIds, domain,
      })
      return data
    },
    async retypeEntity(entityId: string, newType: string, domain?: string): Promise<EntityMutationResult> {
      const { data } = await client.post(`/api/graph/entities/${entityId}/retype`, {
        new_type: newType, domain,
      })
      return data
    },
    async deleteEntity(entityId: string, domain?: string): Promise<{ deleted: boolean }> {
      const { data } = await client.delete(`/api/graph/entities/${entityId}`, {
        params: domain ? { domain } : undefined,
      })
      return data
    },
```

> 同步在 `mining.ts` 顶部的 type import 里补 `EntityMutationResult`。

- [ ] **Step 3: 类型检查**

Run: `cd kb-ui && npm run type-check`（或 `vue-tsc --noEmit`）
Expected: 无新增类型错误

- [ ] **Step 4: 提交**

```bash
git add kb-ui/src/types/index.ts kb-ui/src/api/mining.ts
git commit -m "feat(ui): mining api methods for entity merge/retype/delete"
```

---

## Task 9: EntityGraphView 加选择 + 动作面板 + 确认 + 重算状态

在已有 `EntityGraphView.vue` 上接入交互（参照 `_brainstorm_demo/index.html` 的交互草图）。

**Files:**
- Modify: `kb-ui/src/views/knowledge/EntityGraphView.vue`

- [ ] **Step 1: 选择态**
  - 单击节点：`selected = [id]`；Ctrl/⌘ 单击：toggle 进/出 `selected`。
  - 在 `ForceGraph` 上把 `selected` 的节点描边高亮（传 prop 或在容器层叠加）。

- [ ] **Step 2: 右侧动作面板**
  - `selected.length === 1`：实体卡片 + "改类型"下拉（选项来自 `miningApi.getActiveOntology(domain).node_types` 的 name + `__untyped__`）+ "删除实体"按钮。
  - `selected.length >= 2`：列卡片 + "合并后主名"下拉（选 primary）+ "合并为一个实体"按钮。

- [ ] **Step 3: 确认步 + 受影响边高亮**
  - 点动作先弹 `ElMessageBox.confirm`，文案说明：将改写提及、涉及 N 个邻居、会重跑 NPMI、可能撞名变合并 / 边会消失。
  - 确认前把"受影响实体相连的边"在图上标橙（可在本地用当前 `edges` 过滤出与 selected 相连的，临时改 lineStyle）。

- [ ] **Step 4: 调 API + 重算状态 + 刷新**
  - 顶部状态：提交时显示「● 重算中」，完成显示「✓ 已同步」（用 ref 控制，参照草图 pill）。
  - 调对应 `miningApi.mergeEntities / retypeEntity / deleteEntity`。
  - 成功后用返回的 `neighbors`（或重新 `getEntityNeighbors(primary)`）刷新图；`ElMessage.success`。
  - 失败 `ElMessage.error(e.message)`。

- [ ] **Step 5: 浏览器验证（preview）**
  - 起前端 dev server（preview_start `kb-ui`），打开 `/entities`。
  - 走通：Ctrl 多选 → 合并 → 确认 → 图刷新；单选 → 改类型 → 确认；单选 → 删除。
  - 用 `preview_console_logs` 查报错；`preview_screenshot` 留证。
  - 如果没有种子数据，先跑一次挖掘或手动插几条实体，再验证。

- [ ] **Step 6: 提交**

```bash
git add kb-ui/src/views/knowledge/EntityGraphView.vue
git commit -m "feat(ui): entity graph merge/retype/delete with confirm + scoped recompute status"
```

---

## 收尾
- [ ] 跑后端相关测试：`python -m pytest knowledge_mining/tests/test_entity_mutations.py knowledge_mining/tests/test_scoped_recompute.py knowledge_mining/tests/test_graph_write.py -v`
- [ ] 删演示目录 `_brainstorm_demo/` 与 `.claude/launch.json` 里的 `entity-graph-demo` 配置（brainstorming 临时产物）。
- [ ] 自查：合并后被并实体消失/提及归主/范围外边不变；改类型撞名等价合并；删除不动邻居边。
