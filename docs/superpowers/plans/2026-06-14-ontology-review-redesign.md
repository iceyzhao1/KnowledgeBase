# 本体评审页重新设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"本体确认"评审页从旧的逃生口候选样式改成匹配归纳候选的样式——补成员证据、判重标注、来源中文化、热度字段对齐，并把 Gate1/Gate2 数字编号彻底改成"实体确认/本体确认"。

**Architecture:** 后端先加一个可单测的判重纯函数 `find_duplicate_type`，归纳阶段把成员实体的结构化证据写进候选载荷，候选接口附加 `duplicate_of`；再做一轮 gate 字符串/字段改名（内部状态 + 接口字段）。前端按新载荷重写"本体确认"页的展开证据区与热度/来源/判重展示，其余页面只改名。最后同步 L1/L2/L3 文档措辞。

**Tech Stack:** 后端 Python（psycopg + FastAPI，pytest 单测）；前端 Vue 3 + Element Plus + TypeScript。

**关键背景（执行者必读）：**
- 数据库 `subloop_stage` 是**自由 TEXT 列，没有 CHECK 约束**（见 `databases/ontology/schemas/001_ontology_concept_postgresql.sql:191` 只 `ADD COLUMN ... TEXT`）。所以本次改名**不需要改任何建库脚本**——这点与设计稿 §4.4 的猜测不同，以此计划为准。
- 用户即将重建远程库，存量 `gate1_ontology`/`gate2_entity` 值无需迁移。
- 候选载荷 `payload.df` = 文档数（篇），`payload.support` = 提及数（次）。旧前端误读 `tf`/`df` 导致热度空白。
- 所有面向用户的文字用中文。

---

## File Structure

**后端**
- `knowledge_mining/mining/infra/ontology_store.py` — 新增模块级纯函数 `find_duplicate_type`（判重）。
- `knowledge_mining/mining/stages/ontology_induction/__init__.py` — `_build_type_candidates` 把成员组装成结构化 dict 落 `payload.members`。
- `knowledge_mining/mining/api/routes/ontology.py` — `/ontology/candidates` 给每条 node_type 候选附 `duplicate_of`。
- `knowledge_mining/mining/api/routes/runs.py` — trace 接口两个 gate 计数字段改名。
- `knowledge_mining/mining/jobs/run.py` — `subloop_stage`/`active_gate` 字符串改名（gate2_entity→entity_review，gate1_ontology→ontology_review）。

**后端测试**
- `knowledge_mining/tests/test_ontology_dup.py` —（新建）判重纯函数单测。
- `knowledge_mining/tests/test_ontology_induction.py` — 加结构化成员断言。
- `knowledge_mining/tests/test_review_gate.py` — gate 字符串断言改新值。

**前端**
- `kb-ui/src/views/knowledge/OntologyReviewView.vue` — 标题/来源/热度/判重徽标/B 小表式展开区。
- `kb-ui/src/views/knowledge/MentionReviewView.vue` — 标题改"实体确认"。
- `kb-ui/src/views/mining/RunDetailView.vue` — `active_gate` 新值 + 字段新名 + 文案。
- `kb-ui/src/types/index.ts` — `RunTrace` 两个字段改名 + `OntologyCandidate` 加 `duplicate_of`。
- `kb-ui/src/api/mining.ts`、`kb-ui/src/router/index.ts` — 注释措辞对齐。

**文档**
- `docs/plans/ontology/ontology-L1-solution-design.md` / `-L2-impl-design.md` / `-L3-impl-plan.md` — Gate1/Gate2 措辞改"实体确认/本体确认"。

---

## Task 1: 判重纯函数 find_duplicate_type

**Files:**
- Modify: `knowledge_mining/mining/infra/ontology_store.py`（在模块顶层、`class OntologyStore` 之前加纯函数）
- Test: `knowledge_mining/tests/test_ontology_dup.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `knowledge_mining/tests/test_ontology_dup.py`：

```python
"""find_duplicate_type 判重纯函数单测：完全重名 / 双向包含 / 命中示例 / 无重复。"""
from __future__ import annotations

