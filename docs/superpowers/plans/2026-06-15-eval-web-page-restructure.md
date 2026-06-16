# eval-web 页面拆分重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 eval-web 的「四步向导」与「测试集/黄金/报告」单页两套并行入口合并为「以项目为中心」的工作台，导航从 6 项收到 3 项，消灭重复与死链。

**Architecture:** 纯前端改造，不动 eval-api / eval-llm 任何端点。复用现有的 flow 向导骨架（`renderFlowStepper`/`flowRefresh`/`/state`），把四步压成三步（① 文档与出题、② 黄金校对、③ 评测与报告），把单页里更全的内容（导入/批量拉取、黄金库、报告历史）折进对应步骤。新增「项目列表页」和「工作台」两个路由，删掉旧的 flow/suites/gold/reports/retrieval 入口。

**Tech Stack:** 原生 JS（无构建、无模块）、hash 路由、localStorage 兜底；后端 FastAPI + pytest 回归。

**关于路由的工程取舍（与 spec 的差异，已确认合理）：** spec 写的是 `#/project/<pid>?step=` 带参数路由。实际代码的路由器只解析单 token（`location.hash.replace("#/","")`），且项目/测试集本来就用 localStorage 持久化（`kb_project`/`kb_suite`）。为贴合既有模式、降低风险，本计划改用单 token 路由 `#/work` + localStorage 记住当前步骤（`kb_workstep`）与评测子标签（`kb_evaltab`），刷新照样能恢复。导航是**左侧栏**（`index.html` 的 `#nav`），spec 文案写的"顶部导航"指的就是它。

**通用验证说明：** 本仓库前端无 JS 单测框架，"测试"= ①后端 pytest 回归保持全绿（重构不碰后端）②浏览器手动核对。每个任务末尾给出对应命令/核对项 + 提交。
- 后端回归命令（务必先 cd 进子项目，否则会拉进无关的坏 collection）：
  `cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q`
  期望：与改前相同的全绿数量（当前基线 76 passed）。
- 浏览器：本地起 eval-api 后打开其托管的 SPA（默认 http://localhost:8800/），硬刷新（Ctrl+F5）确保加载新 app.js。

---

## 文件结构

只动这三个静态文件（均在 `runtime_eval/runtime_eval/eval_web/static/`）：

- `app.js` — 路由表 `PAGES`、`setPage` 渲染分发、新增 `renderProjects`/`renderWork`、改造 flow 三步、删除旧页渲染器与重定向、改各处跳转。
- `index.html` — 左侧导航 `#nav` 由 6 项改 3 项；`<script>` 版本号 bump。
- `styles.css` — 新增评测子标签 `.tabs/.tab` 的少量样式（复用既有 `.stepper`）。

不新建文件。后端、模型、端点一律不动。

---

## Task 1: 路由表与导航收口（3 项），加 projects/work 占位

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（`PAGES` 约 11-19、`setPage` 约 156-164、`onProjectPick` 约 189-197）
- Modify: `runtime_eval/runtime_eval/eval_web/static/index.html`（`#nav` 约 21-47、`<script>` 约 74）

- [ ] **Step 1: 改 `PAGES`，只保留 4 个真实页（dashboard/projects/work/chat）**

把 `app.js` 第 11-19 行整段替换为：

```js
const PAGES = {
  dashboard: { title: "看板总览", sub: "项目整体进度与关键指标一览" },
  projects: { title: "项目", sub: "选择或新建项目，进入评估工作台" },
  work: { title: "评估工作台", sub: "文档与出题 → 黄金校对 → 评测与报告" },
  chat: { title: "对话试问", sub: "输入问题，预览知识库会检索到哪些片段" },
};
// 旧 hash 兼容：老链接/书签 → 新页面
const PAGE_ALIAS = { flow: "work", suites: "work", gold: "work", reports: "work", retrieval: "work" };
```

- [ ] **Step 2: 改 `setPage`，支持别名 + 新 renderers 分发**

把 `app.js` 第 156-164 行的 `setPage` 函数体替换为：

```js
function setPage(page) {
  page = PAGE_ALIAS[page] || page;
  if (!PAGES[page]) page = "dashboard";
  state.page = page;
  $("#pageTitle").textContent = PAGES[page].title;
  $("#pageSub").textContent = PAGES[page].sub;
  $$("#nav .navitem").forEach((n) => n.classList.toggle("active", n.dataset.page === page));
  const renderers = { dashboard: renderDashboard, projects: renderProjects, work: renderWork, chat: renderChat };
  (renderers[page] || renderDashboard)();
}
```

（`renderProjects`/`renderWork` 在 Task 2/3 才定义；本步先放占位，见 Step 4。）

- [ ] **Step 3: 改 `onProjectPick` 新建分支跳转到 projects 而非 suites**

把 `app.js` 第 190 行：

```js
  if (v === "__new__") { syncProjectUI(); go("suites"); setTimeout(() => $("#newProjName") && $("#newProjName").focus(), 60); return; }
```

