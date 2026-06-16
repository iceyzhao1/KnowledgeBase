# 评估工作台改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一「项目=题库 / 测试集」概念、退役黄金库门禁、重做 4 步工作流、补齐删除项目 / 导入命名 / 测试集编辑能力。

**Architecture:** 三服务结构不变（eval-web 纯 JS SPA / eval-api FastAPI / eval-llm）。后端改动集中在 `eval_api/app.py`、`orchestrator.py`、`sqlite_store.py`、`shared/models.py`；前端改动集中在 `eval_web/static/app.js` + `styles.css`。黄金表/接口保留但评分路径不再走「确认门禁」。

**Tech Stack:** Python 3 / FastAPI / pydantic / SQLite；前端 vanilla JS（无构建、无测试框架）；后端用 pytest。

**工作目录：** `E:\MyProjects\KnowledgeBase\runtime_eval`（命令都在此目录跑）。源码在嵌套的 `runtime_eval/runtime_eval/...`。测试：`python -m pytest tests/...`。

---

## 文件清单（改动地图）

后端：
- `runtime_eval/shared/models.py` — `TestSuite` 加 `name: str = ""` 字段。
- `runtime_eval/eval_api/app.py` — `_suite_dict`/`_suite_brief` 带 name；`import_suite` 收 name；新增 `PUT /suites/{sid}`（编辑用例）；新增 `DELETE /projects/{pid}`（级联）。
- `runtime_eval/eval_api/orchestrator.py` — `confirmed_gold_facts` 退役为直接 `gold_facts(case)`。
- `runtime_eval/eval_api/sqlite_store.py` — 新增 `delete_project(project_id)` 级联删除。

前端：
- `runtime_eval/eval_web/static/app.js` — `FLOW_STEPS` 重定义为 4 新步；`renderWork` 去掉纯文字提示行；`renderFlowStepper` 重做；新增第②步「题库题目总览」+ 编辑；删黄金 UI；项目列表加删除；导入加命名弹框。
- `runtime_eval/eval_web/static/styles.css` — 步骤条/编辑表/弹框样式微调。

测试：
- `runtime_eval/tests/test_eval_api.py` — 新增 DELETE 级联、导入命名、PUT 编辑、去门禁后检索用例。

---

## Task 1: TestSuite 加 name 字段

**Files:**
- Modify: `runtime_eval/runtime_eval/shared/models.py:119-127`

- [ ] **Step 1: 加字段**

在 `TestSuite` 的 `backend` 字段后加 `name`：

```python
class TestSuite(BaseModel):
    suite_id: str
    project_id: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    backend: str = "unknown"  # provider used to generate (reported by eval-llm)
    name: str = ""  # 用户可读显示名（导入时填；空则前端回落到 corpus_files/backend）
    corpus_files: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    generation_usage: TokenUsage = Field(default_factory=TokenUsage)
    cases: list[TestCase] = Field(default_factory=list)
```

- [ ] **Step 2: 跑现有套件确认没破坏**

Run: `python -m pytest tests/ -q`
Expected: 全绿（新字段有默认值，向后兼容）。

- [ ] **Step 3: Commit**

```bash
git add runtime_eval/runtime_eval/shared/models.py
git commit -m "feat(models): TestSuite 加 name 显示名字段"
```

---

## Task 2: 导入测试集支持命名

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/app.py:417-452`（`_suite_dict`/`_suite_brief`）
- Modify: `runtime_eval/runtime_eval/eval_api/app.py:686-707`（`import_suite`）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_eval_api.py` 末尾加（参考文件已有的 client fixture / 建项目辅助；若无 `_client`/建项目 helper，按文件现有风格手搓一个 `TestClient(create_app(...))` 并先 POST 建项目拿 pid）：

```python
def test_import_suite_with_custom_name(tmp_path):
    from fastapi.testclient import TestClient
    from runtime_eval.eval_api.app import create_app
    from runtime_eval.eval_api.config import ApiConfig

    app = create_app(config=ApiConfig(workspace_dir=tmp_path))
    cl = TestClient(app)
    pid = cl.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]

    yaml_body = (
        "- id: c1\n"
        "  question: 啥是A\n"
        "  question_type: factoid\n"
        "  expected_answer: A是A\n"
        "  source: {doc: d}\n"
    )
    r = cl.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "2024客服题"},
        files={"file": ("cases.yaml", yaml_body, "text/yaml")},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "2024客服题"
```

