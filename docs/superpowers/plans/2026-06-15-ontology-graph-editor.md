# 本体图谱编辑器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本体图谱页从只读升级为可编辑（加/删边、加/删节点、加关系类型），编辑走"草稿→发布版本"两步落地，并修复节点装不下名字的渲染问题。

**Architecture:** 进入编辑态时克隆 active 本体（或已有 draft）到浏览器本地 reactive 副本，所有编辑只改本地副本、图谱即时重画；"保存草稿"整份覆盖式 PUT 到后端 draft 版本；"发布版本"把 draft 激活为新 active（旧 active 转 superseded）。人工编辑这条线不走审核，LLM 候选仍在『本体确认』页审核——两条线分开。

**Tech Stack:** 前端 Vue 3 + Element Plus + TypeScript + ECharts 6；后端 Python（psycopg 直连 PostgreSQL）+ FastAPI + pytest。

**关联设计稿：** `docs/superpowers/specs/2026-06-15-ontology-graph-editor-design.md`

---

## 文件结构（File Structure）

**后端**
- `knowledge_mining/mining/infra/ontology_store.py`（改）— 在 `OntologyStore` 类内新增 6 个草稿原语：`get_draft_version` / `node_types_for_version` / `relation_types_for_version` / `delete_version_types` / `replace_draft` / `publish_draft`。职责：草稿版本的读、整份替换、发布。
- `knowledge_mining/mining/api/routes/ontology.py`（改）— 新增 3 个 REST 路由 + 对应 pydantic 请求模型。职责：把上面 3 个高层原语暴露成 HTTP。
- `knowledge_mining/tests/test_ontology_draft.py`（建）— 草稿原语的纯逻辑/假 store 单测。
- `knowledge_mining/tests/test_ontology_draft_routes.py`（建）— 断言 3 个新路由已注册、方法正确。

**前端**
- `kb-ui/src/types/index.ts`（改）— 新增草稿载荷类型（`OntologyDraft`）。
- `kb-ui/src/api/mining.ts`（改）— 新增 `getOntologyDraft` / `saveOntologyDraft` / `publishOntologyDraft` 三个方法。
- `kb-ui/src/views/knowledge/ontologyGraph.ts`（改）— 放宽 `buildOntologyGraph` 入参类型；新增 `EditableOntology` 类型与 5 个纯函数 `addNode` / `removeNode` / `addRelationType` / `addEdge` / `removeEdge`；导出 `parseExamples`。
- `kb-ui/src/components/charts/OntologyDiGraph.vue`（改）— 节点圆点改小、名字标签外移到节点下方不裁切（修复装不下名字）。
- `kb-ui/src/views/knowledge/OntologyGraphView.vue`（改）— 加「编辑」开关、保存草稿/发布版本/退出编辑按钮、右侧编辑面板表单、编辑态暂停轮询。

**实现顺序：** 后端原语（TDD）→ 后端路由 → 前端类型+API → 前端纯函数 → 渲染修复 → 编辑页集成。先做能独立测试的底层，最后做大文件集成。

---

## 关于前端测试

`kb-ui` 没有装前端测试运行器（package.json 无 vitest/jest）。按设计稿约定：前端纯函数以 `vue-tsc --noEmit` 类型检查 + 人工推演验证为准，不引入测试运行器（YAGNI）。后端有成熟的假 store 单测设施，后端任务全程 TDD。

---

### Task 1: OntologyStore 草稿读取原语（get_draft_version / *_for_version / delete_version_types）

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（`OntologyStore` 类内，紧接 `active_relation_types` 之后、`# -- 别名词典 --` 之前插入）
- Test: `knowledge_mining/tests/test_ontology_draft.py`（新建）

这三个是纯 SQL 读/删原语，用"记录 SQL+params 的假 store"验证 SQL 拼装正确（沿用 `test_review_gate.py` 里 `_FakeGraphQuery` 的风格）。

- [ ] **Step 1: 写失败测试**

新建 `knowledge_mining/tests/test_ontology_draft.py`，内容：

```python
"""草稿编辑器后端单测：OntologyStore 草稿原语（纯逻辑 + 假 store，无需 DB）。

覆盖：
- get_draft_version / node_types_for_version / relation_types_for_version / delete_version_types：SQL 拼装与参数；
- replace_draft：无 draft → 新建 draft 版本 + 写类型；有 draft → 先删旧类型再重写；
- publish_draft：有 draft → 调 activate_version；无 draft → 返回 None。
"""
from __future__ import annotations

from contextlib import contextmanager

from knowledge_mining.mining.infra.ontology_store import OntologyStore


# ---- 读/删原语：校验 SQL 拼装 ----

class _FakeQueryOnto(OntologyStore):
    """记录 _fetchall/_fetchone/_execute 的 SQL+params，校验草稿读/删 SQL。"""

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple]] = []
        self.executed: list[tuple[str, tuple]] = []

    def _fetchall(self, sql, params=()):  # type: ignore[override]
        self.queries.append((sql, tuple(params)))
        return []

    def _fetchone(self, sql, params=()):  # type: ignore[override]
        self.queries.append((sql, tuple(params)))
        return None

    def _execute(self, sql, params=()):  # type: ignore[override]
        self.executed.append((sql, tuple(params)))


def test_get_draft_version_filters_status_draft() -> None:
    g = _FakeQueryOnto()
    g.get_draft_version("dom")
    sql, params = g.queries[0]
    assert "ontology_versions" in sql and "status = 'draft'" in sql
    assert "domain_id = %s" in sql
    assert params == ("dom",)


def test_node_types_for_version_filters_by_version_id() -> None:
    g = _FakeQueryOnto()
    g.node_types_for_version("v1")
    sql, params = g.queries[0]
    assert "ontology_node_types" in sql and "ontology_version_id = %s" in sql
    assert params == ("v1",)


def test_relation_types_for_version_filters_by_version_id() -> None:
    g = _FakeQueryOnto()
    g.relation_types_for_version("v1")
    sql, params = g.queries[0]
    assert "ontology_relation_types" in sql and "ontology_version_id = %s" in sql
    assert params == ("v1",)


def test_delete_version_types_deletes_both_tables() -> None:
    g = _FakeQueryOnto()
    g.delete_version_types("v1")
    assert len(g.executed) == 2
    assert all("DELETE" in s for s, _ in g.executed)
    assert any("ontology_node_types" in s for s, _ in g.executed)
    assert any("ontology_relation_types" in s for s, _ in g.executed)
    assert all(p == ("v1",) for _, p in g.executed)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft.py -v`
Expected: FAIL，`AttributeError: 'OntologyStore' object has no attribute 'get_draft_version'`（4 个用例都因方法不存在而报错）。

- [ ] **Step 3: 写实现**

在 `knowledge_mining/mining/infra/ontology_store.py` 的 `OntologyStore` 类中，`active_relation_types` 方法之后插入：