from knowledge_mining.mining.infra.ontology_store import find_duplicate_type


def test_exact_name_match_case_and_space_insensitive() -> None:
    existing = [{"name": "网络切片类", "examples": []}]
    assert find_duplicate_type(" 网络切片类 ", existing) == "网络切片类"


def test_bidirectional_containment() -> None:
    # 现有名包含提议名
    assert find_duplicate_type("切片", [{"name": "切片类"}]) == "切片类"
    # 提议名包含现有名
    assert find_duplicate_type("网络切片类", [{"name": "切片类"}]) == "切片类"


def test_match_against_example() -> None:
    existing = [{"name": "网络功能", "examples": ["UPF", "SMF"]}]
    assert find_duplicate_type("upf", existing) == "网络功能"


def test_no_duplicate_returns_none() -> None:
    existing = [{"name": "协议类", "examples": ["PFCP"]}]
    assert find_duplicate_type("接口", existing) is None


def test_blank_proposed_returns_none() -> None:
    assert find_duplicate_type("   ", [{"name": "协议类"}]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_ontology_dup.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_duplicate_type'`

- [ ] **Step 3: 实现纯函数**

在 `knowledge_mining/mining/infra/ontology_store.py` 里，`logger = ...` 之后、`class OntologyStore` 之前插入：

```python
def _norm_type_name(s: str) -> str:
    """判重用归一化：去全部空白 + 小写。"""
    return "".join((s or "").split()).lower()


def find_duplicate_type(proposed_name: str, existing_types: list[dict[str, Any]]) -> str | None:
    """判断提议的点类型名是否与现有类型重复，命中返回现有类型名，否则 None。

    点类型无别名字段，以"示例"代偿。规则（任一命中即重复）：
    1. 归一化后与现有 name 完全相同；
    2. 提议名与现有 name 双向子串包含（"切片类"含"切片"）；
    3. 提议名命中现有类型的某个 example。

    existing_types：每项含 "name"（必需）和可选 "examples": list[str]。
    """
    p = _norm_type_name(proposed_name)
    if not p:
        return None
    for t in existing_types:
        name = t.get("name") or ""
        n = _norm_type_name(name)
        if not n:
            continue
        if p == n or p in n or n in p:
            return name
        for ex in (t.get("examples") or []):
            if _norm_type_name(str(ex)) == p:
                return name
    return None
```

> `Any` 已在该文件顶部 `from typing import ... Any` 导入，无需新增 import。若文件顶部未导入 `Any`，先补 `from typing import Any`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_ontology_dup.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/infra/ontology_store.py knowledge_mining/tests/test_ontology_dup.py
git commit -m "feat(ontology): 加点类型判重纯函数 find_duplicate_type"
```

---

## Task 2: 归纳阶段补结构化成员证据

**Files:**
- Modify: `knowledge_mining/mining/stages/ontology_induction/__init__.py:54-109`（`_build_type_candidates` 成员组装）
- Test: `knowledge_mining/tests/test_ontology_induction.py`

- [ ] **Step 1: 写失败测试**

在 `knowledge_mining/tests/test_ontology_induction.py` 末尾追加：

```python
def test_members_carry_structured_evidence() -> None:
    # payload.members 要带 entity_id/name/篇数/次数/原文摘录，供评审页小表展示
    ent = {
        "id": "e1", "canonical_name": "网络切片",
        "document_count": 2, "mention_count": 5,
        "members": [{"quote": "网络切片是一种逻辑上的专用网络"}],
    }
    llm = {"new_types": [{"type_name": "network_slice", "members": ["网络切片"]}]}
    cands = _build_type_candidates([ent], llm, min_df=2)
    assert len(cands) == 1
    m = cands[0].members[0]
    assert m["entity_id"] == "e1"
    assert m["name"] == "网络切片"
    assert m["document_count"] == 2
    assert m["mention_count"] == 5
    assert "网络切片" in m["quote"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_ontology_induction.py::test_members_carry_structured_evidence -v`
Expected: FAIL — `TypeError: string indices must be integers`（当前 `members` 是名字字符串列表，`m["entity_id"]` 取不到）

- [ ] **Step 3: 改成结构化成员**

在 `knowledge_mining/mining/stages/ontology_induction/__init__.py` 的 `_build_type_candidates` 里，把成员循环段（当前 81-94 行，从 `member_ids: list[str] = []` 到 `support += int(ent.get("mention_count") or 0)`）整体替换为：

```python
        member_ids: list[str] = []
        member_objs: list[dict[str, Any]] = []
        df = 0
        support = 0
        seen: set[str] = set()
        for raw in nt.get("members", []) or []:
            key = (str(raw) or "").strip().lower()
            ent = by_name.get(key)
            if ent is None or ent["id"] in seen:
                continue
            seen.add(ent["id"])
            dc = int(ent.get("document_count") or 0)
            mc = int(ent.get("mention_count") or 0)
            quote = ""
            for mem in (ent.get("members") or []):
                if mem.get("quote"):
                    quote = str(mem["quote"])[:120]
                    break
            member_ids.append(ent["id"])
            member_objs.append({
                "entity_id": ent["id"],
                "name": ent.get("canonical_name") or str(raw),
                "document_count": dc,
                "mention_count": mc,
                "quote": quote,
            })
            df += dc
            support += mc
```

然后把同函数末尾的 `out.append(TypeCandidate(...))` 调用里的 `members=member_names` 改为 `members=member_objs`（`member_names` 这个变量已删除，不再引用）。改完该 append 应为：

```python
        out.append(TypeCandidate(
            type_name=type_name,
            definition=(nt.get("definition") or "").strip(),
            examples=[str(x) for x in (nt.get("examples") or [])][:5],
            layer=(nt.get("layer") or "concept"),
            member_entity_ids=member_ids, members=member_objs,
            df=df, support=support,
        ))
```

> `TypeCandidate.members` 的类型语义从 `list[str]` 变成 `list[dict]`，`__slots__` 不用动。`induce()` 里 `payload={... "members": c.members ...}`（197 行附近）自动跟着变成结构化，无需改。`member_entity_ids` 仍是字符串列表，N5 回贴逻辑（`accepted_node_type_members`）不受影响。

- [ ] **Step 4: 跑全套归纳测试确认通过**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_ontology_induction.py -v`
Expected: PASS（原有用例 + 新增 `test_members_carry_structured_evidence` 全绿。原有 `test_members_mapped_to_ids_and_kept` 断言的是 `member_entity_ids`/`df`/`support`，不受影响）

- [ ] **Step 5: 提交**

```bash
git add knowledge_mining/mining/stages/ontology_induction/__init__.py knowledge_mining/tests/test_ontology_induction.py
git commit -m "feat(induction): 候选载荷 payload.members 改成结构化成员证据"
```

---

## Task 3: 候选接口附加 duplicate_of

**Files:**
- Modify: `knowledge_mining/mining/api/routes/ontology.py:126-133`（`list_ontology_candidates`）+ 顶部 import

- [ ] **Step 1: 加 import 与归一化辅助**

在 `knowledge_mining/mining/api/routes/ontology.py` 顶部 import 段：
- `import asyncio` 之后加 `import json`；
- 把 `from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore` 改为
  `from knowledge_mining.mining.infra.ontology_store import OntologyStore, GraphStore, find_duplicate_type`。

在 `_stores(...)` 函数定义之后（模块级）加一个把现有类型行规整成判重输入的辅助：

```python
def _existing_type_for_dup(node_type_row: dict) -> dict:
    """把 active_node_types 行规整成 find_duplicate_type 需要的 {name, examples}。

    examples_json 在库里以 JSON 文本存储，需 loads；非列表一律当空。
    """
    ex = node_type_row.get("examples_json")
    if isinstance(ex, str):
        try:
            ex = json.loads(ex)
        except Exception:
            ex = []
    if not isinstance(ex, list):
        ex = []
    return {"name": node_type_row.get("name") or "", "examples": ex}
```

- [ ] **Step 2: 改 list_ontology_candidates 加 duplicate_of**

把 `list_ontology_candidates` 函数体（126-133 行）替换为：

```python
@router.get("/ontology/candidates")
async def list_ontology_candidates(
    request: Request, domain: str = _DEFAULT_DOMAIN, status: str = "proposed",
) -> dict:
    """本体确认评审列表：某状态的本体候选（默认待审 proposed）。

    每条 node_type 候选附 duplicate_of：与当前 active 本体的现有类型判重，命中则为
    现有类型名（前端显示红色"疑似重复"徽标），无则 None。relation_type 暂不判重。
    """
    onto, _ = _stores(request)
    rows = await _run(onto.list_candidates, domain, status=status)
    node_types = await _run(onto.active_node_types, domain)
    existing = [_existing_type_for_dup(n) for n in node_types]
    items: list[dict] = []
    for r in rows:
        d = dict(r)
        if d.get("kind") == "node_type":
            d["duplicate_of"] = find_duplicate_type(d.get("proposed_name") or "", existing)
        else:
            d["duplicate_of"] = None
        items.append(d)
    return {"domain": domain, "status": status, "items": items}
```

- [ ] **Step 3: 静态校验（无 route 单测，纯函数已覆盖）**

Run: `cd /e/MyProjects/KnowledgeBase && python -c "import knowledge_mining.mining.api.routes.ontology"`
Expected: 无报错（import 通过，证明语法/引用正确）。判重逻辑本身已由 Task 1 的单测覆盖，这里只接线。

- [ ] **Step 4: 提交**

```bash
git add knowledge_mining/mining/api/routes/ontology.py
git commit -m "feat(api): /ontology/candidates 给点类型候选附 duplicate_of"
```

---

## Task 4: 后端 gate 字符串与字段改名

**Files:**
- Modify: `knowledge_mining/mining/jobs/run.py:331-347,372-374,957,962`
- Modify: `knowledge_mining/mining/api/routes/runs.py:768-788,816-817`
- Test: `knowledge_mining/tests/test_review_gate.py:23-35`

改名映射：`gate2_entity` → `entity_review`；`gate1_ontology` → `ontology_review`；接口字段 `gate1_proposed_candidates` → `ontology_proposed_candidates`，`gate2_pending_mentions` → `entity_pending_mentions`。

- [ ] **Step 1: 先改测试断言（失败先行）**

`knowledge_mining/tests/test_review_gate.py` 改两处断言：
- 第 28 行 `assert _check_review_gate(asset, "run1", "dom") == "gate2_entity"` → `== "entity_review"`
- 第 35 行 `assert _check_review_gate(asset, "run1", "dom") == "gate1_ontology"` → `== "ontology_review"`

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_review_gate.py::test_gate2_takes_priority_over_gate1 knowledge_mining/tests/test_review_gate.py::test_gate1_when_no_pending_mentions -v`
Expected: FAIL — 实际仍返回旧字符串 `gate2_entity`/`gate1_ontology`

- [ ] **Step 3: 改 run.py 字符串**

在 `knowledge_mining/mining/jobs/run.py` 把所有 `"gate2_entity"` 替换为 `"entity_review"`、所有 `"gate1_ontology"` 替换为 `"ontology_review"`（共 6 处字符串字面量：333、336、339、344、347、372、374、957、962 附近——以实际匹配为准，全部替换）。同时把这些行附近注释里的"Gate2/Gate1"措辞改成"实体确认/本体确认"，例如：
- 331 行注释 `# Gate2（实体确认）：...` → `# 实体确认：...`
- 338 行注释 `# Gate2 刚清空（上一步停在 gate2_entity）...` → `# 实体确认刚清空（上一步停在 entity_review）...`
- 342 行注释 `# Gate1（本体确认）：...` → `# 本体确认：...`
- 339 行 `if prev_stage == "gate2_entity":` → `if prev_stage == "entity_review":`

`_check_review_gate` 的 docstring（367-369 行）里"Gate2 实体确认在前、Gate1 本体确认在后"可保留语义、把"Gate2/Gate1"去掉，例如改为"实体确认在前、本体确认在后"。

> 用编辑器全局替换更稳妥：在该文件内 `gate2_entity`→`entity_review`、`gate1_ontology`→`ontology_review` 全量替换，再人工扫一遍注释措辞。

- [ ] **Step 4: 改 runs.py trace 字段名**

在 `knowledge_mining/mining/api/routes/runs.py` 的 `get_run_trace` 返回 dict（800-821 行）：
- 第 816 行 `"gate1_proposed_candidates": proposed_candidates,` → `"ontology_proposed_candidates": proposed_candidates,`
- 第 817 行 `"gate2_pending_mentions": pending_mentions,` → `"entity_pending_mentions": pending_mentions,`

同时把 768、782 行附近注释 `# Gate1: ...` / `# Gate2: ...` 改为 `# 本体确认: ...` / `# 实体确认: ...`。

> 注意 `active_gate` 字段（807 行）取的是 `run["subloop_stage"]` 原值，run.py 改名后它自动变成新值，无需改这一行。

- [ ] **Step 5: 跑改名相关测试 + 全量回归**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/test_review_gate.py knowledge_mining/tests/test_ontology_induction.py knowledge_mining/tests/test_ontology_dup.py -v`
Expected: PASS（全绿）

- [ ] **Step 6: 提交**

```bash
git add knowledge_mining/mining/jobs/run.py knowledge_mining/mining/api/routes/runs.py knowledge_mining/tests/test_review_gate.py
git commit -m "refactor(gate): 内部 gate 状态/接口字段去数字编号（entity_review/ontology_review）"
```

---

## Task 5: 前端类型定义对齐

**Files:**
- Modify: `kb-ui/src/types/index.ts:304-318`（RunTrace）、`357-370`（OntologyCandidate）

- [ ] **Step 1: 改 RunTrace 两个字段名**

`kb-ui/src/types/index.ts` 第 313-314 行：

```ts
  ontology_proposed_candidates: number
  entity_pending_mentions: number
```

（替换原 `gate1_proposed_candidates` / `gate2_pending_mentions`）

- [ ] **Step 2: 给 OntologyCandidate 加 duplicate_of**

`kb-ui/src/types/index.ts` 的 `OntologyCandidate` 接口（357-370 行）在 `created_at?` 之后加一行：

```ts
  duplicate_of?: string | null
```

- [ ] **Step 3: 类型检查**

Run: `cd /e/MyProjects/KnowledgeBase/kb-ui && npx vue-tsc --noEmit`
Expected: 报错仅出现在还没改的 `RunDetailView.vue`（引用旧字段名）——这是预期，下一个任务修；`types/index.ts` 本身不应有错。

> 若 `vue-tsc` 太慢/不可用，可跳过此步，靠 Task 6/7 的页面改动 + 构建验证兜底。

- [ ] **Step 4: 提交**

```bash
git add kb-ui/src/types/index.ts
git commit -m "refactor(ui): RunTrace 字段改名 + OntologyCandidate 加 duplicate_of"
```

---

## Task 6: 重写"本体确认"评审页

**Files:**
- Modify: `kb-ui/src/views/knowledge/OntologyReviewView.vue`

- [ ] **Step 1: 改标题与副标题**

把模板第 5 行 `<h2 class="rev-view__title">Gate1 · 本体候选评审</h2>` 改为：

```html
        <h2 class="rev-view__title">本体确认</h2>
```

- [ ] **Step 2: 来源中文表补"全局归纳"**

把 `SOURCE_LABELS`（134-138 行）改为：

```ts
const SOURCE_LABELS: Record<string, string> = {
  global_induction: '全局归纳',
  escape_hatch: '逃生口',
  seed: '种子',
  manual: '人工',
}
```

- [ ] **Step 3: 修热度字段错位（读 df/support）**

把"热度"列模板（78-88 行）替换为：

```html
        <el-table-column label="热度" width="130">
          <template #default="{ row }">
            <span class="rev-heat" v-if="row.kind === 'node_type' && payloadNum(row, 'df') != null">
              <b>{{ payloadNum(row, 'df') }}</b> 篇 / <b>{{ payloadNum(row, 'support') }}</b> 次
            </span>
            <span class="text-muted" v-else-if="row.kind === 'relation_type' && payloadNum(row, 'cooccur') != null">
              共现 <b>{{ payloadNum(row, 'cooccur') }}</b> 次
            </span>
            <span class="text-muted" v-else>-</span>
          </template>
        </el-table-column>
```

- [ ] **Step 4: 提议名旁加"疑似重复"徽标**

把"提议名称"列模板（71-77 行）替换为：

```html
        <el-table-column label="提议名称" min-width="200">
          <template #default="{ row }">
            <span class="rev-name">{{ row.proposed_name }}</span>
            <span v-if="row.duplicate_of" class="chip chip--dup">⚠ 疑似重复：{{ row.duplicate_of }}</span>
            <span v-if="row.status === 'accepted'" class="tag-ok">已通过</span>
            <span v-else-if="row.status === 'rejected'" class="tag-no">已拒绝</span>
          </template>
        </el-table-column>
```

- [ ] **Step 5: 展开区改 B 小表式（成员实体证据）**

把整个 `type="expand"` 列的 `#default` 模板（28-55 行，从 `<div class="rev-expand">` 到对应 `</div>`）替换为：

```html
          <template #default="{ row }">
            <div class="rev-expand">
              <div v-if="payloadStr(row, 'definition')" class="rev-expand__line">
                <span class="rev-expand__label">描述：</span>{{ payloadStr(row, 'definition') }}
              </div>
              <div v-if="row.kind === 'node_type'" class="rev-expand__line">
                <span class="rev-expand__label">属性：</span>
                层级={{ payloadStr(row, 'layer') || '概念' }}
                · {{ payloadStr(row, 'layer') }}
                <template v-if="payloadArr(row, 'examples').length">
                  · 示例：{{ payloadArr(row, 'examples').join(' / ') }}
                </template>
              </div>
              <div v-if="membersOf(row).length" class="rev-expand__members">
                <div class="rev-expand__label rev-expand__members-title">
                  成员实体（{{ membersOf(row).length }}）—— 这个类型由这些你确认过的实体归纳而来：
                </div>
                <table class="rev-member-table">
                  <thead>
                    <tr><th>成员实体</th><th class="rev-member-heat">热度</th><th>原文摘录</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(m, i) in membersOf(row)" :key="i">
                      <td>{{ m.name }}</td>
                      <td class="rev-member-heat">{{ m.document_count ?? 0 }}篇/{{ m.mention_count ?? 0 }}次</td>
                      <td class="rev-member-quote">{{ m.quote ? `「${m.quote}」` : '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="row.kind === 'relation_type'" class="rev-expand__line text-muted">
                共现示例：{{ payloadArr(row, 'examples').join(' · ') || '无' }}
              </div>
              <div v-if="row.kind === 'node_type' && !membersOf(row).length && !payloadStr(row, 'definition')" class="text-muted">
                该候选无附加证据
              </div>
            </div>
          </template>
```

- [ ] **Step 6: 加 Member 类型与 membersOf 取值函数**

在 `<script setup>` 里，`type Evidence = ...`（154 行）之后加：

```ts
type Member = {
  entity_id?: string
  name?: string
  document_count?: number
  mention_count?: number
  quote?: string
}
function membersOf(row: OntologyCandidate): Member[] {
  const v = payloadOf(row)['members']
  return Array.isArray(v) ? (v as Member[]) : []
}
```

`reasonsOf` / `quotesOf` 若不再被模板引用可保留（不强制删，避免误删其他引用）；但新模板未用到 `reasonLabel`/`REASON_LABELS`/`reasonsOf`/`quotesOf`，可在确认无其他引用后删除以保持整洁——非必须。

- [ ] **Step 7: 加徽标与小表样式**

在 `<style scoped>` 末尾（263 行 `</style>` 之前）加：

```css
.chip--dup { background: rgba(245, 108, 108, 0.14); color: var(--kb-danger); font-weight: 600; margin-left: 8px; }
.rev-expand__line { font-size: 13px; color: var(--kb-text-secondary); }
.rev-expand__members { margin-top: 4px; }
.rev-expand__members-title { margin: 6px 0 4px; }
.rev-member-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.rev-member-table th { text-align: left; color: var(--kb-text-tertiary); font-weight: 500; border-bottom: 1px solid var(--kb-border-light); padding: 4px 6px; }
.rev-member-table td { padding: 4px 6px; border-bottom: 1px solid var(--kb-border-light); color: var(--kb-text-secondary); }
.rev-member-heat { width: 90px; }
.rev-member-quote { color: var(--kb-text-secondary); }
```

- [ ] **Step 8: 构建验证**

Run: `cd /e/MyProjects/KnowledgeBase/kb-ui && npx vue-tsc --noEmit`
Expected: 本文件无类型错误（`row.duplicate_of` 因 Task 5 已加字段而合法）。

- [ ] **Step 9: 提交**

```bash
git add kb-ui/src/views/knowledge/OntologyReviewView.vue
git commit -m "feat(ui): 本体确认页改名+来源中文+热度对齐+判重徽标+B小表证据区"
```

---

## Task 7: 其余前端改名（实体确认页 / Run 详情 / 注释）

**Files:**
- Modify: `kb-ui/src/views/knowledge/MentionReviewView.vue:5`
- Modify: `kb-ui/src/views/mining/RunDetailView.vue:56-72`
- Modify: `kb-ui/src/api/mining.ts:243,259`、`kb-ui/src/router/index.ts:70`（仅注释）

- [ ] **Step 1: 实体确认页标题**

`kb-ui/src/views/knowledge/MentionReviewView.vue` 第 5 行 `<h2 class="m-view__title">Gate2 · 实体确认</h2>` 改为：

```html
        <h2 class="m-view__title">实体确认</h2>
```

- [ ] **Step 2: RunDetailView 暂停横幅改新 gate 值与字段**

`kb-ui/src/views/mining/RunDetailView.vue` 把 56-62 行替换为：

```html
              <template v-if="trace?.active_gate === 'ontology_review'">
                本体确认：{{ trace?.ontology_proposed_candidates ?? 0 }} 条待审
              </template>
              <template v-else-if="trace?.active_gate === 'entity_review'">
                实体确认：{{ trace?.entity_pending_mentions ?? 0 }} 条待确认
              </template>
              <template v-else>评审完成后可继续</template>
```

把 67-72 行替换为：

```html
          <router-link v-if="trace?.active_gate === 'ontology_review'" :to="`/candidates/review?run_id=${props.runId}`">
            <el-button type="primary">去评审本体候选</el-button>
          </router-link>
          <router-link v-else-if="trace?.active_gate === 'entity_review'" :to="`/mentions/review?run_id=${props.runId}`">
            <el-button type="primary">去确认实体</el-button>
          </router-link>
```

- [ ] **Step 3: 注释措辞对齐（非功能）**

- `kb-ui/src/api/mining.ts` 第 243 行注释 `// Gate1 本体候选评审` → `// 本体确认评审`；第 259 行 `// Gate2 实体确认` → `// 实体确认`。
- `kb-ui/src/router/index.ts` 第 70 行注释里的"Gate1 候选评审 ... 仿 Gate2 ..." → 用"本体确认 ... 仿实体确认 ..."措辞。

- [ ] **Step 4: 全量类型检查 + 构建**

Run: `cd /e/MyProjects/KnowledgeBase/kb-ui && npx vue-tsc --noEmit`
Expected: 全项目无类型错误（旧字段名已全部消除）。

Run: `cd /e/MyProjects/KnowledgeBase/kb-ui && npm run build`
Expected: 构建成功。

- [ ] **Step 5: 提交**

```bash
git add kb-ui/src/views/knowledge/MentionReviewView.vue kb-ui/src/views/mining/RunDetailView.vue kb-ui/src/api/mining.ts kb-ui/src/router/index.ts
git commit -m "refactor(ui): 实体确认页/Run详情/注释统一去 Gate 数字编号"
```

---

## Task 8: 同步 L1/L2/L3 文档措辞

**Files:**
- Modify: `docs/plans/ontology/ontology-L1-solution-design.md`（10 处 Gate1/Gate2）
- Modify: `docs/plans/ontology/ontology-L2-impl-design.md`（26 处）
- Modify: `docs/plans/ontology/ontology-L3-impl-plan.md`（19 处）

- [ ] **Step 1: 逐文件替换措辞**

对三个文件做**带语义**的替换（不能盲目 sed，因为 Gate1=本体确认、Gate2=实体确认，方向不能错）：
- "Gate1" → "本体确认"
- "Gate2" → "实体确认"
- 若上下文是内部状态值（如写到 `gate1_ontology`/`gate2_entity` 字面量），改为 `ontology_review`/`entity_review`。
- 出现"Gate1/Gate2 两道闸"这类并列措辞，改为"实体确认/本体确认两道检查点"，并保持"实体确认在前、本体确认在后"的顺序表述。

建议用 Grep 逐处定位后用 Edit 改：
Run: `grep -n "Gate1\|Gate2\|gate1_\|gate2_" docs/plans/ontology/ontology-L1-solution-design.md docs/plans/ontology/ontology-L2-impl-design.md docs/plans/ontology/ontology-L3-impl-plan.md`

- [ ] **Step 2: 确认无残留**

Run: `grep -rn "Gate1\|Gate2\|gate1_ontology\|gate2_entity" docs/plans/ontology/`
Expected: 无输出（全部替换干净）。

- [ ] **Step 3: 提交**

```bash
git add docs/plans/ontology/ontology-L1-solution-design.md docs/plans/ontology/ontology-L2-impl-design.md docs/plans/ontology/ontology-L3-impl-plan.md
git commit -m "docs(ontology): L1/L2/L3 Gate1/Gate2 措辞改为实体确认/本体确认"
```

---

## 最终验收

- [ ] **后端全量测试**

Run: `cd /e/MyProjects/KnowledgeBase && python -m pytest knowledge_mining/tests/ -q`
Expected: 全绿（重点 test_review_gate / test_ontology_induction / test_ontology_dup）。

- [ ] **前端构建**

Run: `cd /e/MyProjects/KnowledgeBase/kb-ui && npm run build`
Expected: 成功。

- [ ] **无残留旧编号**

Run: `grep -rn "gate1_ontology\|gate2_entity\|gate1_proposed_candidates\|gate2_pending_mentions" knowledge_mining/ kb-ui/src/`
Expected: 无输出。

---

## Self-Review（写计划者已核对）

- **Spec 覆盖**：§3 改名 → Task 4/5/6/7/8；§4.1 补证据 → Task 2；§4.2 判重纯函数 → Task 1；§4.3 接口标注 → Task 3；§4.4 改名落地 → Task 4（已确认 subloop_stage 无 CHECK 约束，故无建库脚本改动，纠正了 spec §4.4 的猜测）；§5 本体确认页 → Task 6；§6 其余前端 → Task 5/7；§7 测试+文档 → Task 1/2/4/8。
- **类型一致性**：`find_duplicate_type(proposed_name, existing_types)` 在 Task 1 定义、Task 3 调用，签名一致；`payload.members` 结构 `{entity_id,name,document_count,mention_count,quote}` 在 Task 2 产出、Task 6 `Member` 类型与 `membersOf` 消费，字段名一致；新字段名 `ontology_proposed_candidates`/`entity_pending_mentions` 在 Task 4（后端产出）与 Task 5/7（前端消费）一致。
- **占位符**：无 TBD/TODO；每个改代码的步骤都给了完整代码块。