> 注：若 `ApiConfig` 必填项更多，按 `tests/conftest.py` 现有 fixture 取一个可用的 config/store（先 `grep` conftest 里的 `ApiConfig(`/`create_app(` 用法照抄）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_eval_api.py::test_import_suite_with_custom_name -v`
Expected: FAIL（当前 `import_suite` 不收 name，返回 json 无 `name` 或为空）。

- [ ] **Step 3: `_suite_dict` / `_suite_brief` 输出 name**

`app.py` `_suite_dict`（约 417 行）在 `"backend"` 后加一行：

```python
        "backend": suite.backend,
        "name": suite.name,
```

`_suite_brief`（约 443 行）同样在 `"backend"` 后加：

```python
        "backend": suite.backend,
        "name": suite.name,
```

- [ ] **Step 4: `import_suite` 收 name 表单字段**

把 `app.py:686-707` 的 `import_suite` 改为：

```python
    @app.post("/api/v1/projects/{pid}/suites:import")
    async def import_suite(
        pid: str,
        file: UploadFile = File(...),
        name: str = Form(""),
    ):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        filename = file.filename or "suite.yaml"
        data = await file.read()
        doc_ref = filename.rsplit(".", 1)[0]
        try:
            cases = parse_suite_file(name=filename, data=data, doc_ref=doc_ref)
        except ImportError_ as exc:
            raise HTTPException(400, str(exc))
        if not cases:
            raise HTTPException(400, "未解析到任何用例")
        suite = TestSuite(
            suite_id=f"suite_{uuid.uuid4().hex[:10]}",
            project_id=pid,
            backend="imported",
            name=(name.strip() or doc_ref),
            corpus_files=[filename],
            cases=cases,
        )
        store.save_suite(suite)
        return _suite_dict(suite)
```

确认文件顶部 import 含 `Form`（`from fastapi import ... Form ...`）；若没有，加上。

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_eval_api.py::test_import_suite_with_custom_name -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/app.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(suites): 导入测试集支持自定义名称"
```

---

## Task 3: 黄金库门禁退役（评分直接用用例自带事实）

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/orchestrator.py:538-548`
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 写失败测试（未确认黄金也应参与打分）**

在 `tests/test_eval_api.py` 加（直接测纯函数）：

```python
def test_confirmed_gold_facts_ignores_gate(tmp_path):
    from runtime_eval.eval_api import orchestrator
    from runtime_eval.eval_api.config import ApiConfig
    from runtime_eval.eval_api.sqlite_store import SqliteStore
    from runtime_eval.shared.models import (
        GoldRecord, QuestionType, SourceRef, TestCase,
    )

    store = SqliteStore(ApiConfig(workspace_dir=tmp_path))
    case = TestCase(
        id="c1", question="啥是A", question_type=QuestionType.FACTOID,
        expected_answer="A是A", expected_evidence=["事实X"],
        source=SourceRef(doc="d"),
    )
    # 存一条「草稿」黄金（旧逻辑会因未确认而返回空）
    store.save_gold(GoldRecord(
        fingerprint=GoldRecord.make_fingerprint(case.question),
        question=case.question, question_type=QuestionType.FACTOID,
        expected_answer="A是A", status="draft",
    ))
    # 退役后：门禁失效，直接用用例自带事实
    assert orchestrator.confirmed_gold_facts(store, case) == ["事实X"]
```

> 若 `GoldRecord` 必填字段更多，先 `grep "class GoldRecord"` 照其字段补齐最小构造。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_eval_api.py::test_confirmed_gold_facts_ignores_gate -v`
Expected: FAIL（旧逻辑草稿返回 `[]`）。

- [ ] **Step 3: 退役门禁**

把 `orchestrator.py:538-548` 改为：

```python
def confirmed_gold_facts(store: Store, case: TestCase) -> list[str]:
    """黄金库已轻量退役：检索层评分直接用用例自带事实，不再卡「已确认」门禁。

    保留函数名与签名，避免大面积改调用点；黄金表/接口仍在但不参与打分。
    """

    return gold_facts(case)
```

- [ ] **Step 4: 跑测试确认通过 + 全套件回归**