```python
    # -- 草稿版本（人工编辑器）--

    def get_draft_version(self, domain_id: str) -> dict[str, Any] | None:
        """取该领域 status='draft' 的本体版本（约定每领域至多一个 draft，取最新）。"""
        return self._fetchone(
            "SELECT * FROM ontology_versions WHERE domain_id = %s AND status = 'draft' "
            "ORDER BY version_no DESC LIMIT 1",
            (domain_id,),
        )

    def node_types_for_version(self, version_id: str) -> list[dict[str, Any]]:
        """按 version_id 读点类型（active_node_types 只按 active 读，这里补按 id 读）。"""
        return self._fetchall(
            "SELECT * FROM ontology_node_types WHERE ontology_version_id = %s ORDER BY name",
            (version_id,),
        )

    def relation_types_for_version(self, version_id: str) -> list[dict[str, Any]]:
        """按 version_id 读边类型。"""
        return self._fetchall(
            "SELECT * FROM ontology_relation_types WHERE ontology_version_id = %s ORDER BY name",
            (version_id,),
        )

    def delete_version_types(self, version_id: str) -> None:
        """删一个版本名下的全部点/边类型（供 replace_draft 的"清空+重写"复用）。"""
        self._execute(
            "DELETE FROM ontology_node_types WHERE ontology_version_id = %s", (version_id,))
        self._execute(
            "DELETE FROM ontology_relation_types WHERE ontology_version_id = %s", (version_id,))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft.py -v`
Expected: 4 passed。

- [ ] **Step 5: 提交（仅当用户要求提交时）**

> 注意：本仓库用户立有"不明确要求就不提交"的硬规矩。除非用户明确说提交，否则**跳过本步**，继续下一个 Task。若要提交：

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_ontology_draft.py
git commit -m "feat(ontology): add draft version read primitives to OntologyStore"
```

---

### Task 2: OntologyStore 草稿写入原语（replace_draft / publish_draft）

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（接 Task 1 插入的草稿块之后）
- Test: `knowledge_mining/tests/test_ontology_draft.py`（在 Task 1 文件末尾追加）

这两个是编排原语，验证点是"调了哪些高层方法、顺序对不对"，所以假 store 覆盖 `transaction` + 被调用的高层方法（`get_draft_version` / `next_version_no` / `create_version` / `delete_version_types` / `add_node_type` / `add_relation_type` / `activate_version`），不下钻到 SQL。

- [ ] **Step 1: 写失败测试**

在 `knowledge_mining/tests/test_ontology_draft.py` 末尾追加：

```python
# ---- replace_draft / publish_draft：编排逻辑 ----

class _FakeReplaceOnto(OntologyStore):
    """覆盖 transaction + 被 replace_draft/publish_draft 调用的高层方法，记录调用序列。"""

    def __init__(self, *, draft=None, version_no=5) -> None:
        self.calls: list[tuple] = []
        self._draft = draft
        self._version_no = version_no

    @contextmanager
    def transaction(self):  # type: ignore[override]
        self.calls.append(("transaction",))
        yield

    def get_draft_version(self, domain_id):  # type: ignore[override]
        return self._draft

    def next_version_no(self, domain_id):  # type: ignore[override]
        return self._version_no

    def create_version(self, domain_id, *, version_no, status="draft",
                       source="human_review", created_by=None, note=None):  # type: ignore[override]
        self.calls.append(("create_version", domain_id, version_no, status, source))
        return "new-draft-vid"

    def delete_version_types(self, version_id):  # type: ignore[override]
        self.calls.append(("delete_version_types", version_id))

    def add_node_type(self, version_id, **kw):  # type: ignore[override]
        self.calls.append(("add_node_type", version_id, kw["name"]))
        return "n"

    def add_relation_type(self, version_id, **kw):  # type: ignore[override]
        self.calls.append(("add_relation_type", version_id, kw["name"]))
        return "r"

    def activate_version(self, version_id, domain_id):  # type: ignore[override]
        self.calls.append(("activate_version", version_id, domain_id))


_NODES = [{"name": "feature", "layer": "concept", "is_strong": True,
           "definition": "d", "examples": ["x"]}]
_RELS = [{"name": "triggers", "layer": "concept", "is_directed": True,
          "inverse_name": None, "allowed_pairs": [{"head": "alarm", "tail": "feature"}],
          "definition": None}]


def test_replace_draft_creates_when_no_draft() -> None:
    store = _FakeReplaceOnto(draft=None, version_no=7)
    vid = store.replace_draft("dom", _NODES, _RELS, created_by="tester")
    assert vid == "new-draft-vid"
    kinds = [c[0] for c in store.calls]
    # 无 draft：先建 draft 版本，再写点/边类型；不调 delete_version_types
    assert kinds[0] == "transaction"
    assert ("create_version", "dom", 7, "draft", "human_edit") in store.calls
    assert "delete_version_types" not in kinds
    assert ("add_node_type", "new-draft-vid", "feature") in store.calls
    assert ("add_relation_type", "new-draft-vid", "triggers") in store.calls


def test_replace_draft_clears_then_rewrites_when_draft_exists() -> None:
    store = _FakeReplaceOnto(draft={"id": "old-draft"}, version_no=7)
    vid = store.replace_draft("dom", _NODES, _RELS)
    assert vid == "old-draft"
    kinds = [c[0] for c in store.calls]
    # 有 draft：不新建版本，先删旧类型再重写
    assert "create_version" not in kinds
    assert ("delete_version_types", "old-draft") in store.calls
    assert kinds.index("delete_version_types") < kinds.index("add_node_type")
    assert ("add_node_type", "old-draft", "feature") in store.calls
    assert ("add_relation_type", "old-draft", "triggers") in store.calls


def test_publish_draft_activates_existing_draft() -> None:
    store = _FakeReplaceOnto(draft={"id": "draft-1"})
    out = store.publish_draft("dom")
    assert out == "draft-1"
    assert ("activate_version", "draft-1", "dom") in store.calls