替换为：

```js
  if (v === "__new__") { syncProjectUI(); go("projects"); setTimeout(() => $("#newProjName") && $("#newProjName").focus(), 60); return; }
```

- [ ] **Step 4: 加临时占位渲染器（让 app 先能跑起来，后续任务替换）**

在 `app.js` 第 297 行（`openSuiteFrom` 之前）插入临时占位（Task 2/3 会用真实实现替换）：

```js
async function renderProjects() { const v = $("#view"); v.className = "view wide"; v.innerHTML = '<div class="card"><div class="muted">项目列表（Task 2 实现）</div></div>'; }
async function renderWork() { const v = $("#view"); v.className = "view wide"; v.innerHTML = '<div class="card"><div class="muted">工作台（Task 3 实现）</div></div>'; }
```

- [ ] **Step 5: 改 `index.html` 左侧导航为 3 项**

把 `index.html` 第 26-41 行（flow / suites / gold / reports 四个 `navitem`）整段删除，替换为一个 projects 项 + 一个 work 项：

```html
      <div class="navitem" data-page="projects">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
        <span>项目</span>
      </div>
      <div class="navitem" data-page="work">
        <svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M13 6l6 6-6 6"/><circle cx="4" cy="12" r="1.6"/></svg>
        <span>评估工作台</span>
      </div>
```

结果：`看板总览 / 项目 / 评估工作台 / —分隔— / 对话试问`。

- [ ] **Step 6: bump `app.js` 版本号强制刷新**

把 `index.html` 第 74 行替换为：

```html
<script src="/static/app.js?v=20260615-restructure"></script>
```

- [ ] **Step 7: 浏览器验证**

硬刷新后核对：左侧只剩「看板总览 / 项目 / 评估工作台 / 对话试问」4 项；点「项目」「评估工作台」分别显示占位卡片；访问旧链接 `http://localhost:8800/#/flow`、`#/suites`、`#/gold`、`#/reports`、`#/retrieval` 都被导到工作台占位；看板、对话仍正常。

- [ ] **Step 8: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js runtime_eval/runtime_eval/eval_web/static/index.html
git commit -m "refactor(eval-web): 导航收口到 3 项 + projects/work 路由占位"
```

---

## Task 2: 项目列表页 `renderProjects`

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（替换 Task 1 的 `renderProjects` 占位；新增 `renderProjectList`/`openProject`）

- [ ] **Step 1: 用真实实现替换 `renderProjects` 占位**

把 Task 1 Step 4 里那行 `async function renderProjects() {...占位...}` 替换为：

```js
async function renderProjects() {
  const v = $("#view"); v.className = "view wide";
  v.innerHTML = `
    <div class="card tight">
      <div class="card-h"><h3>新建项目</h3><span class="hint">项目是文档、测试集与评估结果的容器</span></div>
      <div class="inline-fields">
        <div class="field grow"><input type="text" id="newProjName" placeholder="例如：客服知识库 v2" /></div>
        <button class="btn" id="newProjBtn">＋ 新建</button>
      </div>
    </div>
    <div class="card">
      <div class="card-h"><h3>全部项目</h3><span class="spacer"></span><span class="hint" id="projCount"></span></div>
      <div id="projList" class="list"><div class="muted small">加载中…</div></div>
    </div>`;
  bindNewProject();
  renderProjectList();
}
function renderProjectList() {
  const box = $("#projList"); if (!box) return;
  const ps = state.projects || [];
  const cnt = $("#projCount"); if (cnt) cnt.textContent = ps.length ? `${ps.length} 个` : "";
  if (!ps.length) { box.innerHTML = `<div class="muted small">还没有项目。用上面的输入框新建一个开始。</div>`; return; }
  box.innerHTML = ps.map((p) => {
    const active = p.project_id === state.projectId ? " active" : "";
    return `<div class="litem${active}" onclick="openProject('${jsq(p.project_id)}')">
      <div class="grow"><div class="nm">📁 ${esc(p.name)}</div>
        <div class="meta mono faint">${esc(p.project_id)}</div></div>
      <button class="btn sm">进入工作台 →</button>
    </div>`;
  }).join("");
}
function openProject(pid) {
  state.projectId = pid;
  localStorage.setItem("kb_project", pid);
  state.suiteId = null; state.suite = null; localStorage.removeItem("kb_suite");
  state.flowStep = null; state.flowStale = new Set();
  syncProjectUI();
  if (pid) { syncSuitesFromServer(pid, false); syncRunsFromServer(pid, false); }
  go("work");
}
```

说明：`jsq`（约 1125 行）已存在，用于安全内联字符串；`bindNewProject`（约 311 行）已存在；`openProject` 与既有 `onProjectPick` 逻辑一致但末尾固定跳 `work`。

- [ ] **Step 2: 浏览器验证**

硬刷新 → 点「项目」：列出全部项目，顶部可新建；新建后出现在列表且自动选中；点任一项目卡 → 顶栏项目选择器切到它并跳到工作台（占位）。新建项目走 `createProject`（约 198 行）已有逻辑。

- [ ] **Step 3: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "feat(eval-web): 项目列表页（卡片 + 新建 + 进入工作台）"
```