Run: `python -m pytest tests/test_eval_api.py::test_confirmed_gold_facts_ignores_gate -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: 全绿（若有断言「草稿不参与打分」的旧测试转红，按本退役语义更新该断言——草稿现在也参与）。

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/orchestrator.py runtime_eval/tests/test_eval_api.py
git commit -m "refactor(gold): 黄金门禁轻量退役，评分直用用例自带事实"
```

---

## Task 4: 编辑测试集（替换用例集）接口

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/app.py`（在 `get_suite` 之后加 PUT）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_put_suite_replaces_cases(tmp_path):
    from fastapi.testclient import TestClient
    from runtime_eval.eval_api.app import create_app
    from runtime_eval.eval_api.config import ApiConfig

    cl = TestClient(create_app(config=ApiConfig(workspace_dir=tmp_path)))
    pid = cl.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    yaml_body = (
        "- id: c1\n  question: q1\n  question_type: factoid\n"
        "  expected_answer: a1\n  source: {doc: d}\n"
    )
    sid = cl.post(
        f"/api/v1/projects/{pid}/suites:import",
        data={"name": "S"},
        files={"file": ("c.yaml", yaml_body, "text/yaml")},
    ).json()["suite_id"]

    new_cases = [
        {"id": "c1", "question": "改过的q", "question_type": "factoid",
         "expected_answer": "改过的a", "key_points": ["要点1"],
         "source": {"doc": "d"}, "difficulty": "hard"},
        {"id": "c2", "question": "新增题", "question_type": "factoid",
         "expected_answer": "a2", "source": {"doc": "d"}},
    ]
    r = cl.put(f"/api/v1/suites/{sid}", json={"cases": new_cases})
    assert r.status_code == 200
    body = r.json()
    assert len(body["cases"]) == 2
    assert body["cases"][0]["question"] == "改过的q"
    assert body["cases"][0]["difficulty"] == "hard"
    assert body["cases"][1]["id"] == "c2"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_eval_api.py::test_put_suite_replaces_cases -v`
Expected: FAIL（无 PUT 路由 → 405）。

- [ ] **Step 3: 加入参模型 + PUT 路由**

在 `app.py` 顶部模型区（其它 `class XxxIn(BaseModel)` 附近）加：

```python
class UpdateSuiteIn(BaseModel):
    cases: list[TestCase]
    name: str | None = None
```

在 `get_suite`（约 709-714 行）之后加：

```python
    @app.put("/api/v1/suites/{sid}")
    def update_suite(sid: str, body: UpdateSuiteIn):
        suite = store.get_suite(sid)
        if suite is None:
            raise HTTPException(404, "suite not found")
        if not body.cases:
            raise HTTPException(400, "测试集至少保留一题")
        suite.cases = body.cases
        if body.name is not None:
            suite.name = body.name.strip()
        store.save_suite(suite)
        return _suite_dict(suite)
```

确认 `TestCase` 已在 `app.py` import（顶部应已从 `..shared.models` 导入；若无则补）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_eval_api.py::test_put_suite_replaces_cases -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/app.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(suites): PUT /suites/{sid} 整体替换用例（编辑/增删）"
```

---

## Task 5: 删除项目（级联）store 方法

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/sqlite_store.py`（`save_project` 附近加 `delete_project`）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_delete_project_cascade(tmp_path):
    from runtime_eval.eval_api.config import ApiConfig
    from runtime_eval.eval_api.sqlite_store import SqliteStore
    from runtime_eval.shared.models import (
        Project, QuestionType, SourceRef, TestCase, TestSuite,
    )

    store = SqliteStore(ApiConfig(workspace_dir=tmp_path))
    store.save_project(Project(project_id="p1", name="P"))
    store.save_suite(TestSuite(
        suite_id="s1", project_id="p1",
        cases=[TestCase(id="c1", question="q", question_type=QuestionType.FACTOID,
                        expected_answer="a", source=SourceRef(doc="d"))],
    ))
    assert store.get_project("p1") is not None
    assert len(store.list_suites("p1")) == 1

    store.delete_project("p1")
    assert store.get_project("p1") is None
    assert store.list_suites("p1") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_eval_api.py::test_delete_project_cascade -v`
Expected: FAIL（无 `delete_project`）。

- [ ] **Step 3: 加级联删除方法**

在 `sqlite_store.py` `get_project`（约 184-186 行）之后加：

```python
    def delete_project(self, project_id: str) -> bool:
        """删项目并级联清掉其下全部数据（所有表都带 project_id 列）。"""
        tables = [
            "documents", "suites", "responses", "runs",
            "retrieval_sets", "retrieval_runs", "gold",
            "run_summaries", "projects",
        ]
        with self._lock:
            existed = self._conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone() is not None
            for t in tables:
                self._conn.execute(
                    f"DELETE FROM {t} WHERE project_id = ?", (project_id,)
                )
            self._conn.commit()
        return existed