def test_publish_draft_none_when_no_draft() -> None:
    store = _FakeReplaceOnto(draft=None)
    assert store.publish_draft("dom") is None
    assert all(c[0] != "activate_version" for c in store.calls)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft.py -k "replace_draft or publish_draft" -v`
Expected: FAIL，`AttributeError: 'OntologyStore' object has no attribute 'replace_draft'`。

- [ ] **Step 3: 写实现**

在 `knowledge_mining/mining/infra/ontology_store.py` 的 `OntologyStore` 类中，紧接 Task 1 的 `delete_version_types` 之后插入：

```python
    def replace_draft(
        self,
        domain_id: str,
        node_types: list[dict[str, Any]],
        relation_types: list[dict[str, Any]],
        *,
        created_by: str | None = None,
    ) -> str:
        """整份覆盖式保存草稿：事务内 get-or-create draft → 清空旧类型 → 按提交内容重写。

        node_types 每项键：name, layer, is_strong, definition, examples（list）。
        relation_types 每项键：name, layer, is_directed, inverse_name, allowed_pairs（list[{head,tail}]）, definition。
        返回 draft 版本 id。
        """
        with self.transaction():
            draft = self.get_draft_version(domain_id)
            if draft is None:
                vid = self.create_version(
                    domain_id, version_no=self.next_version_no(domain_id),
                    status="draft", source="human_edit", created_by=created_by,
                )
            else:
                vid = draft["id"]
                self.delete_version_types(vid)
            for nt in node_types:
                self.add_node_type(
                    vid, name=nt["name"], layer=nt.get("layer", "concept"),
                    is_strong=nt.get("is_strong", False), definition=nt.get("definition"),
                    examples=nt.get("examples") or [],
                )
            for rt in relation_types:
                self.add_relation_type(
                    vid, name=rt["name"], layer=rt.get("layer", "concept"),
                    is_directed=rt.get("is_directed", True),
                    inverse_name=rt.get("inverse_name"),
                    allowed_pairs=rt.get("allowed_pairs") or [],
                    definition=rt.get("definition"),
                )
            return vid

    def publish_draft(self, domain_id: str) -> str | None:
        """发布：把该领域 draft 激活为新 active（旧 active 自动转 superseded）。

        无 draft → 返回 None（路由转 400）。
        """
        draft = self.get_draft_version(domain_id)
        if draft is None:
            return None
        self.activate_version(draft["id"], domain_id)
        return draft["id"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft.py -v`
Expected: 8 passed（Task 1 的 4 个 + 本任务 4 个）。

- [ ] **Step 5: 全量后端回归**

Run: `python -m pytest knowledge_mining/tests/test_review_gate.py knowledge_mining/tests/test_ontology_draft.py -v`
Expected: 全部 passed（确认没碰坏 OntologyStore 既有行为）。

- [ ] **Step 6: 提交（仅当用户要求提交时，规则同 Task 1 Step 5）**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_ontology_draft.py
git commit -m "feat(ontology): add replace_draft/publish_draft orchestration"
```

---

### Task 3: 后端 3 个草稿路由（GET/PUT /ontology/draft + POST /ontology/draft/publish）

**Files:**
- Modify: `knowledge_mining/mining/api/routes/ontology.py`（请求模型加在 `class PromoteRequest` 之后；路由加在 `promote_candidates` 函数之后、`# ── Gate2 mention 裁决 ──` 之前）
- Test: `knowledge_mining/tests/test_ontology_draft_routes.py`（新建）

路由是薄封装，且本仓库无 HTTP test client 设施，所以用"导入 router 检查路由已注册"的轻量测试守住契约（路径 + 方法）；业务逻辑已被 Task 1/2 的 store 测试覆盖。

- [ ] **Step 1: 写失败测试**

新建 `knowledge_mining/tests/test_ontology_draft_routes.py`：

```python
"""草稿编辑器路由契约测试：3 个新路由已注册、HTTP 方法正确。

不连 DB、不起服务——只导入 APIRouter 检查 route 表。业务逻辑由 test_ontology_draft.py 覆盖。
"""
from __future__ import annotations

from knowledge_mining.mining.api.routes.ontology import router


def _route_methods() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in router.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if path and methods:
            out.setdefault(path, set()).update(methods)
    return out


def test_draft_get_and_put_registered() -> None:
    rm = _route_methods()
    assert "/api/ontology/draft" in rm
    assert "GET" in rm["/api/ontology/draft"]
    assert "PUT" in rm["/api/ontology/draft"]


def test_draft_publish_registered() -> None:
    rm = _route_methods()
    assert "/api/ontology/draft/publish" in rm
    assert "POST" in rm["/api/ontology/draft/publish"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft_routes.py -v`
Expected: FAIL，两个用例 AssertionError（`/api/ontology/draft` 不在路由表里）。

- [ ] **Step 3: 写请求模型**

在 `knowledge_mining/mining/api/routes/ontology.py` 的 `class PromoteRequest(BaseModel):` 块之后插入：

```python
class DraftNodeTypeModel(BaseModel):
    name: str
    layer: str = "concept"
    is_strong: bool = False
    definition: str | None = None
    examples_json: list = []


class DraftRelationTypeModel(BaseModel):
    name: str
    layer: str = "concept"
    is_directed: bool = True
    inverse_name: str | None = None
    allowed_pairs_json: list = []
    definition: str | None = None


class SaveDraftRequest(BaseModel):
    domain: str | None = None
    node_types: list[DraftNodeTypeModel] = []
    relation_types: list[DraftRelationTypeModel] = []
    created_by: str | None = None


class PublishDraftRequest(BaseModel):
    domain: str | None = None
```

- [ ] **Step 4: 写路由**

在 `knowledge_mining/mining/api/routes/ontology.py` 的 `promote_candidates` 函数之后、`# ── Gate2 mention 裁决 ──` 注释之前插入：

```python
# ── 本体图谱编辑器：草稿 / 发布 ──

@router.get("/ontology/draft")
async def get_ontology_draft(request: Request, domain: str = _DEFAULT_DOMAIN) -> dict:
    """取该领域 draft 版本及其点/边类型。无 draft → version=null、空列表（前端据此从 active 克隆）。"""
    onto, _ = _stores(request)
    draft = await _run(onto.get_draft_version, domain)
    if not draft:
        return {"domain": domain, "version": None, "node_types": [], "relation_types": []}
    node_types = await _run(onto.node_types_for_version, draft["id"])
    rel_types = await _run(onto.relation_types_for_version, draft["id"])
    return {
        "domain": domain,
        "version": dict(draft),
        "node_types": [dict(n) for n in node_types],
        "relation_types": [dict(r) for r in rel_types],
    }


@router.put("/ontology/draft")
async def save_ontology_draft(body: SaveDraftRequest, request: Request) -> dict:
    """整份覆盖式保存草稿：后端 get-or-create draft 版本 → 清空旧类型 → 按提交内容重写。"""
    onto, _ = _stores(request)
    domain = body.domain or _DEFAULT_DOMAIN
    node_types = [
        {"name": n.name, "layer": n.layer, "is_strong": n.is_strong,
         "definition": n.definition, "examples": n.examples_json}
        for n in body.node_types
    ]
    relation_types = [
        {"name": r.name, "layer": r.layer, "is_directed": r.is_directed,
         "inverse_name": r.inverse_name, "allowed_pairs": r.allowed_pairs_json,
         "definition": r.definition}
        for r in body.relation_types
    ]
    try:
        draft_vid = await _run(
            onto.replace_draft, domain, node_types, relation_types,
            created_by=body.created_by,
        )
    except Exception as e:
        logger.error("save ontology draft failed: %s", e, exc_info=True)
        raise HTTPException(400, f"保存草稿失败：{e}")
    return {"domain": domain, "draft_version_id": draft_vid}


@router.post("/ontology/draft/publish")
async def publish_ontology_draft(body: PublishDraftRequest, request: Request) -> dict:
    """发布：把 draft 激活为新 active（旧 active → superseded）。无 draft → 400。"""
    onto, _ = _stores(request)
    domain = body.domain or _DEFAULT_DOMAIN
    new_vid = await _run(onto.publish_draft, domain)
    if new_vid is None:
        raise HTTPException(400, "没有可发布的草稿，请先保存草稿")
    return {"domain": domain, "new_version_id": new_vid}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest knowledge_mining/tests/test_ontology_draft_routes.py -v`
Expected: 2 passed。

- [ ] **Step 6: 语法自检**

Run: `python -c "import knowledge_mining.mining.api.routes.ontology"`
Expected: 无输出、退出码 0（模块可导入，pydantic 模型与路由无语法/引用错误）。

- [ ] **Step 7: 提交（仅当用户要求提交时，规则同前）**

```bash
git add knowledge_mining/mining/api/routes/ontology.py knowledge_mining/tests/test_ontology_draft_routes.py
git commit -m "feat(ontology): add draft save/publish REST routes"
```

---

### Task 4: 前端类型 + API 方法（草稿读/存/发布）

**Files:**
- Modify: `kb-ui/src/types/index.ts`（在 `ActiveOntology` 接口之后插入）
- Modify: `kb-ui/src/api/mining.ts`（import 段 + `useMiningApi` 返回对象内，紧接 `promoteCandidates` 之后）

无前端测试运行器，本任务以 `vue-tsc --noEmit` 类型检查为验收。

- [ ] **Step 1: 加类型**

在 `kb-ui/src/types/index.ts` 的 `ActiveOntology` 接口（以 `}` 结束那行）之后插入：

```typescript
// 草稿编辑器：GET /ontology/draft 返回 / PUT 提交载荷，结构与 ActiveOntology 同构
export interface OntologyDraft {
  domain: string
  version: OntologyVersion | null
  node_types: OntologyNodeType[]
  relation_types: OntologyRelationType[]
}
```

- [ ] **Step 2: 加 API 方法**

在 `kb-ui/src/api/mining.ts` 顶部 `import type { ... } from '@/types'` 列表里，把 `ActiveOntology, OntologyCandidate,` 那一行改为同时引入 `OntologyDraft`：

把（约第 6 行）
```typescript
  RunTrace, OntologyVersion, ActiveOntology, OntologyCandidate,
```
改为
```typescript
  RunTrace, OntologyVersion, ActiveOntology, OntologyDraft, OntologyCandidate,
```

然后在 `useMiningApi()` 返回对象里，`promoteCandidates` 方法（以 `},` 结束）之后插入：

```typescript
    // 本体图谱编辑器：草稿读 / 整份覆盖式存 / 发布
    async getOntologyDraft(domain?: string): Promise<OntologyDraft> {
      const { data } = await client.get('/api/ontology/draft', { params: domain ? { domain } : undefined })
      return data
    },

    async saveOntologyDraft(
      domain: string,
      payload: { node_types: OntologyNodeType[]; relation_types: OntologyRelationType[] },
    ): Promise<{ domain: string; draft_version_id: string }> {
      const { data } = await client.put('/api/ontology/draft', { domain, ...payload })
      return data
    },

    async publishOntologyDraft(domain: string): Promise<{ domain: string; new_version_id: string }> {
      const { data } = await client.post('/api/ontology/draft/publish', { domain })
      return data
    },
```

> 说明：`saveOntologyDraft` 的 payload 里 `node_types/relation_types` 用前端 `OntologyNodeType/OntologyRelationType` 形状直接提交，后端 `SaveDraftRequest` 按 `name/layer/is_strong/definition/examples_json` 与 `name/layer/is_directed/inverse_name/allowed_pairs_json/definition` 取字段，多余的 `id` 字段被忽略。

- [ ] **Step 3: 类型检查通过**

Run: `cd kb-ui && npx vue-tsc --noEmit`
Expected: 退出码 0，无报错。

- [ ] **Step 4: 提交（仅当用户要求提交时，规则同前）**

```bash
git add kb-ui/src/types/index.ts kb-ui/src/api/mining.ts
git commit -m "feat(ontology-ui): add draft API methods and OntologyDraft type"
```

---

### Task 5: ontologyGraph.ts 编辑纯函数（addNode/removeNode/addRelationType/addEdge/removeEdge）

**Files:**
- Modify: `kb-ui/src/views/knowledge/ontologyGraph.ts`

放宽 `buildOntologyGraph` 入参类型，让本地可编辑模型也能直接喂进去渲染；新增 `EditableOntology` 类型与 5 个纯函数（输入旧模型 + 参数，返回新模型，不改原对象，便于测试与 Vue 重渲染）。无前端测试运行器，验收 = `vue-tsc --noEmit` + 人工推演（每个函数的输入/输出在注释里写清）。

- [ ] **Step 1: 放宽 buildOntologyGraph 入参 + 导出 parseExamples**

把 `kb-ui/src/views/knowledge/ontologyGraph.ts` 顶部 import 改为同时引入节点/边类型：

把（约第 4 行）
```typescript
import type { ActiveOntology, OntologyCandidate } from '@/types'
```
改为
```typescript
import type { ActiveOntology, OntologyCandidate, OntologyNodeType, OntologyRelationType } from '@/types'
```

把 `function parseExamples(` 改为导出：
```typescript
export function parseExamples(raw: unknown): string[] {
```

把 `buildOntologyGraph` 的签名第一个参数类型从 `active: ActiveOntology` 放宽为只读所需的子集（`ActiveOntology` 与 `EditableOntology` 都结构兼容）：

把
```typescript
export function buildOntologyGraph(
  active: ActiveOntology,
  candidates: OntologyCandidate[] = [],
  showCandidates = false,
): OntoGraphData {
```
改为
```typescript
export function buildOntologyGraph(
  active: Pick<ActiveOntology, 'node_types' | 'relation_types'>,
  candidates: OntologyCandidate[] = [],
  showCandidates = false,
): OntoGraphData {
```

- [ ] **Step 2: 追加 EditableOntology 类型与 5 个纯函数**

在 `kb-ui/src/views/knowledge/ontologyGraph.ts` 文件末尾追加：

```typescript
// ── 本地可编辑模型 + 增删纯函数（编辑器用）──
// 约定：所有函数都返回"新模型"（浅拷贝顶层 + 拷贝被改的数组），不修改入参，
// 便于单测断言与 Vue 重新渲染。校验（重名/端点存在性）放在调用方（View）里做，这里只做变换。

export interface EditableOntology {
  node_types: OntologyNodeType[]
  relation_types: OntologyRelationType[]
}

export function cloneModel(m: EditableOntology): EditableOntology {
  return {
    node_types: m.node_types.map(n => ({ ...n })),
    relation_types: m.relation_types.map(r => ({
      ...r,
      allowed_pairs_json: (r.allowed_pairs_json || []).map(p => ({ ...p })),
    })),
  }
}

export interface NewNodeInput {
  name: string
  layer: string
  isStrong: boolean
  definition?: string
}

/** 新建节点类型。id 留空字符串（后端 add_node_type 会生成正式 id，保存时忽略本地 id）。 */
export function addNode(model: EditableOntology, input: NewNodeInput): EditableOntology {
  const next = cloneModel(model)
  next.node_types.push({
    id: '',
    name: input.name,
    layer: input.layer || 'concept',
    is_strong: !!input.isStrong,
    definition: input.definition || null,
    examples_json: [],
  })
  return next
}

/** 删除节点类型，并连带删除所有边类型里以它为 head 或 tail 的 pair。 */
export function removeNode(model: EditableOntology, name: string): EditableOntology {
  const next = cloneModel(model)
  next.node_types = next.node_types.filter(n => n.name !== name)
  next.relation_types = next.relation_types.map(r => ({
    ...r,
    allowed_pairs_json: (r.allowed_pairs_json || []).filter(
      p => p.head !== name && p.tail !== name,
    ),
  }))
  return next
}

export interface NewRelationTypeInput {
  name: string
  isDirected: boolean
  inverseName?: string
  definition?: string
}

/** 新建关系类型（空 allowed_pairs，建完后可通过 addEdge 往里加边）。 */
export function addRelationType(model: EditableOntology, input: NewRelationTypeInput): EditableOntology {
  const next = cloneModel(model)
  next.relation_types.push({
    id: '',
    name: input.name,
    layer: 'concept',
    is_directed: !!input.isDirected,
    inverse_name: input.inverseName || null,
    allowed_pairs_json: [],
    definition: input.definition || null,
  })
  return next
}

/** 给某个已存在的关系类型加一条边（head→tail 的 pair）。已存在同 pair 则原样返回。 */
export function addEdge(
  model: EditableOntology, relationName: string, head: string, tail: string,
): EditableOntology {
  const next = cloneModel(model)
  const rel = next.relation_types.find(r => r.name === relationName)
  if (!rel) return next
  const pairs = rel.allowed_pairs_json || (rel.allowed_pairs_json = [])
  if (pairs.some(p => p.head === head && p.tail === tail)) return next
  pairs.push({ head, tail })
  return next
}

/** 删除一条边（关系类型 relationName 下 head→tail 的 pair）。参数与 addEdge 对称。 */
export function removeEdge(
  model: EditableOntology, relationName: string, head: string, tail: string,
): EditableOntology {
  const next = cloneModel(model)
  const rel = next.relation_types.find(r => r.name === relationName)
  if (!rel) return next
  rel.allowed_pairs_json = (rel.allowed_pairs_json || []).filter(
    p => !(p.head === head && p.tail === tail),
  )
  return next
}
```

- [ ] **Step 3: 类型检查通过**

Run: `cd kb-ui && npx vue-tsc --noEmit`
Expected: 退出码 0，无报错。

- [ ] **Step 4: 人工推演验证（无运行器，逐函数自检）**

逐一核对（在脑中或纸上代入一组样例，确认输出符合预期；不需写运行代码）：
- `addNode({node_types:[],relation_types:[]}, {name:'X',layer:'concept',isStrong:true})` → 新模型 node_types 多一项 `{id:'',name:'X',is_strong:true,...}`，原模型不变（顶层与数组都是新对象）。
- `removeNode(m, 'X')`：m 含节点 X 和一条边类型 R 的 pair `{head:'X',tail:'Y'}` → 输出里 X 节点没了、R 的该 pair 也没了。
- `addRelationType(m, {name:'R2',isDirected:false})` → relation_types 多一项 `allowed_pairs_json:[]`。
- `addEdge(m,'R','A','B')`：R 存在 → 其 allowed_pairs_json 多 `{head:'A',tail:'B'}`；再调一次同参数 → 不重复。R 不存在 → 原样（浅拷贝）返回。
- `removeEdge(m,'R','A','B')` → R 的 `{head:'A',tail:'B'}` 被删。
确认每个函数都没改入参 `m`（`cloneModel` 已保证）。

- [ ] **Step 5: 提交（仅当用户要求提交时，规则同前）**

```bash
git add kb-ui/src/views/knowledge/ontologyGraph.ts
git commit -m "feat(ontology-ui): add editable-model pure helpers to ontologyGraph"
```

---

### Task 6: OntologyDiGraph.vue 标签外移修复（节点装不下名字）

**Files:**
- Modify: `kb-ui/src/components/charts/OntologyDiGraph.vue`（`render()` 内 `nodes` 映射的 `symbolSize` / `symbol` / `label`）

把节点从"大圆角矩形里塞文字"改成"小圆点 + 名字标签放节点下方、不裁切"。候选/层配色逻辑不动。无前端测试运行器，验收 = `vue-tsc --noEmit` + 跑起来肉眼确认长中文名不再被裁。

- [ ] **Step 1: 改 nodes 映射**

把 `kb-ui/src/components/charts/OntologyDiGraph.vue` 的 `render()` 中这段：

```typescript
  const nodes = props.nodes.map(n => ({
    id: n.id,
    name: n.name,
    symbolSize: n.isStrong ? 46 : 36,
    symbol: 'roundRect',
    itemStyle: n.isCandidate
      ? { color: '#fff7ed', borderColor: CANDIDATE_COLOR, borderWidth: 2, borderType: 'dashed' as const }
      : { color: colorOf(n.layer), borderColor: colorOf(n.layer), borderWidth: 1 },
    label: {
      show: true, fontSize: 12,
      color: n.isCandidate ? CANDIDATE_COLOR : '#fff',
      fontWeight: n.isStrong ? 700 : 400 as const,
    },
  }))
```

替换为：

```typescript
  const nodes = props.nodes.map(n => ({
    id: n.id,
    name: n.name,
    // 小圆点：名字不再塞进图形内，所以图形可以小一些
    symbolSize: n.isStrong ? 18 : 13,
    symbol: 'circle',
    itemStyle: n.isCandidate
      ? { color: '#fff7ed', borderColor: CANDIDATE_COLOR, borderWidth: 2, borderType: 'dashed' as const }
      : { color: colorOf(n.layer), borderColor: colorOf(n.layer), borderWidth: 1 },
    label: {
      show: true,
      // 名字标签移到圆点下方、不裁切，长中文名完整显示
      position: 'bottom' as const,
      distance: 6,
      fontSize: 12,
      color: n.isCandidate ? CANDIDATE_COLOR : 'var(--kb-text-primary)',
      fontWeight: n.isStrong ? 700 : 400 as const,
      overflow: 'none' as const,
    },
  }))
```

> 说明：`color` 由原来的白字（`#fff`，因为字在深色图形里）改为深色文字（圆点外侧是浅色画布背景）。`overflow: 'none'` 关掉 ECharts 默认的标签截断。`position:'bottom'` + `distance` 把标签放到圆点下方。

- [ ] **Step 2: 类型检查通过**

Run: `cd kb-ui && npx vue-tsc --noEmit`
Expected: 退出码 0，无报错。

- [ ] **Step 3: 肉眼验证**

启动前端（`cd kb-ui && npm run dev`），打开本体图谱页，确认：节点变成小圆点、名字显示在圆点下方、长中文类型名（如"网络切片管理功能"）完整可见不被裁。

- [ ] **Step 4: 提交（仅当用户要求提交时，规则同前）**

```bash
git add kb-ui/src/components/charts/OntologyDiGraph.vue
git commit -m "fix(ontology-ui): move node labels outside dots so long names fit"
```

---

### Task 7: 视图整合 —— 编辑模式、草稿、保存/发布

**Files:**
- Modify: `kb-ui/src/views/knowledge/OntologyGraphView.vue`（整文件替换）

这是把前面所有零件拼起来的总装任务：在原只读视图上加一个"编辑模式"开关；进入编辑后，把当前 active 本体克隆成一份浏览器本地草稿（`EditableOntology`），所有增删改都作用在草稿上、即时重画；"保存草稿"整体覆盖写后端 draft 版本；"发布版本"先保存再激活。编辑模式下暂停 5 秒轮询（否则会把用户没保存的改动覆盖掉）。

前端无测试框架，验证靠 `vue-tsc --noEmit` + 手动走查。

- [ ] **Step 1: 整文件替换 `OntologyGraphView.vue`**

```vue
<!-- kb-ui/src/views/knowledge/OntologyGraphView.vue -->
<template>
  <div class="og-view">
    <div class="og-view__header">
      <div class="og-view__header-left">
        <h2 class="og-view__title">本体图谱</h2>
        <span class="og-pill og-pill--live"><span class="og-dot" />实时</span>
        <span v-if="!editMode" class="og-pill og-pill--ro">只读</span>
        <span v-else class="og-pill og-pill--edit">编辑中（草稿）</span>
        <span class="og-view__sub" v-if="active.version">
          v{{ active.version.version_no }} · {{ currentNodeCount }} 类型 · {{ graph.edges.length }} 边
        </span>
        <span class="og-view__sub" v-else>尚未引种本体</span>
      </div>
      <div class="og-view__actions">
        <el-switch v-if="!editMode" v-model="showCandidates" active-text="显示待审候选" inline-prompt />
        <el-button v-if="!editMode" @click="loadAll" :loading="loading"><el-icon><Refresh /></el-icon></el-button>
        <el-button v-if="!editMode" type="primary" @click="enterEdit">进入编辑</el-button>
        <template v-else>
          <el-button @click="exitEdit">退出编辑</el-button>
          <el-button @click="saveDraft" :loading="saving">保存草稿</el-button>
          <el-button type="primary" @click="publishDraft" :loading="publishing">发布版本</el-button>
        </template>
      </div>
    </div>

    <div class="og-body">
      <div class="og-card og-graph">
        <OntologyDiGraph
          :nodes="graph.nodes" :edges="graph.edges"
          @node-click="onNodeClick" @edge-click="onEdgeClick"
        />
      </div>

      <div class="og-card og-panel">
        <!-- 编辑工具区：仅编辑模式显示 -->
        <template v-if="editMode">
          <div class="og-edit-sec">
            <div class="og-edit-title">新建节点</div>
            <div class="og-edit-row">
              <el-input v-model="nodeForm.name" placeholder="类型名" size="small" />
              <el-select v-model="nodeForm.layer" size="small" style="width: 96px">
                <el-option label="概念层" value="concept" />
                <el-option label="实例层" value="instance" />
                <el-option label="属性层" value="property" />
              </el-select>
              <el-switch v-model="nodeForm.is_strong" active-text="强" inline-prompt size="small" />
              <el-button size="small" type="primary" @click="doAddNode">加</el-button>
            </div>
          </div>

          <div class="og-edit-sec">
            <div class="og-edit-title">新建关系类型</div>
            <div class="og-edit-row">
              <el-input v-model="relForm.name" placeholder="关系名" size="small" />
              <el-switch v-model="relForm.is_directed" active-text="有向" inline-prompt size="small" />
              <el-input v-model="relForm.inverse_name" placeholder="反向名（可选）" size="small" />
              <el-input v-model="relForm.definition" placeholder="定义（可选）" size="small" />
              <el-button size="small" type="primary" @click="doAddRelationType">加</el-button>
            </div>
          </div>

          <div class="og-edit-sec">
            <div class="og-edit-title">新建边</div>
            <div class="og-edit-row">
              <el-select v-model="edgeForm.head" size="small" placeholder="头类型" filterable>
                <el-option v-for="n in nodeNames" :key="n" :label="n" :value="n" />
              </el-select>
              <el-select v-model="edgeForm.relation" size="small" placeholder="关系" filterable>
                <el-option v-for="r in relNames" :key="r" :label="r" :value="r" />
              </el-select>
              <el-select v-model="edgeForm.tail" size="small" placeholder="尾类型" filterable>
                <el-option v-for="n in nodeNames" :key="n" :label="n" :value="n" />
              </el-select>
              <el-button size="small" type="primary" @click="doAddEdge">加</el-button>
            </div>
          </div>
        </template>

        <!-- 节点详情 -->
        <template v-if="selectedNode">
          <div class="og-panel__head">
            <span class="og-panel__name">{{ selectedNode.name }}</span>
            <span class="og-tag">{{ layerLabel(selectedNode.layer) }}</span>
            <span class="og-tag" :class="{ 'og-tag--strong': selectedNode.isStrong }">
              {{ selectedNode.isStrong ? '强' : '弱' }}
            </span>
            <span v-if="selectedNode.isCandidate" class="og-tag og-tag--cand">待审候选</span>
            <el-button v-if="editMode && !selectedNode.isCandidate" size="small" type="danger" plain @click="doRemoveNode(selectedNode.name)">删除节点</el-button>
          </div>
          <div class="og-panel__sec" v-if="selectedNode.definition">
            <div class="og-panel__label">定义</div>
            <div class="og-panel__text">{{ selectedNode.definition }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedNode.examples.length">
            <div class="og-panel__label">示例</div>
            <div class="og-chips">
              <span v-for="(ex, i) in selectedNode.examples" :key="i" class="og-chip">{{ ex }}</span>
            </div>
          </div>
          <div class="og-panel__sec" v-if="outEdges.length">
            <div class="og-panel__label">出边（作为头类型）</div>
            <div v-for="e in outEdges" :key="e.id" class="og-edge-row">
              <span class="og-rel">{{ e.relationName }}</span> → {{ e.target }}
            </div>
          </div>
          <div class="og-panel__sec" v-if="inEdges.length">
            <div class="og-panel__label">入边（作为尾类型）</div>
            <div v-for="e in inEdges" :key="e.id" class="og-edge-row">
              {{ e.source }} <span class="og-rel">{{ e.relationName }}</span> →
            </div>
          </div>
        </template>

        <!-- 边详情 -->
        <template v-else-if="selectedEdge">
          <div class="og-panel__head">
            <span class="og-panel__name">{{ selectedEdge.relationName }}</span>
            <span v-if="selectedEdge.isCandidate" class="og-tag og-tag--cand">待审候选</span>
            <el-button v-if="editMode && !selectedEdge.isCandidate" size="small" type="danger" plain @click="doRemoveEdge(selectedEdge)">删除边</el-button>
          </div>
          <div class="og-panel__sec">
            <div class="og-panel__label">连接</div>
            <div class="og-panel__text">
              {{ selectedEdge.source }} {{ selectedEdge.isDirected ? '→' : '—' }} {{ selectedEdge.target }}
            </div>
          </div>
          <div class="og-panel__sec">
            <div class="og-panel__label">方向</div>
            <div class="og-panel__text">{{ selectedEdge.isDirected ? '有向' : '无向' }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedEdge.inverseName">
            <div class="og-panel__label">反向名</div>
            <div class="og-panel__text">{{ selectedEdge.inverseName }}</div>
          </div>
          <div class="og-panel__sec" v-if="selectedEdge.definition">
            <div class="og-panel__label">定义</div>
            <div class="og-panel__text">{{ selectedEdge.definition }}</div>
          </div>
        </template>

        <div v-else-if="!editMode" class="og-panel__empty">单击节点或箭头查看定义、示例、连接约束</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDomainStore } from '@/stores/domain'
import { useMiningApi } from '@/api/mining'
import type { ActiveOntology, OntologyCandidate } from '@/types'
import OntologyDiGraph from '@/components/charts/OntologyDiGraph.vue'
import {
  buildOntologyGraph, parseExamples,
  type OntoGraphData, type OntoGraphNode, type OntoGraphEdge,
  type EditableOntology,
  cloneModel, addNode, removeNode, addRelationType, addEdge, removeEdge,
} from './ontologyGraph'

const domainStore = useDomainStore()
const miningApi = useMiningApi()

const loading = ref(false)
const saving = ref(false)
const publishing = ref(false)
const showCandidates = ref(false)
const editMode = ref(false)

const active = reactive<ActiveOntology>({ domain: '', version: null, node_types: [], relation_types: [] })
const candidates = ref<OntologyCandidate[]>([])
const graph = ref<OntoGraphData>({ nodes: [], edges: [] })

// 编辑模式下的本地草稿副本（与 active 解耦，改它不影响只读视图）
const draft = ref<EditableOntology>({ node_types: [], relation_types: [] })

const selectedNode = ref<OntoGraphNode | null>(null)
const selectedEdge = ref<OntoGraphEdge | null>(null)

const nodeForm = reactive({ name: '', layer: 'concept', is_strong: false })
const relForm = reactive({ name: '', is_directed: true, inverse_name: '', definition: '' })
const edgeForm = reactive({ head: '', relation: '', tail: '' })

// 数据指纹：只读轮询时若数据没变就不重画（避免力导布局被打断）。编辑模式不轮询。
let lastSig = ''
function dataSignature(a: ActiveOntology, cands: OntologyCandidate[]): string {
  return JSON.stringify({
    v: a.version?.version_no ?? null,
    n: a.node_types.map(t => [t.name, t.layer, t.is_strong, t.definition, t.examples_json]),
    r: a.relation_types.map(t => [t.name, t.is_directed, t.inverse_name, t.definition, t.allowed_pairs_json]),
    c: cands.map(c => [c.id, c.status, c.kind, c.proposed_name, c.layer, c.payload_json]),
  })
}

const nodeNames = computed(() => draft.value.node_types.map(t => t.name))
const relNames = computed(() => draft.value.relation_types.map(t => t.name))
const currentNodeCount = computed(() => editMode.value ? draft.value.node_types.length : active.node_types.length)

const outEdges = computed(() =>
  selectedNode.value ? graph.value.edges.filter(e => e.source === selectedNode.value!.id) : [])
const inEdges = computed(() =>
  selectedNode.value ? graph.value.edges.filter(e => e.target === selectedNode.value!.id) : [])

function rebuild() {
  // 编辑模式从草稿建图（不显示候选），只读模式从 active 建图
  if (editMode.value) {
    graph.value = buildOntologyGraph(draft.value, [], false)
  } else {
    graph.value = buildOntologyGraph(active, candidates.value, showCandidates.value)
  }
  if (selectedNode.value && !graph.value.nodes.some(n => n.id === selectedNode.value!.id)) selectedNode.value = null
  if (selectedEdge.value && !graph.value.edges.some(e => e.id === selectedEdge.value!.id)) selectedEdge.value = null
}

async function loadAll() {
  loading.value = true
  try {
    const [a, c] = await Promise.all([
      miningApi.getActiveOntology(domainStore.currentDomain),
      miningApi.getOntologyCandidates({ domain: domainStore.currentDomain, status: 'proposed' }),
    ])
    const sig = dataSignature(a, c.items)
    if (sig === lastSig) return
    lastSig = sig
    Object.assign(active, a)
    candidates.value = c.items
    if (!editMode.value) rebuild()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

// ── 编辑模式进出 ──
async function enterEdit() {
  // 先尝试拉后端已存在的草稿；没有就从当前 active 克隆一份
  try {
    const d = await miningApi.getOntologyDraft(domainStore.currentDomain)
    if (d.version) {
      draft.value = cloneModel({ node_types: d.node_types, relation_types: d.relation_types })
    } else {
      draft.value = cloneModel(active)
    }
  } catch {
    draft.value = cloneModel(active)
  }
  selectedNode.value = null
  selectedEdge.value = null
  editMode.value = true
  rebuild()
}

function exitEdit() {
  editMode.value = false
  selectedNode.value = null
  selectedEdge.value = null
  lastSig = '' // 强制下一次轮询重画回 active
  rebuild()
  loadAll()
}

function onNodeClick(id: string) {
  selectedEdge.value = null
  selectedNode.value = graph.value.nodes.find(n => n.id === id) || null
}
function onEdgeClick(id: string) {
  selectedNode.value = null
  selectedEdge.value = graph.value.edges.find(e => e.id === id) || null
}

function layerLabel(l: string) {
  return ({ concept: '概念层', instance: '实例层', property: '属性层' } as Record<string, string>)[l] || l
}

// ── 编辑操作（都作用在 draft 上，然后重画）──
function doAddNode() {
  const name = nodeForm.name.trim()
  if (!name) { ElMessage.warning('请填写类型名'); return }
  if (nodeNames.value.includes(name)) { ElMessage.warning('类型名已存在'); return }
  draft.value = addNode(draft.value, { name, layer: nodeForm.layer, isStrong: nodeForm.is_strong })
  nodeForm.name = ''
  rebuild()
}

function doAddRelationType() {
  const name = relForm.name.trim()
  if (!name) { ElMessage.warning('请填写关系名'); return }
  if (relNames.value.includes(name)) { ElMessage.warning('关系名已存在'); return }
  draft.value = addRelationType(draft.value, {
    name,
    isDirected: relForm.is_directed,
    inverseName: relForm.inverse_name.trim() || undefined,
    definition: relForm.definition.trim() || undefined,
  })
  relForm.name = ''; relForm.inverse_name = ''; relForm.definition = ''
  rebuild()
}

function doAddEdge() {
  const { head, relation, tail } = edgeForm
  if (!head || !relation || !tail) { ElMessage.warning('请选择头类型、关系、尾类型'); return }
  draft.value = addEdge(draft.value, relation, head, tail)
  edgeForm.head = ''; edgeForm.relation = ''; edgeForm.tail = ''
  rebuild()
}

async function doRemoveNode(name: string) {
  const affected = graph.value.edges.filter(e => e.source === name || e.target === name).length
  try {
    await ElMessageBox.confirm(
      `删除节点「${name}」将同时移除与它相连的 ${affected} 条边，确定吗？`,
      '确认删除', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  draft.value = removeNode(draft.value, name)
  selectedNode.value = null
  rebuild()
}

function doRemoveEdge(e: OntoGraphEdge) {
  draft.value = removeEdge(draft.value, e.relationName, e.source, e.target)
  selectedEdge.value = null
  rebuild()
}

// ── 保存 / 发布 ──
function buildSavePayload() {
  return {
    node_types: draft.value.node_types.map(t => ({
      name: t.name, layer: t.layer, is_strong: t.is_strong,
      definition: t.definition ?? null,
      // 字段名必须是 examples_json / allowed_pairs_json，与后端 pydantic
      // DraftNodeTypeModel / DraftRelationTypeModel 的字段一致，否则 pydantic 丢弃→边和示例丢失
      examples_json: parseExamples(t.examples_json),
    })),
    relation_types: draft.value.relation_types.map(t => ({
      name: t.name, layer: t.layer, is_directed: t.is_directed,
      inverse_name: t.inverse_name ?? null,
      allowed_pairs_json: Array.isArray(t.allowed_pairs_json) ? t.allowed_pairs_json : [],
      definition: t.definition ?? null,
    })),
  }
}

async function saveDraft(): Promise<boolean> {
  saving.value = true
  try {
    await miningApi.saveOntologyDraft(domainStore.currentDomain, buildSavePayload())
    ElMessage.success('草稿已保存')
    return true
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '保存失败')
    return false
  } finally {
    saving.value = false
  }
}

async function publishDraft() {
  try {
    await ElMessageBox.confirm('发布后将成为当前生效本体（旧版本归档），确定吗？', '确认发布',
      { type: 'warning', confirmButtonText: '发布', cancelButtonText: '取消' })
  } catch { return }
  publishing.value = true
  try {
    const ok = await saveDraft()
    if (!ok) return
    await miningApi.publishOntologyDraft(domainStore.currentDomain)
    ElMessage.success('已发布新版本')
    editMode.value = false
    selectedNode.value = null
    selectedEdge.value = null
    lastSig = ''
    await loadAll()
  } catch (e: unknown) {
    ElMessage.error(e instanceof Error ? e.message : '发布失败')
  } finally {
    publishing.value = false
  }
}

// ── 实时：轮询 + 重新聚焦刷新（编辑模式下都暂停）──
let timer: ReturnType<typeof setInterval> | null = null
function onFocus() { if (!editMode.value) loadAll() }
function pollTick() {
  if (editMode.value || document.visibilityState !== 'visible' || loading.value) return
  loadAll()
}

onMounted(() => {
  loadAll()
  timer = setInterval(pollTick, 5000)
  window.addEventListener('focus', onFocus)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('focus', onFocus)
})

watch(showCandidates, () => { if (!editMode.value) rebuild() })
watch(() => domainStore.currentDomain, () => {
  if (editMode.value) editMode.value = false // 切场景包时退出编辑，避免草稿串场景
  selectedNode.value = null
  selectedEdge.value = null
  lastSig = ''
  loadAll()
})
</script>

<style scoped>
.og-view { display: flex; flex-direction: column; gap: 14px; height: 100%; }
.og-view__header { display: flex; align-items: center; justify-content: space-between; }
.og-view__header-left { display: flex; align-items: center; gap: 10px; }
.og-view__title { font-size: 16px; font-weight: 650; color: var(--kb-text-primary); margin: 0; letter-spacing: -0.2px; }
.og-view__sub { font-size: 12px; color: var(--kb-text-tertiary); }
.og-view__actions { display: flex; gap: 10px; align-items: center; }
.og-pill { font-size: 11px; padding: 2px 9px; border-radius: 11px; display: inline-flex; align-items: center; gap: 5px; }
.og-pill--live { background: #ecfdf5; color: #059669; }
.og-pill--ro { background: var(--kb-border-light); color: var(--kb-text-secondary); }
.og-pill--edit { background: #fff7ed; color: #f59e0b; border: 1px solid #fed7aa; }
.og-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; }
.og-body { display: grid; grid-template-columns: 1fr 320px; gap: 14px; flex: 1; min-height: 0; }
.og-card { background: var(--kb-bg-card); border-radius: var(--kb-radius); box-shadow: var(--kb-shadow-card); border: 1px solid var(--kb-border-light); padding: 12px; }
.og-graph { min-height: 600px; }
.og-panel { display: flex; flex-direction: column; gap: 12px; overflow: auto; }
.og-edit-sec { display: flex; flex-direction: column; gap: 6px; padding-bottom: 10px; border-bottom: 1px dashed var(--kb-border-light); }
.og-edit-title { font-size: 12px; font-weight: 600; color: var(--kb-text-secondary); }
.og-edit-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.og-edit-row .el-input, .og-edit-row .el-select { flex: 1; min-width: 90px; }
.og-panel__head { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.og-panel__name { font-size: 14px; font-weight: 650; color: var(--kb-text-primary); }
.og-tag { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--kb-border-light); color: var(--kb-text-secondary); }
.og-tag--strong { background: var(--kb-accent-soft); color: var(--kb-accent); }
.og-tag--cand { background: #fff7ed; color: #f59e0b; border: 1px solid #fed7aa; }
.og-panel__sec { display: flex; flex-direction: column; gap: 5px; }
.og-panel__label { font-size: 11px; color: var(--kb-text-tertiary); }
.og-panel__text { font-size: 12px; color: var(--kb-text-secondary); line-height: 1.5; }
.og-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.og-chip { font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--kb-accent-soft); color: var(--kb-accent); }
.og-edge-row { font-size: 12px; color: var(--kb-text-secondary); padding: 2px 0; }
.og-rel { color: var(--kb-accent); }
.og-panel__empty { font-size: 12px; color: var(--kb-text-tertiary); text-align: center; padding-top: 40px; }
</style>
```

- [ ] **Step 2: 类型检查通过**

Run: `cd kb-ui && npx vue-tsc --noEmit`
Expected: 退出码 0。若报 `cloneModel`/`addEdge` 等找不到，回 Task 5 确认这些函数已 `export`。

- [ ] **Step 3: 手动走查（前端无测试框架，靠肉眼）**

启动 `cd kb-ui && npm run dev`，打开本体图谱页，按下列清单逐项确认：
1. 默认只读，右上角显示"只读"灰标，5 秒轮询照常。
2. 点"进入编辑"→ 标头变橙色"编辑中（草稿）"，右侧出现"新建节点/新建关系类型/新建边"三个工具区，轮询停止。
3. 新建一个节点 → 图上立刻多出小圆点；新建关系类型 + 新建边 → 图上立刻多出箭头。
4. 点已有节点 → 详情区出现"删除节点"按钮；点删除 → 弹窗提示"将移除 N 条边" → 确认后节点和相连边一起消失。
5. 点已有边 → "删除边"按钮 → 点击后边消失。
6. 点"保存草稿" → 提示"草稿已保存"（后端 draft 版本被整体覆盖写）。
7. 点"发布版本" → 二次确认 → 提示"已发布新版本" → 自动退出编辑，标头回到只读并显示新版本号。
8. 切换场景包下拉 → 自动退出编辑、重新加载。

- [ ] **Step 4: 提交（仅当用户明确要求提交时；规则同前，只 add 指定文件，绝不 `git add .`）**

```bash
git add kb-ui/src/views/knowledge/OntologyGraphView.vue
git commit -m "feat(ontology-ui): add draft editor mode with save/publish to graph view"
```

---

## 收尾（全部任务完成后）

- [ ] **后端全量回归**

Run: `cd knowledge_mining && python -m pytest tests/test_ontology_draft.py tests/test_ontology_draft_routes.py tests/test_review_gate.py -v`
Expected: 全绿。确认新增的草稿读写/发布逻辑没碰坏已有的本体确认（review gate）链路。

- [ ] **前端类型总检**

Run: `cd kb-ui && npx vue-tsc --noEmit`
Expected: 退出码 0。

- [ ] **端到端冒烟（手动）**

启动后端 + 前端，跑一遍：进入编辑 → 加节点/边 → 保存草稿 → 刷新页面再"进入编辑"应能拉回刚才的草稿（验证 `GET /ontology/draft` 回填）→ 发布 → 确认 active 版本号 +1、旧版本归档、『本体确认』页的候选审核流程不受影响（两条线互不干扰）。

- [ ] **结束开发分支**

使用 `superpowers:finishing-a-development-branch` 收尾。

> ⚠️ 重要：本仓库约定——**未经用户明确指示，不要 `git commit`、不要 `git add -A/.`、不要合并或推送**。以上各任务里的提交步骤仅在用户开口要求提交时才执行；平时只写代码、跑测试、做验证。