---

## Task 3: 工作台外壳 + 三步骨架（复用 flow 机制）

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（替换 Task 1 的 `renderWork` 占位；改 `FLOW_STEPS`/`flowDefaultStep`/解锁逻辑/`flowGoStep`/`renderFlowStep`；新增 `state.evalTab`）

- [ ] **Step 1: state 增加 evalTab 字段**

把 `app.js` 第 32 行：

```js
  flowStale: new Set(),    // D4：上游改动后下游"需重跑"的步骤索引
```

替换为（仅多加 evalTab 一行，工作台当前步仍复用既有的 `state.flowStep`）：

```js
  flowStale: new Set(),    // D4：上游改动后下游"需重跑"的步骤索引
  evalTab: localStorage.getItem("kb_evaltab") || "evidence",  // 评测步的子标签
```

- [ ] **Step 2: `FLOW_STEPS` 从 4 步改 3 步**

把 `app.js` 第 387-392 行替换为：

```js
const FLOW_STEPS = [
  { key: "docgen", t: "文档与出题", d: "上传文档 + AI 出题 / 导入 / 批量拉取" },
  { key: "gold", t: "黄金校对", d: "复核 / 修正 / 确认黄金答案" },
  { key: "eval", t: "评测与报告", d: "取证据 → 逐题打分 → 报告与历史" },
];
```

- [ ] **Step 3: 用 `renderWork` 替换 flow 外壳**

把 Task 1 Step 4 里的 `async function renderWork() {...占位...}` 替换为（同时**删除**旧的 `renderFlow`，约 394-409 行）：

```js
async function renderWork() {
  const v = $("#view"); v.className = "view wide";
  if (!state.projectId) { v.innerHTML = noProjectBlock(); bindNewProject(); return; }
  v.innerHTML = `
    <div class="card tight">
      <div class="card-h"><h3>评估工作台 · ${esc(state.projectName || "")}</h3>
        <span class="hint">文档与出题 → 黄金校对 → 评测与报告</span></div>
      <div class="inline-fields">
        <div class="field grow"><span class="lbl">当前测试集（出题后自动选中；可切换/留空开新）</span>${suitePickerHtml()}</div>
        <span id="flowStateLine" class="muted small"></span>
      </div>
    </div>
    <div id="flowStepper"></div>
    <div id="flowBody"></div>`;
  await flowRefresh();
}
```

（`renderWork` 复用了原 `renderFlow` 的 DOM 结构 `#flowStepper`/`#flowBody`/`#flowStateLine`，所以 `flowRefresh`/`renderFlowStepper` 无需大改。）

- [ ] **Step 4: 默认步映射改为 3 索引**

把 `app.js` 第 442-444 行 `flowDefaultStep` 替换为：

```js
function flowDefaultStep(f) {
  return ({ documents: 0, questions: 0, gold: 1, evidence: 2, report: 2 })[f.currentStep] ?? 0;
}
```

- [ ] **Step 5: 解锁逻辑改 3 步 + 软门禁（重命名 flowUnlocked → flowReady）**

把 `app.js` 第 445-459 行（`flowUnlocked`/`flowDone`/`flowMarkStale`/`flowGoStep`）整段替换为：

```js
// 软门禁：flowReady 仅用于"提示/徽章"，导航不再硬锁（spec 要求可随便点回）
function flowReady(f, i) {
  if (!f) return i === 0;
  if (i === 0) return true;
  if (i === 1) return !!f.steps.questions;                  // 出题后才好校对
  if (i === 2) return !!(f.steps.questions && f.steps.gold); // 有题且至少一条确认黄金
  return false;
}
function flowDone(f, i) {
  if (!f) return false;
  return [f.steps.questions, f.steps.gold, f.steps.report][i];
}
function flowMarkStale(fromStep) { for (let i = fromStep + 1; i < FLOW_STEPS.length; i++) state.flowStale.add(i); }
function flowGoStep(i) { state.flowStep = i; renderFlowStepper(); renderFlowStep(); }
```

- [ ] **Step 6: 修正 `flowRefresh` 里对 flowUnlocked 的调用**

把 `app.js` 第 433 行：

```js
  if (state.flowStep == null || !flowUnlocked(f, state.flowStep)) state.flowStep = flowDefaultStep(f);
```

替换为（步索引越界也回落）：

```js
  if (state.flowStep == null || state.flowStep > 2) state.flowStep = flowDefaultStep(f);
```

- [ ] **Step 7: `renderFlowStepper` 用 flowReady + 软门禁样式**

把 `app.js` 第 461-476 行 `renderFlowStepper` 替换为：