```

> 说明：`responses` / `retrieval_sets` 主键是 suite_id 但都有 `project_id` 列（见 `_SCHEMA`），按 project_id 删即可，无需先查 suite。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_eval_api.py::test_delete_project_cascade -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/sqlite_store.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(store): delete_project 级联清空项目下全部数据"
```

---

## Task 6: DELETE /projects/{pid} 接口 + 报告目录清理

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_api/app.py:586-588`（`list_projects` 之后加 DELETE）
- Test: `runtime_eval/tests/test_eval_api.py`

- [ ] **Step 1: 写失败测试**

```python
def test_delete_project_endpoint(tmp_path):
    from fastapi.testclient import TestClient
    from runtime_eval.eval_api.app import create_app
    from runtime_eval.eval_api.config import ApiConfig

    cl = TestClient(create_app(config=ApiConfig(workspace_dir=tmp_path)))
    pid = cl.post("/api/v1/projects", json={"name": "P"}).json()["project_id"]
    assert cl.delete(f"/api/v1/projects/{pid}").status_code == 200
    assert all(p["project_id"] != pid for p in cl.get("/api/v1/projects").json())
    assert cl.delete(f"/api/v1/projects/{pid}").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_eval_api.py::test_delete_project_endpoint -v`
Expected: FAIL（无 DELETE 路由）。

- [ ] **Step 3: 加 DELETE 路由**

在 `app.py` `list_projects`（约 586-588 行）之后加：

```python
    @app.delete("/api/v1/projects/{pid}")
    def delete_project(pid: str):
        if store.get_project(pid) is None:
            raise HTTPException(404, "project not found")
        store.delete_project(pid)
        shutil.rmtree(store.project_dir(pid), ignore_errors=True)
        return {"deleted": pid}
```

确认 `app.py` 顶部 import 有 `shutil`；若无则在 import 区加 `import shutil`。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `python -m pytest tests/test_eval_api.py::test_delete_project_endpoint -v`
Expected: PASS

Run: `python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_api/app.py runtime_eval/tests/test_eval_api.py
git commit -m "feat(projects): DELETE /projects/{pid} 二次确认级联删除（后端）"
```

---

## Task 7: 前端 — 4 步流程重定义 + 步骤条重做 + 去纯文字提示

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js:342-352`（`renderWork` 去掉纯文字 hint 行）
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js:448-526`（`FLOW_STEPS`/`flowDefaultStep`/`flowReady`/`flowDone`/`renderFlowStepper`/`renderFlowStep`）

无自动化测试（vanilla JS）；用「起服务 + 浏览器肉眼核对」验证（见 Task 10）。

- [ ] **Step 1: 改 `renderWork` 头部，删掉那行会被误当步骤条的纯文字提示**

把 `app.js:342-352` 的模板里这段：

```js
      <div class="card-h"><h3>评估工作台 · ${esc(state.projectName || "")}</h3>
        <span class="hint">准备题目 → 标准答案 → 取证据 → 评分与报告</span></div>
```

改为（提示换成与新 4 步一致、且明确这是说明不是步骤条）：

```js
      <div class="card-h"><h3>评估工作台 · ${esc(state.projectName || "")}</h3>
        <span class="hint">导入测试集 → 题库题目 → 检索取证 → 评分报告</span></div>