```js
function renderFlowStepper() {
  const box = $("#flowStepper"); if (!box) return;
  const f = state.flow;
  box.innerHTML = `<div class="stepper">` + FLOW_STEPS.map((s, i) => {
    const ready = flowReady(f, i);
    const done = flowDone(f, i);
    const active = i === state.flowStep;
    const stale = state.flowStale.has(i);
    const cls = ["stepper-item", active ? "active" : "", done ? "done" : ""].filter(Boolean).join(" ");
    const badge = stale ? `<span class="badge warn">需重跑</span>`
      : done ? `<span class="badge ok">✓</span>`
      : !ready ? `<span class="badge muted">待就绪</span>` : "";
    return `<div class="${cls}" onclick="flowGoStep(${i})">
        <div class="stepper-num">${i + 1}</div>
        <div class="stepper-txt"><div class="t">${s.t} ${badge}</div><div class="d">${s.d}</div></div>
      </div>${i < FLOW_STEPS.length - 1 ? `<div class="stepper-arrow">→</div>` : ""}`;
  }).join("") + `</div>`;
}
```

（去掉 `locked` 类，软门禁只用「待就绪」徽章提示，仍可点。）

- [ ] **Step 8: `renderFlowStep` 分发到 3 个步函数**

把 `app.js` 第 478-481 行 `renderFlowStep` 替换为：

```js
function renderFlowStep() {
  const fns = [workStepDocGen, workStepGold, workStepEval];
  (fns[state.flowStep] || workStepDocGen)();
}
```

（`workStepDocGen`/`workStepGold`/`workStepEval` 在 Task 4/5/6 实现；本步先放最小占位，见 Step 9。）

- [ ] **Step 9: 加三步占位（让 app 跑起来）**

在 `app.js` 第 481 行（`renderFlowStep` 之后）插入：

```js
function workStepDocGen() { const b = $("#flowBody"); if (b) b.innerHTML = '<div class="card"><div class="muted">① 文档与出题（Task 4）</div></div>'; }
function workStepGold() { const b = $("#flowBody"); if (b) b.innerHTML = '<div class="card"><div class="muted">② 黄金校对（Task 5）</div></div>'; }
function workStepEval() { const b = $("#flowBody"); if (b) b.innerHTML = '<div class="card"><div class="muted">③ 评测与报告（Task 6）</div></div>'; }
```

- [ ] **Step 10: 删除旧的 4 步函数体**

删除 `app.js` 旧的 `flowStepDocuments`（约 483-497）、`flowStepGold`（约 511-542）——它们的内容会在 Task 4/5 以新形态重建。**保留** `flowStepEvidence`（约 611-636）和 `flowStepReport`（约 639-658）以及它们依赖的 `flowUploadDoc`/`flowGenerate`/`flowPreviewPrompt`/`showPromptModal`/`closePromptModal`/`flowConfirmAll`（约 498-608），Task 6/5 会复用。先删 `flowStepDocuments`/`flowStepGold` 两个函数即可。

- [ ] **Step 11: 浏览器验证**

硬刷新 → 选中一个有测试集的项目 → 工作台显示 3 步步骤条（文档与出题 / 黄金校对 / 评测与报告），点哪步下面换对应占位卡；未出题的项目第②③步显示「待就绪」徽章但仍可点；顶栏测试集下拉可切换。无 JS 报错（看控制台）。

- [ ] **Step 12: 后端回归 + 提交**

```bash
cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q
```
期望：76 passed（与基线一致）。然后：
```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "feat(eval-web): 工作台外壳 + 三步骨架（软门禁，复用 /state）"
```

---

## Task 4: 步骤① 文档与出题（复用 suitesBody）

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（替换 `workStepDocGen` 占位）

- [ ] **Step 1: 用真实实现替换 `workStepDocGen`**

`suitesBody`（约 679-724）已经产出「上传文档 / AI 出题 / 导入 / 批量拉取 / 测试集列表 / 用例预览」整块，且其按钮（`uploadBtn`/`genBtn`/`importBtn`/`pullBtn`）已在底部事件委托（约 1417-1426）里绑定。直接复用：

把 Task 3 Step 9 的 `workStepDocGen` 占位替换为：

```js
function workStepDocGen() {
  const box = $("#flowBody"); if (!box) return;
  box.innerHTML = suitesBody() + `
    <div class="row-actions" style="margin-top:4px">
      <button class="btn ghost" onclick="flowGoStep(1)">下一步：黄金校对 →</button>
      <span class="muted small">${flowReady(state.flow, 1) ? "" : "出题或导入后即可校对黄金"}</span>
    </div>`;
  renderTypeChecks(); loadDocs(); renderSuiteList();
}
```

- [ ] **Step 2: 让"出题成功"后刷新步骤条状态**

`generateSuite`（约 804-817）当前在 suites 页用；工作台里出题后需要刷新步骤条/状态行。把第 812-814 行：

```js
    registerSuite(suite, "AI出题"); renderSuiteList();
    toast(`已生成 ${suite.cases.length} 道题（自带黄金）`);
    previewSuite(suite.suite_id);
```

替换为：

```js
    registerSuite(suite, "AI出题");
    await selectSuite(suite.suite_id); syncProjectUI();
    toast(`已生成 ${suite.cases.length} 道题（自带黄金草稿，请到「黄金校对」确认）`);
    if (state.page === "work") { state.flowStep = 1; state.flowStale = new Set(); await flowRefresh(true); }
    else { renderSuiteList(); previewSuite(suite.suite_id); }
```

（保持在 suites 页时旧行为不变；在工作台则选中新测试集、跳到黄金校对步并刷新。）

- [ ] **Step 3: 浏览器验证**

工作台第①步：上传 .md/.txt/.chm 文档 → 列表出现；勾文档+题型，点「生成测试集」→ 出题成功后自动跳到第②步且顶栏测试集已选中；「导入」「批量拉取」按钮可用；测试集列表/预览正常。

- [ ] **Step 4: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "feat(eval-web): 工作台①文档与出题（复用 suitesBody，出题后进②）"
```

---

## Task 5: 步骤② 黄金校对（复用 goldBody + 黄金库）

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（替换 `workStepGold` 占位）

- [ ] **Step 1: 用真实实现替换 `workStepGold`**

复用 `goldBody`（约 874-887，逐题校对 + 批量评估按钮）和黄金库表格（`loadGoldLib`/`toggleGoldNewForm`/`confirmSuiteGold` 等已存在）。把 Task 3 Step 9 的 `workStepGold` 占位替换为：

```js
function workStepGold() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在「文档与出题」生成或选择一个测试集。"); return; }
  box.innerHTML = `
    ${goldBody()}
    <div class="card">
      <div class="card-h"><h3>黄金集库</h3><span class="hint">按问题指纹存储，可跨测试集复用 · 只有「已确认」参与打分</span>
        <span class="spacer"></span>
        <select id="goldFilter" class="sel sm" style="width:130px">
          <option value="">全部状态</option><option value="draft">仅草稿</option><option value="confirmed">仅已确认</option>
        </select>
        <button class="btn sm" id="goldConfirmAllBtn">确认本测试集草稿</button>
        <button class="btn sm" id="goldNewBtn">＋ 新建</button>
        <button class="btn sm ghost" id="goldLibBtn">刷新</button></div>
      <div id="goldNewForm" style="display:none"></div>
      <div id="goldLib"><div class="muted small">加载中…</div></div>
    </div>
    <div class="row-actions" style="margin-top:4px">
      <button class="btn ghost" onclick="flowGoStep(2)">下一步：评测与报告 →</button>
      <span class="muted small">${flowReady(state.flow, 2) ? "" : "至少确认一条黄金后再评测"}</span>
    </div>`;
  $("#goldLibBtn").onclick = loadGoldLib;
  $("#goldFilter").onchange = loadGoldLib;
  $("#goldNewBtn").onclick = toggleGoldNewForm;
  const caBtn = $("#goldConfirmAllBtn"); if (caBtn) caBtn.onclick = confirmSuiteGold;
  renderGoldCases(); loadGoldProgress(); loadGoldLib();
}
```

说明：`goldBody` 里的「批量评估（仅已标注）」按钮 id 为 `batchEvalBtn`，已在事件委托里绑到 `batchEvaluateAnnotated`；`#goldProg` 元素在 `goldBody` 外（原 renderGold 顶部），故本步把进度显示挪到 `loadGoldProgress` 写的 `#goldProg`——`goldBody` 内没有该元素时 `loadGoldProgress`（约 933-939）已做空判断（`if (el)`），安全。

- [ ] **Step 2: `confirmSuiteGold`/`saveGold` 后刷新步骤条**

把 `confirmSuiteGold`（约 1069-1075）末尾：

```js
    toast(`已确认 ${r.confirmed} 条草稿（共 ${r.total} 题）`); loadGoldLib(); loadGoldProgress();
```

替换为：

```js
    toast(`已确认 ${r.confirmed} 条草稿（共 ${r.total} 题）`); loadGoldLib(); loadGoldProgress();
    if (state.page === "work") flowRefresh(true);
```

- [ ] **Step 3: 浏览器验证**

工作台第②步：逐题校对/保存黄金；「确认本测试集草稿」后步骤条第③步从「待就绪」变可评测；黄金库表格可筛选/新建/编辑/删除/确认；点「下一步」进第③步。