```

- [ ] **Step 2: 重定义 `FLOW_STEPS`**

把 `app.js:448-453` 改为：

```js
const FLOW_STEPS = [
  { key: "import", t: "导入测试集", d: "AI 出题 / 拉真实日志 / 导入题目（可命名）" },
  { key: "overview", t: "题库题目", d: "查看并编辑本题库全部题目（改/增/删）" },
  { key: "evidence", t: "检索取证", d: "收被测系统答卷（serving 取证 / Claude 作答）" },
  { key: "report", t: "评分报告", d: "逐题打分 → 成绩单 + 增量价值(L4) + 报告历史" },
];
```

- [ ] **Step 3: 调整 `flowDefaultStep` 与软门禁（去掉 gold 步）**

把 `app.js:486-501` 的 `flowDefaultStep`/`flowReady`/`flowDone` 改为（步序：0 导入 / 1 题目总览 / 2 取证 / 3 报告）：

```js
function flowDefaultStep(f) {
  return ({ documents: 0, questions: 1, gold: 1, evidence: 2, report: 3 })[f.currentStep] ?? 0;
}
function flowReady(f, i) {
  if (!f) return i === 0;
  if (i === 0) return true;
  if (i === 1) return !!f.steps.questions;                      // 有题才好看总览/编辑
  if (i === 2) return !!f.steps.questions;                      // 有题即可取证（已无黄金门禁）
  if (i === 3) return !!(f.steps.questions && f.steps.evidence); // 取到证据才好打分
  return false;
}
function flowDone(f, i) {
  if (!f) return false;
  return [f.steps.questions, f.steps.questions, f.steps.evidence, f.steps.report][i];
}
```

- [ ] **Step 4: `renderFlowStep` 派发到新步骤函数**

把 `app.js:524-527` 改为：

```js
function renderFlowStep() {
  const fns = [workStepImport, workStepOverview, workStepEvidence, workStepReport];
  (fns[state.flowStep] || workStepImport)();
}
```

> `workStepImport` = 原 `workStepDocGen` 重命名（Task 7 Step 5）；`workStepOverview` = 新增（Task 8）；`workStepEvidence`/`workStepReport` 复用现有。

- [ ] **Step 5: `workStepDocGen` 重命名为 `workStepImport`，并修内部「下一步」文案 + 导入 tab 文案**

把 `app.js:528` 的 `function workStepDocGen() {` 改名为 `function workStepImport() {`；其内：
- `setDocSource('import')` 那个 tab 文案 `"导入题库"` → `"导入测试集"`；
- 底部按钮 `onclick="flowGoStep(1)">下一步：标准答案 →` → `onclick="flowGoStep(1)">下一步：题库题目 →`；
- 提示 `出题 / 拉取 / 导入后即可校对黄金` → `出题 / 拉取 / 导入后即可查看与编辑题目`。

同时 `setDocSource`（约 545-549 行）里调用的 `workStepDocGen()` 改成 `workStepImport()`。

- [ ] **Step 6: 起服务肉眼核对步骤条**（见 Task 10 完整流程；此处先确认 4 个卡片渲染成横向步骤条而非纯文字）

- [ ] **Step 7: Commit**

```bash
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "feat(web): 评估工作台改 4 步（导入/题目/取证/报告）+ 步骤条文案统一"
```

---

## Task 8: 前端 — 第②步「题库题目总览 + 编辑」

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（新增 `workStepOverview` 等函数；可放在原 `workStepGold` 位置附近，并删除 `workStepGold`/黄金相关 UI 函数引用）
- Modify: `runtime_eval/runtime_eval/eval_web/static/styles.css`（编辑表样式）

- [ ] **Step 1: 删除 `workStepGold`（黄金 UI 退役），新增 `workStepOverview`**

删掉 `app.js:555-581` 的整个 `workStepGold` 函数，替换为下面两个函数。`workStepOverview` 拉本项目所有测试集，逐集列题，提供「编辑」进入编辑态：

```js
async function workStepOverview() {
  const box = $("#flowBody"); if (!box) return;
  const pid = state.projectId;
  box.innerHTML = `<div class="card"><div class="card-h">
      <h3>题库题目总览</h3><span class="hint">本题库（项目）下所有测试集的题目，可改 / 增 / 删</span>
      <span class="spacer"></span>
      <button class="btn sm ghost" onclick="workStepOverview()">刷新</button>
      <button class="btn sm" onclick="flowGoStep(0)">＋ 导入/出题</button>
    </div><div id="overviewBody"><div class="muted small">加载中…</div></div></div>`;
  try {
    const suites = await api(`/api/v1/projects/${pid}/suites`);
    const body = $("#overviewBody"); if (!body) return;
    if (!suites.length) { body.innerHTML = `<div class="empty"><div class="h">题库还没有题目</div><div class="muted">先去第①步导入或出题。</div></div>`; return; }
    const full = await Promise.all(suites.map((s) => api(`/api/v1/suites/${s.suite_id}`)));
    body.innerHTML = full.map(renderSuiteCard).join("");
  } catch (e) {
    const body = $("#overviewBody"); if (body) body.innerHTML = `<div class="muted small" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}

function suiteLabel(s) { return s.name || (s.corpus_files || []).join(",") || s.backend || s.suite_id; }

function renderSuiteCard(suite) {
  const editing = state.editSuiteId === suite.suite_id;
  const head = `<div class="ov-suite-h">
      <b>${esc(suiteLabel(suite))}</b>
      <span class="badge muted">${suite.cases.length} 题</span>
      <span class="spacer"></span>
      ${editing
        ? `<button class="btn sm" onclick="overviewAddCase('${suite.suite_id}')">＋ 加题</button>
           <button class="btn sm" onclick="overviewSaveEdit('${suite.suite_id}')">保存</button>
           <button class="btn sm ghost" onclick="overviewCancelEdit()">取消</button>`
        : `<button class="btn sm ghost" onclick="overviewStartEdit('${suite.suite_id}')">编辑</button>`}
    </div>`;
  const rows = (editing ? state.editCases : suite.cases).map((c, i) => editing
    ? overviewEditRow(c, i)
    : `<tr><td>${esc(c.question)}</td><td>${esc(c.expected_answer)}</td><td>${esc((c.key_points || []).join(" / "))}</td><td>${esc(c.difficulty || "normal")}</td></tr>`
  ).join("");
  return `<div class="ov-suite" data-sid="${suite.suite_id}">${head}
    <table class="tbl ov-tbl"><thead><tr><th>问题</th><th>标准答案</th><th>要点</th><th>难度</th>${editing ? "<th></th>" : ""}</tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function overviewEditRow(c, i) {
  const diffOpts = ["easy", "normal", "hard"].map((d) =>
    `<option value="${d}"${(c.difficulty || "normal") === d ? " selected" : ""}>${d}</option>`).join("");
  return `<tr data-i="${i}">
    <td><textarea class="ov-in" data-f="question" rows="2">${esc(c.question)}</textarea></td>
    <td><textarea class="ov-in" data-f="expected_answer" rows="2">${esc(c.expected_answer)}</textarea></td>
    <td><textarea class="ov-in" data-f="key_points" rows="2">${esc((c.key_points || []).join("\\n"))}</textarea></td>
    <td><select class="ov-in" data-f="difficulty">${diffOpts}</select></td>
    <td><button class="btn sm ghost" onclick="overviewDelCase(${i})">删</button></td>
  </tr>`;
}
```

- [ ] **Step 2: 编辑态状态机函数（增/删/保存/取消）**

紧接其后加。编辑时把当前 suite 的 cases 深拷进 `state.editCases`，DOM 输入先回写再 PUT：

```js
function overviewStartEdit(sid) {
  const card = document.querySelector(`.ov-suite[data-sid="${sid}"]`);
  // 从已加载的总览重新拉一份完整 suite，保证 cases 全字段
  api(`/api/v1/suites/${sid}`).then((suite) => {
    state.editSuiteId = sid;
    state.editCases = JSON.parse(JSON.stringify(suite.cases));
    workStepOverview();
  }).catch((e) => toast("打开编辑失败：" + e.message, "err"));
}
function overviewCancelEdit() { state.editSuiteId = null; state.editCases = null; workStepOverview(); }

function overviewSyncFromDom() {
  // 把当前编辑表里每行输入回写到 state.editCases
  document.querySelectorAll(".ov-suite[data-sid] tbody tr[data-i]").forEach((tr) => {
    const i = +tr.getAttribute("data-i");
    const c = state.editCases[i]; if (!c) return;
    tr.querySelectorAll(".ov-in").forEach((el) => {
      const f = el.getAttribute("data-f");
      if (f === "key_points") c.key_points = el.value.split("\n").map((x) => x.trim()).filter(Boolean);
      else c[f] = el.value;
    });
  });
}
function overviewAddCase(sid) {
  overviewSyncFromDom();
  const n = state.editCases.length + 1;
  state.editCases.push({
    id: `c_new_${Date.now()}_${n}`, question: "", question_type: "factoid",
    expected_answer: "", key_points: [], expected_evidence: [], expected_entities: [],
    source: { doc: "" }, difficulty: "normal",
  });
  workStepOverview();
}
function overviewDelCase(i) {
  overviewSyncFromDom();
  state.editCases.splice(i, 1);
  workStepOverview();
}
async function overviewSaveEdit(sid) {
  overviewSyncFromDom();
  if (!state.editCases.length) { toast("至少保留一题", "err"); return; }
  try {
    await api(`/api/v1/suites/${sid}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cases: state.editCases }),
    });
    toast("已保存");
    state.editSuiteId = null; state.editCases = null;
    if (state.suiteId === sid) await selectSuite(sid); // 刷新当前选中集缓存
    workStepOverview();
  } catch (e) { toast("保存失败：" + e.message, "err"); }
}
```

- [ ] **Step 3: 加编辑表样式**

在 `styles.css` 步骤条区块后（约 345 行后）加：

```css
/* 题库题目总览 / 编辑 */
.ov-suite { margin-bottom: 18px; }
.ov-suite-h { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.ov-tbl td { vertical-align: top; }
.ov-in { width: 100%; box-sizing: border-box; font: inherit; padding: 6px 8px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); color: var(--ink); resize: vertical; }
.ov-in:focus { border-color: var(--accent); outline: none; }
```

- [ ] **Step 4: 清掉对已删黄金函数的悬空引用**

`grep` 确认没有残留调用导致运行时报错：

Run: 用 Grep 工具查 `workStepGold|goldBody|renderGoldCases|loadGoldProgress|loadGoldLib|confirmSuiteGold|goldConfirmAllBtn` 在 `app.js` 的引用。
处理：`workStepGold` 已删；若 `renderFlowStep` 已不再引用它（Task 7 Step 4 已改）即可。其余 `loadGoldLib` 等函数定义可保留（不被调用即死代码，但为最小改动先留着，避免连带破坏黄金管理页若仍挂在别处）。**只需确保新 4 步流程里无任何调用** —— 重点确认 `renderFlowStep` 数组、`workStepOverview` 内无黄金调用。

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_web/static/app.js runtime_eval/runtime_eval/eval_web/static/styles.css
git commit -m "feat(web): 第②步题库题目总览 + 逐题编辑/增删，退役黄金 UI"
```

---

## Task 9: 前端 — 导入命名弹框 + 项目删除按钮

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（导入提交处加 name；`renderProjectList` 加删除按钮 + `deleteProject`）

- [ ] **Step 1: 找到导入提交函数并加命名**

用 Grep 在 `app.js` 查 `suites:import` 的调用处（导入按钮的 handler，通常在 `docSourceImport` 相关函数里，用 `FormData` POST）。在 `FormData` 提交前用 `prompt` 取名字（默认＝文件名去扩展名），并 `fd.append("name", theName)`：

```js
// 在构造 FormData、append file 之后、fetch 之前：
const defaultName = (file.name || "测试集").replace(/\.[^.]+$/, "");
const suiteName = (window.prompt("给这个测试集起个名字（便于区分）", defaultName) || "").trim();
if (suiteName) fd.append("name", suiteName);
```

> 若导入用的是裸 `fetch` 而非 `api()`，保持原样只加 `fd.append`。`prompt` 返回 null（用户取消）时按默认名继续或中止——这里：空则后端回落到文件名，无需中止。

- [ ] **Step 2: 项目列表加删除按钮**

把 `app.js:316-329` 的 `renderProjectList` 里每项模板（约 321-328 行）改为带删除按钮，且按钮点击不冒泡触发 `openProject`：

```js
  box.innerHTML = ps.map((p) => {
    const active = p.project_id === state.projectId ? " active" : "";
    return `<div class="litem${active}">
      <div class="grow" onclick="openProject('${jsq(p.project_id)}')" style="cursor:pointer">
        <div class="nm">📁 ${esc(p.name)}</div>
        <div class="meta mono faint">${esc(p.project_id)}</div></div>
      <button class="btn sm" onclick="openProject('${jsq(p.project_id)}')">进入工作台 →</button>
      <button class="btn sm danger" onclick="deleteProject('${jsq(p.project_id)}', '${jsq(p.name)}')">删除</button>
    </div>`;
  }).join("");
```

- [ ] **Step 3: 加 `deleteProject`（二次确认）**

在 `createProject`（约 199-207 行）之后加：

```js
async function deleteProject(pid, name) {
  if (!window.confirm(`确定删除项目「${name}」？\n其下所有测试集、评估结果、报告将一并删除，且无法恢复。`)) return;
  try {
    await api(`/api/v1/projects/${pid}`, { method: "DELETE" });
    if (state.projectId === pid) {
      state.projectId = null; state.projectName = ""; localStorage.removeItem("kb_project");
      state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite");
      syncProjectUI();
    }
    await loadProjects();
    renderProjectList();
    toast("项目已删除：" + name);
  } catch (e) { toast("删除失败：" + e.message, "err"); }
}
```

- [ ] **Step 4: 加 `.btn.danger` 样式（若 styles.css 还没有）**

Grep `.btn.danger` 或 `.danger` 在 styles.css。若无，加：

```css
.btn.danger { color: var(--red); border-color: var(--red); }
.btn.danger:hover { background: var(--red); color: #fff; }
```

> 若 `--red` 变量不存在，用 `#e5484d`。先 Grep `--red` 确认。

- [ ] **Step 5: Commit**

```bash
git add runtime_eval/runtime_eval/eval_web/static/app.js runtime_eval/runtime_eval/eval_web/static/styles.css
git commit -m "feat(web): 导入测试集弹框命名 + 项目列表二次确认删除"
```

---

## Task 10: 整体起服务联调验证

**Files:** 无（验证任务）

- [ ] **Step 1: 后端全量回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿（含本次新增 6 个后端测试）。

- [ ] **Step 2: 起三服务**（按仓库现有启动方式；通常 eval-llm:8801 / eval-api 兜底起 SPA）

Run（参照 README / 现有脚本，示意）：
```
python -m runtime_eval.eval_llm   # 8801
python -m runtime_eval.eval_api   # 主服务，serves SPA
```
Expected: eval-api 起来，浏览器开首页不报 JS 控制台错误。

- [ ] **Step 3: 用 preview 工具肉眼核对（无 claude CLI 也能验的部分）**

逐项确认：
1. 进入「项目」页 → 每个项目有「删除」按钮；点删除弹二次确认；取消不删，确认后列表移除。
2. 进工作台 → 顶部是**横向 4 步步骤条**（序号圆圈+标题+描述，当前步高亮），不再是一行纯文字。4 步标题为：导入测试集 / 题库题目 / 检索取证 / 评分报告。
3. 第①步「导入测试集」tab 文案为「导入测试集」；选文件导入时弹出命名输入框。
4. 第②步「题库题目」列出本项目所有测试集的题，点「编辑」可改问题/答案/要点/难度，可「＋加题」「删」，「保存」后刷新生效。
5. 全程界面无「黄金」字样；控制台无 `workStepGold is not defined` 之类报错。

> 跑分/取证依赖真实 claude CLI + 在线 KB，无法在此环境端到端验证——如不可用，明确告知用户「这部分只做了静态核对，未端到端跑分」。

- [ ] **Step 4: 收尾 Commit（若联调中有微调）**

```bash
git add -A
git commit -m "fix(web): 工作台改造联调修复"
```

---

## Self-Review 对照 spec

- ✅ 概念统一：题库=项目、测试集=TestSuite（name 字段）、文案统一「导入测试集/题库题目」（Task 1/2/7）。
- ✅ 黄金轻量退役：门禁去除（Task 3）、前端黄金 UI 删除（Task 8）、后端表/接口保留（未动 gold 表/`/gold` 路由）。
- ✅ 删除项目：store 级联（Task 5）+ DELETE 接口（Task 6）+ 前端二次确认（Task 9）。
- ✅ 导入命名：后端 Form name（Task 2）+ 前端弹框（Task 9）。
- ✅ 测试集编辑：PUT 接口（Task 4）+ 第②步编辑 UI（Task 8）。
- ✅ 步骤条重做：FLOW_STEPS + renderFlowStepper 复用（Task 7）+ 去纯文字提示行 + 联调核对（Task 10）。

类型一致性核对：`UpdateSuiteIn.cases: list[TestCase]` 与前端 PUT body `{cases:[...]}` 字段一致；`name` 在 model / `_suite_dict` / `_suite_brief` / import / PUT 全程一致；前端 `suiteLabel` 回落顺序 name→corpus_files→backend。