- [ ] **Step 4: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "feat(eval-web): 工作台②黄金校对（复用 goldBody + 黄金库）"
```

---

## Task 6: 步骤③ 评测与报告（子标签：取证据 / 打分报告 / 报告历史）

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（替换 `workStepEval` 占位；给 `flowStepEvidence`/`flowStepReport` 加目标容器参数；新增报告历史复用函数）
- Modify: `runtime_eval/runtime_eval/eval_web/static/styles.css`（新增 `.tabs/.tab`）

- [ ] **Step 1: 给 `flowStepEvidence` / `flowStepReport` 加可选目标容器**

把 `flowStepEvidence`（约 611-636）首行：

```js
function flowStepEvidence() {
  const box = $("#flowBody"); if (!box) return;
```

替换为：

```js
function flowStepEvidence(targetSel) {
  const box = $(targetSel || "#flowBody"); if (!box) return;
```

并把该函数体内第 630 行的「下一步」按钮：

```js
        <button class="btn ghost" onclick="flowGoStep(3)" ${flowUnlocked(state.flow, 3) ? "" : "disabled"}>下一步：评估报告 →</button>
        <span class="muted small">${flowUnlocked(state.flow, 3) ? "" : "需至少为一题取到证据"}</span>
```

替换为（指向子标签而非已不存在的第 4 步）：

```js
        <button class="btn ghost" onclick="workEvalTab('judge')">去打分与报告 →</button>
```

同理把 `flowStepReport`（约 639-658）首行：

```js
function flowStepReport() {
  const box = $("#flowBody"); if (!box) return;
```

替换为：

```js
function flowStepReport(targetSel) {
  const box = $(targetSel || "#flowBody"); if (!box) return;
```

- [ ] **Step 2: 新增报告历史复用函数（从 renderReports 抽列表渲染）**

在 `app.js` 第 378 行（`renderReports` 之后）新增一个可复用的「把报告列表渲染进指定容器」函数：

```js
async function renderReportListInto(targetSel) {
  const box = $(targetSel); if (!box) return;
  const pid = state.projectId;
  box.innerHTML = `<div class="card"><div id="reportList"><div class="muted small">加载中…</div></div></div>`;
  try {
    const rows = await api(`/api/v1/projects/${pid}/runs`);
    const labels = {}; regSuites(pid).forEach((s) => { labels[s.suite_id] = s.label; });
    const list = $("#reportList"); if (!list) return;
    if (!rows.length) { list.innerHTML = `<div class="empty"><div class="h">还没有评估报告</div><div class="muted">在「取证据 / 打分与报告」走完一次评估，这里就会出现历史档案。</div></div>`; return; }
    list.innerHTML = `<div class="list">` + rows.map((r) => {
      const when = (r.created_at || "").replace("T", " ").slice(0, 16);
      const label = labels[r.suite_id] || r.suite_id || "—";
      const statusBadge = r.status === "done" ? `<span class="badge ok">完成</span>`
        : r.status === "partial" ? `<span class="badge muted">进行中</span>`
        : `<span class="badge muted">${esc(r.status || "")}</span>`;
      return `<div class="litem">
        <div class="grow">
          <div class="nm">${esc(label)} <span class="badge muted">${RUN_LAYER_LABEL[r.layer] || r.layer}</span> <span class="badge muted">${RUN_KIND_LABEL[r.kind] || r.kind}</span></div>
          <div class="meta">${runScoreLine(r)}</div>
          <div class="src">${when}</div>
        </div>
        ${statusBadge}
        <a class="btn sm" href="${reportUrl(r, "html")}" target="_blank">↗ HTML</a>
        <a class="btn sm ghost" href="${reportUrl(r, "md")}" target="_blank">MD</a>
      </div>`;
    }).join("") + `</div>`;
  } catch (e) {
    const list = $("#reportList"); if (list) list.innerHTML = `<div class="muted small" style="color:var(--red)">加载失败：${esc(e.message)}</div>`;
  }
}
```

（沿用既有 `RUN_LAYER_LABEL`/`RUN_KIND_LABEL`/`runScoreLine`/`reportUrl`，约 322-343。）

- [ ] **Step 3: 用真实实现替换 `workStepEval`**

把 Task 3 Step 9 的 `workStepEval` 占位替换为：

```js
function workStepEval() {
  const box = $("#flowBody"); if (!box) return;
  if (!state.suite) { box.innerHTML = pickHint("请先在「文档与出题」生成测试集。"); return; }
  const tab = state.evalTab || "evidence";
  box.innerHTML = `
    <div class="card tight"><div class="tabs">
      <button class="tab ${tab === "evidence" ? "active" : ""}" onclick="workEvalTab('evidence')">取证据</button>
      <button class="tab ${tab === "judge" ? "active" : ""}" onclick="workEvalTab('judge')">打分与报告</button>
      <button class="tab ${tab === "history" ? "active" : ""}" onclick="workEvalTab('history')">报告历史</button>
    </div></div>
    <div id="evalTabBody"></div>`;
  renderEvalTab();
}
function workEvalTab(t) { state.evalTab = t; localStorage.setItem("kb_evaltab", t); renderEvalTab(); }
function renderEvalTab() {
  const tab = state.evalTab || "evidence";
  if (tab === "judge") flowStepReport("#evalTabBody");
  else if (tab === "history") renderReportListInto("#evalTabBody");
  else flowStepEvidence("#evalTabBody");
}
```

- [ ] **Step 4: 新增 `.tabs/.tab` 样式**

在 `styles.css` 第 344 行（`.stepper-arrow` 那行之后）追加：

```css
.tabs { display: flex; gap: 6px; }
.tab { padding: 8px 16px; border: 1px solid var(--line); background: var(--surface); color: var(--muted); border-radius: 9px; cursor: pointer; font-size: 13px; font-weight: 600; }
.tab:hover { border-color: var(--accent); }
.tab.active { background: var(--accent-soft); border-color: var(--accent); color: var(--ink); box-shadow: 0 0 0 1px var(--accent) inset; }
```

- [ ] **Step 5: 浏览器验证**

工作台第③步出现三个子标签：
- 「取证据」：检索器名/domain、批量取证（serving SSE）、Claude 自动取证、逐题列表都在；点「去打分与报告」切到 judge 标签。
- 「打分与报告」：评估标准卡、全部评估、生成最终报告、逐题结果都在。
- 「报告历史」：列出本项目历次 run，可开 HTML/MD。
切标签后刷新页面（Ctrl+F5）应记住上次所在子标签（localStorage）。

- [ ] **Step 6: 后端回归 + 提交**

```bash
cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q
```
期望 76 passed。然后：
```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js runtime_eval/runtime_eval/eval_web/static/styles.css
git commit -m "feat(eval-web): 工作台③评测与报告（取证据/打分报告/报告历史 子标签）"
```

---

## Task 7: 收口跳转 + 删除死代码

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（看板/测试集列表/预览页里的跳转改向 work；删 `renderRetrieval` 重定向、旧 `renderReports`/`renderSuites`/`renderGold` 渲染器、事件委托里失效项；清理 `onSuitePick` 的 flow 分支）

- [ ] **Step 1: 看板跳转改向 work**

`renderDashboard`（约 242-297）里：
- 第 259 行 `onclick="go('suites')"` → `onclick="go('work')"`（按钮文案「去管理 →」改为「去工作台 →」）
- 第 263 行 `onclick="go('reports')"` → `onclick="openHistory()"`（按钮文案「全部报告 →」保留），新增下面的 `openHistory`
- 第 273 行 `openSuiteFrom('${s.suite_id}','gold')` → `openSuiteWork('${s.suite_id}', 1)`
- 第 283 行文案 `在「评估流程」跑一次。` → `在「评估工作台」跑一次。`

新增 `openSuiteWork` 和 `openHistory`（在 `openSuiteFrom` 约 298 行旁）：

```js
async function openSuiteWork(sid, step) {
  if (await selectSuite(sid)) { state.flowStep = (step == null ? 0 : step); state.flowStale = new Set(); go("work"); }
}
// 看板「全部报告 →」：进工作台第③步并切到报告历史子标签
function openHistory() { state.flowStep = 2; state.evalTab = "history"; localStorage.setItem("kb_evaltab", "history"); go("work"); }
```

- [ ] **Step 2: 测试集列表/预览页的「标注/评估」按钮改向 work**

`renderSuiteList`（约 743-761）里第 756-757 行：

```js
      <button class="btn sm ghost" onclick="openSuiteFrom('${s.suite_id}','gold')">标注</button>
      <button class="btn sm ghost" onclick="openSuiteFrom('${s.suite_id}','retrieval')">评估</button>
```

替换为：

```js
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 1)">校对黄金</button>
      <button class="btn sm ghost" onclick="openSuiteWork('${s.suite_id}', 2)">评测</button>
```

`previewSuite`（约 764-786）里第 772-773 行：

```js
      <button class="btn sm" onclick="go('gold')">去标注黄金 →</button>
      <button class="btn sm" onclick="go('retrieval')">去检索评估 →</button>
```

替换为：

```js
      <button class="btn sm" onclick="flowGoStep(1)">去校对黄金 →</button>
      <button class="btn sm" onclick="flowGoStep(2)">去评测 →</button>
```

- [ ] **Step 3: `openSuiteFrom` 收尾改向 work（兼容残留调用）**

把 `openSuiteFrom`（约 298 行）：

```js
async function openSuiteFrom(sid, page) { if (await selectSuite(sid)) go(page); }
```

替换为（旧 gold/retrieval/reports 一律进工作台对应步）：

```js
async function openSuiteFrom(sid, page) {
  const stepMap = { gold: 1, retrieval: 2, reports: 2 };
  if (await selectSuite(sid)) { state.flowStep = stepMap[page] ?? 0; state.flowStale = new Set(); go("work"); }
}
```

- [ ] **Step 4: 清理 `onSuitePick` 的 flow 分支**

把 `onSuitePick`（约 231-237）替换为：

```js
async function onSuitePick(sid) {
  if (state.page === "work") {
    state.flowStep = null; state.flowStale = new Set();
    await selectSuite(sid); setPage("work"); return;
  }
  if (await selectSuite(sid)) setPage(state.page);
}
```

- [ ] **Step 5: 删除已下线的整页渲染器**

删除以下不再被 `setPage` 引用的函数（已被工作台取代）：
- `renderReports`（约 344-378）—— 注意**保留** `RUN_KIND_LABEL`/`RUN_LAYER_LABEL`/`reportUrl`/`runScoreLine`（约 322-343），它们被 Task 6 的 `renderReportListInto` 复用。
- `renderSuites`（约 663-677）—— **保留** `suitesBody`/`renderTypeChecks`/`loadDocs`/`renderSuiteList`/`previewSuite` 等，它们被工作台①复用。
- `renderGold`（约 845-873）—— **保留** `goldBody`/`renderGoldCases`/`loadGoldLib` 等。
- `renderRetrieval`（约 1082，`async function renderRetrieval() { go("flow"); }`）整行删除。

- [ ] **Step 6: 浏览器验证（全链路）**

硬刷新，跑一遍：项目 → 新建/进入 → ①上传+出题 → ②校对+确认黄金 → ③取证据→打分→报告→报告历史；看板上的「去管理/全部报告/最近测试集」点击都进工作台正确步；旧 hash（`#/flow #/suites #/gold #/reports #/retrieval`）都进工作台；控制台无 `is not defined` 报错。

- [ ] **Step 7: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "refactor(eval-web): 跳转统一进工作台 + 删除旧整页渲染器与死链"
```

---

## Task 8: 全量回归 + 注释/文案收尾

**Files:**
- Modify: `runtime_eval/runtime_eval/eval_web/static/app.js`（顶部注释、boot 兜底）

- [ ] **Step 1: 更新文件顶部注释**

把 `app.js` 第 3-7 行注释里：

```js
   左侧导航 + 多页面：看板 / 测试集 / 黄金 / 检索评估 / 对话
```

替换为：

```js
   左侧导航：看板 / 项目 / 评估工作台（文档与出题→黄金校对→评测与报告）/ 对话
```

- [ ] **Step 2: boot 默认页兜底确认**

确认 `boot`（约 1432-1436）对未知 hash 回落 dashboard 即可（`setPage` 已处理别名 + 回落，无需改）。打开 `http://localhost:8800/`（无 hash）应进看板。

- [ ] **Step 3: 后端全量回归**

```bash
cd /e/MyProjects/KnowledgeBase/runtime_eval && python -m pytest -q
```
期望：76 passed（与基线一致，未引入后端回归）。

- [ ] **Step 4: 浏览器冒烟核对清单**

- 导航只有 4 项：看板总览 / 项目 / 评估工作台 / 对话试问。
- 无「评估流程」「测试集管理」「黄金标注」「评估报告」「检索评估」旧入口与死链。
- 工作台三步内容与旧页一致、空间不缩水；第③步三个子标签可切换。
- 步骤条软门禁：未就绪步显示「待就绪」但仍可点。
- 刷新后按 localStorage 恢复到当前项目/步骤/子标签。
- 对话试问、看板照常。

- [ ] **Step 5: 提交**

```bash
cd /e/MyProjects/KnowledgeBase
git add runtime_eval/runtime_eval/eval_web/static/app.js
git commit -m "chore(eval-web): 注释/文案收尾，重构完成"
```

---

## 自查（Spec 覆盖核对）

- 导航 6→3：Task 1（nav）+ Task 7（删旧渲染器）✓（实际呈现为 4 个 navitem：看板/项目/工作台/对话，"3 个核心 + 对话工具"与 spec 一致）。
- 项目列表 + 新建：Task 2 ✓
- 工作台 = 项目上下文 + 步骤条 + 整页内容：Task 3（外壳/步骤条）✓；项目上下文复用既有顶栏 `projSelect` + 侧栏 `projMini`（已持久显示当前项目），未另起上下文条——比 spec 更省，效果一致。
- ① 文档与出题（复用 renderSuites 内容）：Task 4 ✓
- ② 黄金校对（复用 renderGold 内容）：Task 5 ✓
- ③ 评测与报告（flow③④ + reports，子标签）：Task 6 ✓
- 复用 `/state` 解锁逻辑：Task 3（flowRefresh/flowReady）✓
- 删除 flow 外壳 / retrieval 死链 / 旧导航项：Task 3 Step 10 + Task 7 ✓
- 路由：`#projects` / `#work`（+ 旧 hash 别名）✓（用单 token + localStorage 替代 spec 的 querystring，理由见开头取舍说明）。
- 错误/边界：进入 work 无项目 → `noProjectBlock`（Task 3 Step 3）；未就绪软提示（Task 3 Step 7）；报告历史空态（Task 6 Step 2）；切项目清步骤（openProject）✓
- 后端不动、pytest 全绿：Task 3/6/8 ✓
- 非目标（不改视觉框架/不引构建/不改后端）：全程遵守 ✓
```
